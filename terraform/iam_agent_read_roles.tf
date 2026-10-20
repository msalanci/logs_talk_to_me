# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# iam_agent_read_roles.tf — the read roles the agent role assumes in dev and prod, one per service it reads there
# through the AWS API: Health, Service Quotas, GuardDuty, Macie and Inspector. Each trusts only the agent role and
# allows only its service's read actions. The LTTM*ReadRole names must match CROSS_ACCOUNT_ROLE_TEMPLATE in
# agents/tools/.

data "aws_iam_policy_document" "health_cross_account_dev_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "health_cross_account_dev_policy" {
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
}

resource "aws_iam_role" "health_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMHealthReadRole"
  assume_role_policy = data.aws_iam_policy_document.health_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "health_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-health-read"
  role     = aws_iam_role.health_cross_account_dev.id
  policy   = data.aws_iam_policy_document.health_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "health_cross_account_prod_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "health_cross_account_prod_policy" {
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
}

resource "aws_iam_role" "health_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMHealthReadRole"
  assume_role_policy = data.aws_iam_policy_document.health_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "health_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-health-read"
  role     = aws_iam_role.health_cross_account_prod.id
  policy   = data.aws_iam_policy_document.health_cross_account_prod_policy.json
}

data "aws_iam_policy_document" "quotas_cross_account_dev_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "quotas_cross_account_dev_policy" {
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
}

resource "aws_iam_role" "quotas_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMQuotasReadRole"
  assume_role_policy = data.aws_iam_policy_document.quotas_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "quotas_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-quotas-read"
  role     = aws_iam_role.quotas_cross_account_dev.id
  policy   = data.aws_iam_policy_document.quotas_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "quotas_cross_account_prod_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "quotas_cross_account_prod_policy" {
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
}

resource "aws_iam_role" "quotas_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMQuotasReadRole"
  assume_role_policy = data.aws_iam_policy_document.quotas_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "quotas_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-quotas-read"
  role     = aws_iam_role.quotas_cross_account_prod.id
  policy   = data.aws_iam_policy_document.quotas_cross_account_prod_policy.json
}

data "aws_iam_policy_document" "gd_cross_account_dev_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "gd_cross_account_dev_policy" {
  statement {
    sid    = "GuardDutyRead"
    effect = "Allow"
    actions = [
      "guardduty:ListDetectors",
      "guardduty:ListFindings",
      "guardduty:GetFindings",
      "guardduty:GetDetector",
      "guardduty:DescribeMalwareScans",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "gd_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMGuardDutyReadRole"
  assume_role_policy = data.aws_iam_policy_document.gd_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "gd_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-guardduty-read"
  role     = aws_iam_role.gd_cross_account_dev.id
  policy   = data.aws_iam_policy_document.gd_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "gd_cross_account_prod_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "gd_cross_account_prod_policy" {
  statement {
    sid    = "GuardDutyRead"
    effect = "Allow"
    actions = [
      "guardduty:ListDetectors",
      "guardduty:ListFindings",
      "guardduty:GetFindings",
      "guardduty:GetDetector",
      "guardduty:DescribeMalwareScans",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "gd_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMGuardDutyReadRole"
  assume_role_policy = data.aws_iam_policy_document.gd_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "gd_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-guardduty-read"
  role     = aws_iam_role.gd_cross_account_prod.id
  policy   = data.aws_iam_policy_document.gd_cross_account_prod_policy.json
}

data "aws_iam_policy_document" "macie_cross_account_dev_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "macie_cross_account_dev_policy" {
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
}

resource "aws_iam_role" "macie_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMMacieReadRole"
  assume_role_policy = data.aws_iam_policy_document.macie_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "macie_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-macie-read"
  role     = aws_iam_role.macie_cross_account_dev.id
  policy   = data.aws_iam_policy_document.macie_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "macie_cross_account_prod_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "macie_cross_account_prod_policy" {
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
}

resource "aws_iam_role" "macie_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMMacieReadRole"
  assume_role_policy = data.aws_iam_policy_document.macie_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "macie_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-macie-read"
  role     = aws_iam_role.macie_cross_account_prod.id
  policy   = data.aws_iam_policy_document.macie_cross_account_prod_policy.json
}

data "aws_iam_policy_document" "inspector_cross_account_dev_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "inspector_cross_account_dev_policy" {
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
}

resource "aws_iam_role" "inspector_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMInspectorReadRole"
  assume_role_policy = data.aws_iam_policy_document.inspector_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "inspector_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-inspector-read"
  role     = aws_iam_role.inspector_cross_account_dev.id
  policy   = data.aws_iam_policy_document.inspector_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "inspector_cross_account_prod_trust" {
  statement {
    sid     = "AllowAgentRoleAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "inspector_cross_account_prod_policy" {
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
}

resource "aws_iam_role" "inspector_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMInspectorReadRole"
  assume_role_policy = data.aws_iam_policy_document.inspector_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "inspector_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-inspector-read"
  role     = aws_iam_role.inspector_cross_account_prod.id
  policy   = data.aws_iam_policy_document.inspector_cross_account_prod_policy.json
}
