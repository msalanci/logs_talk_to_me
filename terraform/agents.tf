# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

# =============================================================================
# agents.tf — Agent IAM role and AgentCore Memory for LTTM v3
# =============================================================================


data "aws_iam_policy_document" "agent_trust" {
  provider = aws.default_uswest2

  statement {
    sid     = "AllowAgentCoreAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

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

  tags = {
    Project = var.prefix
  }
}

data "aws_iam_policy_document" "agent_permissions" {
  provider = aws.default_uswest2

  statement {
    sid     = "BedrockInvokeModel"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0",
      "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0",
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:us-west-2:960319001022:inference-profile/us.anthropic.claude-sonnet-4-20250514-v1:0",
      "arn:aws:bedrock:us-west-2:960319001022:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ]
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

  statement {
    sid     = "ConfigSnapshotS3Read"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/config-snapshot/*",
    ]
  }

  statement {
    sid       = "AthenaResultsS3List"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.prefix}"]
  }

  statement {
    sid       = "AthenaResultsS3Location"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.prefix}"]
  }

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
    sid     = "AccessAnalyzerCrossAccountAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.dev_account_id}:role/LTTMAccessAnalyzerReadRole",
      "arn:aws:iam::${var.prod_account_id}:role/LTTMAccessAnalyzerReadRole",
    ]
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

  tags = { Project = var.prefix }
}

resource "aws_bedrockagentcore_memory_strategy" "semantic" {
  provider    = aws.default_uswest2
  name        = "semantic_strategy"
  memory_id   = aws_bedrockagentcore_memory.lttm.id
  type        = "SEMANTIC"
  description = "Extracts facts and knowledge across LTTM sessions"
  namespaces  = ["default"]
}

resource "aws_bedrockagentcore_memory_strategy" "summary" {
  provider    = aws.default_uswest2
  name        = "summary_strategy"
  memory_id   = aws_bedrockagentcore_memory.lttm.id
  type        = "SUMMARIZATION"
  description = "Summarizes LTTM conversation history to keep context compact"
  namespaces  = ["{sessionId}"]
}

resource "aws_bedrockagentcore_memory_strategy" "episodic" {
  provider    = aws.default_uswest2
  name        = "episodic_strategy"
  memory_id   = aws_bedrockagentcore_memory.lttm.id
  type        = "EPISODIC"
  description = "Captures session experiences and generates reflections for LTTM"
  namespaces  = ["{sessionId}"]
}
