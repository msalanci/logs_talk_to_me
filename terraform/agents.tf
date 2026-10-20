# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# agents.tf — the AgentCore execution role (lttm-agent-role) with its permissions, and AgentCore Memory with its
# three strategies (semantic, summarization, episodic).
# The runtime itself is not managed by Terraform: the agentcore CLI deploys it, with this role and this memory.

data "aws_iam_policy_document" "agent_trust" {
  provider = aws.default_uswest2

  statement {
    sid     = "AllowAgentCoreAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    # bedrock-agentcore.amazonaws.com, not bedrock.amazonaws.com: with the wrong principal,
    # CreateAgentRuntime fails with "Role validation failed".
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = ["${var.main_account_id}"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${var.agentcore_region}:${var.main_account_id}:*"]
    }
  }
}

resource "aws_iam_role" "agent" {
  provider           = aws.default_uswest2
  name               = "lttm-agent-role"
  assume_role_policy = data.aws_iam_policy_document.agent_trust.json
}

data "aws_iam_policy_document" "agent_permissions" {
  provider = aws.default_uswest2

  # A "us." inference profile needs both its own ARN and the foundation-model ARN in every Region it routes
  # to (hence the "*"); the foundation model's ID is the profile's without its geography prefix. A bare model
  # ID is refused for on-demand use with a ValidationException.
  statement {
    sid     = "BedrockInvokeModel"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = flatten([
      for profile in var.agent_models : [
        "arn:aws:bedrock:*::foundation-model/${replace(profile, "/^[a-z]+\\./", "")}",
        "arn:aws:bedrock:${var.agentcore_region}:${var.main_account_id}:inference-profile/${profile}",
      ]
    ])
  }

  statement {
    sid    = "AthenaQueryExecution"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [
      "arn:aws:athena:${var.project_region}:${var.main_account_id}:workgroup/lttm-athena-workgroup",
    ]
  }

  statement {
    sid    = "GlueSchemaRead"
    effect = "Allow"
    actions = [
      "glue:GetTable",
      "glue:GetDatabase",
      "glue:GetPartitions",
    ]
    resources = [
      "arn:aws:glue:${var.project_region}:${var.main_account_id}:catalog",
      "arn:aws:glue:${var.project_region}:${var.main_account_id}:database/lttm_logs",
      "arn:aws:glue:${var.project_region}:${var.main_account_id}:table/lttm_logs/*",
    ]
  }

  statement {
    sid    = "AthenaResultsS3"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
    ]
    resources = [
      "arn:aws:s3:::${var.prefix}/athena-results/*",
    ]
  }

  # Not covered by Lake Formation, which gates only the Glue catalog, nor by the bucket policy, which leaves
  # config-snapshot/ out. Without it, the config_snapshot table answers PERMISSION_DENIED.
  statement {
    sid     = "ConfigSnapshotS3Read"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/config-snapshot/*",
    ]
  }

  # No s3:prefix condition: Athena lists the bucket without a prefix while it plans a query, and a
  # condition denies that call.
  statement {
    sid       = "AthenaResultsS3List"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.prefix}"]
  }

  # Without it, Athena answers "cannot verify or create the output bucket", even with PutObject and ListBucket.
  statement {
    sid       = "AthenaResultsS3Location"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.prefix}"]
  }

  # The runtime fetches its code from the agentcore CLI's bucket at start. Without it, Python never starts:
  # a 30 s initialization timeout, with no log line at all.
  statement {
    sid    = "AgentCodeS3Read"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [
      "arn:aws:s3:::bedrock-agentcore-codebuild-sources-${var.main_account_id}-${var.agentcore_region}/*",
    ]
  }

  statement {
    sid    = "CloudWatchLogsGroupOps"
    effect = "Allow"
    actions = [
      "logs:DescribeLogStreams",
      "logs:CreateLogGroup",
    ]
    resources = [
      "arn:aws:logs:${var.agentcore_region}:${var.main_account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
    ]
  }

  # log-group:*, not the runtime prefix: the runtime looks its log group up first, and without this it
  # silently drops all its logs.
  statement {
    sid    = "CloudWatchLogsDescribeGroups"
    effect = "Allow"
    actions = [
      "logs:DescribeLogGroups",
    ]
    resources = [
      "arn:aws:logs:${var.agentcore_region}:${var.main_account_id}:log-group:*",
    ]
  }

  statement {
    sid    = "CloudWatchLogsStreamWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.agentcore_region}:${var.main_account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
    ]
  }

  statement {
    sid    = "XRayTracing"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  statement {
    sid    = "AccessAnalyzerRead"
    effect = "Allow"
    actions = [
      "access-analyzer:ListAnalyzers",
      "access-analyzer:ListFindings",
      "access-analyzer:ListFindingsV2",
      "access-analyzer:GetFinding",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "HealthRead"
    effect = "Allow"
    actions = [
      "health:DescribeEvents",
      "health:DescribeEventDetails",
      "health:DescribeAffectedEntities",
      "health:DescribeEventTypes",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "HealthCrossAccountAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.dev_account_id}:role/LTTMHealthReadRole",
      "arn:aws:iam::${var.prod_account_id}:role/LTTMHealthReadRole",
    ]
  }

  statement {
    sid    = "ServiceQuotasRead"
    effect = "Allow"
    actions = [
      "servicequotas:ListServices",
      "servicequotas:ListServiceQuotas",
      "servicequotas:GetServiceQuota",
      "servicequotas:ListRequestedServiceQuotaChangeHistory",
      "servicequotas:GetAWSDefaultServiceQuotaValue",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "QuotasCrossAccountAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.dev_account_id}:role/LTTMQuotasReadRole",
      "arn:aws:iam::${var.prod_account_id}:role/LTTMQuotasReadRole",
    ]
  }

  statement {
    sid    = "GuardDutyRead"
    effect = "Allow"
    actions = [
      "guardduty:ListDetectors",
      "guardduty:ListFindings",
      "guardduty:GetFindings",
      "guardduty:GetDetector",
      "guardduty:ListMembers",
      "guardduty:DescribeMalwareScans",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "GuardDutyCrossAccountAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.dev_account_id}:role/LTTMGuardDutyReadRole",
      "arn:aws:iam::${var.prod_account_id}:role/LTTMGuardDutyReadRole",
    ]
  }

  statement {
    sid    = "MacieRead"
    effect = "Allow"
    actions = [
      "macie2:ListFindings",
      "macie2:GetFindings",
      "macie2:DescribeBuckets",
      "macie2:ListResourceProfiles",
      "macie2:GetResourceProfile",
      "macie2:GetSensitiveDataOccurrences",
      "macie2:GetSensitiveDataOccurrencesAvailability",
      "macie2:GetMacieSession",
      "macie2:ListClassificationJobs",
      "macie2:DescribeClassificationJob",
      "macie2:SearchResources",
      "macie2:GetBucketStatistics",
      "macie2:GetFindingStatistics",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "MacieCrossAccountAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.dev_account_id}:role/LTTMMacieReadRole",
      "arn:aws:iam::${var.prod_account_id}:role/LTTMMacieReadRole",
    ]
  }

  statement {
    sid    = "InspectorRead"
    effect = "Allow"
    actions = [
      "inspector2:ListFindings",
      "inspector2:ListCoverage",
      "inspector2:BatchGetFindingDetails",
      "inspector2:GetFindingsReportStatus",
      "inspector2:ListAccountPermissions",
      "inspector2:ListFindingAggregations",
      "inspector2:GetMember",
      "inspector2:ListMembers",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "InspectorCrossAccountAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.dev_account_id}:role/LTTMInspectorReadRole",
      "arn:aws:iam::${var.prod_account_id}:role/LTTMInspectorReadRole",
    ]
  }

  statement {
    sid    = "OrganizationsRead"
    effect = "Allow"
    actions = [
      "organizations:DescribeOrganization",
      "organizations:DescribeOrganizationalUnit",
      "organizations:DescribePolicy",
      "organizations:ListAccounts",
      "organizations:ListAccountsForParent",
      "organizations:ListOrganizationalUnitsForParent",
      "organizations:ListPolicies",
      "organizations:ListPoliciesForTarget",
      "organizations:ListRoots",
      "organizations:ListParents",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ControlTowerRead"
    effect = "Allow"
    actions = [
      "controltower:ListLandingZones",
      "controltower:GetHomeRegion",
      "controltower:GetLandingZone",
      "controltower:DescribeLandingZoneConfiguration",
      "controltower:GetAvailableUpdates",
      "controltower:GetLandingZoneDriftStatus",
      "controltower:GetLandingZoneStatus",
      "controltower:ListLandingZoneOperations",
      "controltower:GetLandingZoneOperation",
      "controltower:ListEnabledControls",
      "controltower:ListGuardrailsForTarget",
      "controltower:GetEnabledControl",
      "controltower:DescribeGuardrailForTarget",
      "controltower:ListControlOperations",
      "controltower:GetControlOperation",
      "controltower:ListEnabledBaselines",
      "controltower:ListManagedAccounts",
      "controltower:ListManagedOrganizationalUnits",
      "controltower:ListBaselines",
      "controltower:GetBaselineOperation",
      "controltower:DescribeRegisterOrganizationalUnitOperation",
      "controltower:DescribeGuardrail", # controlcatalog:GetControl checks it too
      "controlcatalog:GetControl",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AgentCoreMemory"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetMemory",
      "bedrock-agentcore:InvokeMemory",
      "bedrock-agentcore:SearchMemory",
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:GetEvent",
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:DeleteEvent",
      "bedrock-agentcore:RetrieveMemoryRecords",
      "bedrock-agentcore:ListMemoryRecords",
      "bedrock-agentcore:GetMemoryRecord",
      "bedrock-agentcore:DeleteMemoryRecord",
      "bedrock-agentcore:BatchCreateMemoryRecords",
      "bedrock-agentcore:BatchDeleteMemoryRecords",
      "bedrock-agentcore:BatchUpdateMemoryRecords",
      "bedrock-agentcore:ListActors",
      "bedrock-agentcore:ListSessions",
      "bedrock-agentcore:StartMemoryExtractionJob",
      "bedrock-agentcore:ListMemoryExtractionJobs",
    ]
    resources = [aws_bedrockagentcore_memory.lttm.arn]
  }

  statement {
    sid       = "CloudTrailDigestPublicKeys"
    effect    = "Allow"
    actions   = ["cloudtrail:ListPublicKeys"]
    resources = ["*"]
  }

  # Used only by the supervisor's S3 session fallback, when the runtime has no memory ID.
  statement {
    sid    = "S3SessionManagerFallback"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
    ]
    resources = [
      "arn:aws:s3:::${var.prefix}/sessions/*",
    ]
  }

  statement {
    sid       = "BedrockApplyGuardrail"
    effect    = "Allow"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.lttm.guardrail_arn]
  }
}

resource "aws_iam_role_policy" "agent" {
  provider = aws.default_uswest2
  name     = "lttm-agent-permissions"
  role     = aws_iam_role.agent.id
  policy   = data.aws_iam_policy_document.agent_permissions.json
}

resource "aws_bedrockagentcore_memory" "lttm" {
  provider              = aws.default_uswest2
  name                  = "${replace(var.prefix, "-", "_")}_agent_memory"
  description           = "LTTM conversation memory — stores session history for follow-up questions"
  event_expiry_duration = var.memory_retention_days

}

resource "aws_bedrockagentcore_memory_strategy" "semantic" {
  provider            = aws.default_uswest2
  name                = "semantic_strategy"
  memory_id           = aws_bedrockagentcore_memory.lttm.id
  type                = "SEMANTIC"
  description         = "Extracts facts and knowledge across LTTM sessions"
  namespace_templates = ["default"]
}

resource "aws_bedrockagentcore_memory_strategy" "summary" {
  provider            = aws.default_uswest2
  name                = "summary_strategy"
  memory_id           = aws_bedrockagentcore_memory.lttm.id
  type                = "SUMMARIZATION"
  description         = "Summarizes LTTM conversation history to keep context compact"
  namespace_templates = ["{sessionId}"]
}

resource "aws_bedrockagentcore_memory_strategy" "episodic" {
  provider            = aws.default_uswest2
  name                = "episodic_strategy"
  memory_id           = aws_bedrockagentcore_memory.lttm.id
  type                = "EPISODIC"
  description         = "Captures session experiences and generates reflections for LTTM"
  namespace_templates = ["/strategy/{memoryStrategyId}/actor/{actorId}/session/{sessionId}/"]

  # AWS requires the reflections' template to be the episodes' template, only less nested.
  reflection_configuration {
    namespace_templates = ["/strategy/{memoryStrategyId}/actor/{actorId}/"]
  }
}
