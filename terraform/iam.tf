# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# iam.tf — Defines all IAM roles and inline policies for the LTTM data pipeline.
# =============================================================================


# lttm-firehose-main - Execution role for the main-account Kinesis Firehose delivery stream
data "aws_iam_policy_document" "firehose_main_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "firehose_main_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/cloudwatch/*",
      "arn:aws:s3:::${var.prefix}/cloudwatch-errors/*",
    ]
  }
}

resource "aws_iam_role" "firehose_main" {
  name               = "lttm-firehose-main"
  assume_role_policy = data.aws_iam_policy_document.firehose_main_trust.json
}

resource "aws_iam_role_policy" "firehose_main" {
  name   = "lttm-firehose-main-policy"
  role   = aws_iam_role.firehose_main.id
  policy = data.aws_iam_policy_document.firehose_main_policy.json
}


# lttm-firehose-cross-account-dev - A role in the MAIN account that the DEV-account Firehose assumes
data "aws_iam_policy_document" "firehose_cross_account_dev_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.dev_account_id]
    }
  }
}

data "aws_iam_policy_document" "firehose_cross_account_dev_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/cloudwatch/*",
      "arn:aws:s3:::${var.prefix}/cloudwatch-errors/*",
    ]
  }
}

resource "aws_iam_role" "firehose_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-firehose-cross-account-dev"
  assume_role_policy = data.aws_iam_policy_document.firehose_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "firehose_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-firehose-cross-account-dev-policy"
  role     = aws_iam_role.firehose_cross_account_dev.id
  policy   = data.aws_iam_policy_document.firehose_cross_account_dev_policy.json
}


# lttm-firehose-cross-account-prod - A role in the MAIN account that the PROD-account Firehose assumes
data "aws_iam_policy_document" "firehose_cross_account_prod_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.prod_account_id]
    }
  }
}

data "aws_iam_policy_document" "firehose_cross_account_prod_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/cloudwatch/*",
      "arn:aws:s3:::${var.prefix}/cloudwatch-errors/*",
    ]
  }
}

resource "aws_iam_role" "firehose_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-firehose-cross-account-prod"
  assume_role_policy = data.aws_iam_policy_document.firehose_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "firehose_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-firehose-cross-account-prod-policy"
  role     = aws_iam_role.firehose_cross_account_prod.id
  policy   = data.aws_iam_policy_document.firehose_cross_account_prod_policy.json
}


# lttm-cwl-to-firehose-main - Allow the CloudWatch to push logvrecords INTO the Kinesis Firehose delivery streams in main account
data "aws_iam_policy_document" "cwl_to_firehose_main_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_main_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.main_account_id}:deliverystream/lttm-firehose-main"]
  }
}

resource "aws_iam_role" "cwl_to_firehose_main" {
  name               = "lttm-cwl-to-firehose-main"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_main_trust.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_main" {
  name   = "lttm-cwl-to-firehose-main-policy"
  role   = aws_iam_role.cwl_to_firehose_main.id
  policy = data.aws_iam_policy_document.cwl_to_firehose_main_policy.json
}


# lttm-cwl-to-firehose-dev - Allow the CloudWatch to push logvrecords INTO the Kinesis Firehose delivery streams in dev account
data "aws_iam_policy_document" "cwl_to_firehose_dev_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.dev_account_id]
    }
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_dev_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.dev_account_id}:deliverystream/lttm-firehose-dev"]
  }
}

resource "aws_iam_role" "cwl_to_firehose_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-cwl-to-firehose-dev"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_dev_trust.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-cwl-to-firehose-dev-policy"
  role     = aws_iam_role.cwl_to_firehose_dev.id
  policy   = data.aws_iam_policy_document.cwl_to_firehose_dev_policy.json
}


# lttm-cwl-to-firehose-prod - Allow the CloudWatch to push logvrecords INTO the Kinesis Firehose delivery streams in prod account
data "aws_iam_policy_document" "cwl_to_firehose_prod_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.prod_account_id]
    }
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_prod_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.prod_account_id}:deliverystream/lttm-firehose-prod"]
  }
}

resource "aws_iam_role" "cwl_to_firehose_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-cwl-to-firehose-prod"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_prod_trust.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-cwl-to-firehose-prod-policy"
  role     = aws_iam_role.cwl_to_firehose_prod.id
  policy   = data.aws_iam_policy_document.cwl_to_firehose_prod_policy.json
}


# lttm-firehose-main-uswest2 - Firehose execution role for main account in eu-west-2
data "aws_iam_policy_document" "firehose_main_uswest2_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "firehose_main_uswest2_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/cloudwatch/*",
      "arn:aws:s3:::${var.prefix}/cloudwatch-errors/*",
    ]
  }
}

resource "aws_iam_role" "firehose_main_uswest2" {
  provider           = aws.default_uswest2
  name               = "lttm-firehose-main-uswest2"
  assume_role_policy = data.aws_iam_policy_document.firehose_main_uswest2_trust.json
}

resource "aws_iam_role_policy" "firehose_main_uswest2" {
  provider = aws.default_uswest2
  name     = "lttm-firehose-main-uswest2-policy"
  role     = aws_iam_role.firehose_main_uswest2.id
  policy   = data.aws_iam_policy_document.firehose_main_uswest2_policy.json
}


# lttm-cwl-to-firehose-main-uswest2 - CloudWatch → Firehose role for main account in eu-west-2
data "aws_iam_policy_document" "cwl_to_firehose_main_uswest2_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_main_uswest2_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.agentcore_region}:${var.main_account_id}:deliverystream/lttm-firehose-main-uswest2"]
  }
}

resource "aws_iam_role" "cwl_to_firehose_main_uswest2" {
  provider           = aws.default_uswest2
  name               = "lttm-cwl-to-firehose-main-uswest2"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_main_uswest2_trust.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_main_uswest2" {
  provider = aws.default_uswest2
  name     = "lttm-cwl-to-firehose-main-uswest2-policy"
  role     = aws_iam_role.cwl_to_firehose_main_uswest2.id
  policy   = data.aws_iam_policy_document.cwl_to_firehose_main_uswest2_policy.json
}


# LTTMAccessAnalyzerReadRole - allow the agent (lttm-agent-role in the main account) to query IAM Access Analyzer findings in dev
data "aws_iam_policy_document" "aa_cross_account_dev_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "aa_cross_account_dev_policy" {
  statement {
    effect = "Allow"
    actions = [
      "access-analyzer:ListAnalyzers",
      "access-analyzer:ListFindings",
      "access-analyzer:ListFindingsV2",
      "access-analyzer:GetFinding",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "aa_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMAccessAnalyzerReadRole"
  assume_role_policy = data.aws_iam_policy_document.aa_cross_account_dev_trust.json
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "aa_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-access-analyzer-read"
  role     = aws_iam_role.aa_cross_account_dev.id
  policy   = data.aws_iam_policy_document.aa_cross_account_dev_policy.json
}

# LTTMAccessAnalyzerReadRole - allow the agent (lttm-agent-role in the main account) to query IAM Access Analyzer findings in prod
data "aws_iam_policy_document" "aa_cross_account_prod_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }
}

data "aws_iam_policy_document" "aa_cross_account_prod_policy" {
  statement {
    effect = "Allow"
    actions = [
      "access-analyzer:ListAnalyzers",
      "access-analyzer:ListFindings",
      "access-analyzer:ListFindingsV2",
      "access-analyzer:GetFinding",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "aa_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMAccessAnalyzerReadRole"
  assume_role_policy = data.aws_iam_policy_document.aa_cross_account_prod_trust.json
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "aa_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-access-analyzer-read"
  role     = aws_iam_role.aa_cross_account_prod.id
  policy   = data.aws_iam_policy_document.aa_cross_account_prod_policy.json
}


# LTTMHealthReadRole - allow the agent (lttm-agent-role in the main account) to query AWS Health events in dev
data "aws_iam_policy_document" "health_cross_account_dev_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "health_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-health-read"
  role     = aws_iam_role.health_cross_account_dev.id
  policy   = data.aws_iam_policy_document.health_cross_account_dev_policy.json
}

# LTTMHealthReadRole - allow the agent (lttm-agent-role in the main account) to query AWS Health events in prod
data "aws_iam_policy_document" "health_cross_account_prod_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "health_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-health-read"
  role     = aws_iam_role.health_cross_account_prod.id
  policy   = data.aws_iam_policy_document.health_cross_account_prod_policy.json
}

# LTTMQuotasReadRole allow the agent (lttm-agent-role in the main account) to query Service Quotas in dev
data "aws_iam_policy_document" "quotas_cross_account_dev_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "quotas_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-quotas-read"
  role     = aws_iam_role.quotas_cross_account_dev.id
  policy   = data.aws_iam_policy_document.quotas_cross_account_dev_policy.json
}

# LTTMQuotasReadRole allow the agent (lttm-agent-role in the main account) to query Service Quotas in prod
data "aws_iam_policy_document" "quotas_cross_account_prod_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "quotas_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-quotas-read"
  role     = aws_iam_role.quotas_cross_account_prod.id
  policy   = data.aws_iam_policy_document.quotas_cross_account_prod_policy.json
}


# LTTMGuardDutyReadRole - allow the agent (lttm-agent-role in the main account) to query GuardDuty findings in dev
data "aws_iam_policy_document" "gd_cross_account_dev_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "gd_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-guardduty-read"
  role     = aws_iam_role.gd_cross_account_dev.id
  policy   = data.aws_iam_policy_document.gd_cross_account_dev_policy.json
}


# LTTMGuardDutyReadRole - allow the agent (lttm-agent-role in the main account) to query GuardDuty findings in prod
data "aws_iam_policy_document" "gd_cross_account_prod_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "gd_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-guardduty-read"
  role     = aws_iam_role.gd_cross_account_prod.id
  policy   = data.aws_iam_policy_document.gd_cross_account_prod_policy.json
}


# lttm-firehose-main-useast1 - Firehose execution role for main account in us-east-1 region
data "aws_iam_policy_document" "firehose_main_useast1_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "firehose_main_useast1_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/cloudwatch/*",
      "arn:aws:s3:::${var.prefix}/cloudwatch-errors/*",
    ]
  }
}

resource "aws_iam_role" "firehose_main_useast1" {
  provider           = aws.default_useast1
  name               = "lttm-firehose-main-useast1"
  assume_role_policy = data.aws_iam_policy_document.firehose_main_useast1_trust.json
}

resource "aws_iam_role_policy" "firehose_main_useast1" {
  provider = aws.default_useast1
  name     = "lttm-firehose-main-useast1-policy"
  role     = aws_iam_role.firehose_main_useast1.id
  policy   = data.aws_iam_policy_document.firehose_main_useast1_policy.json
}


# lttm-cwl-to-firehose-main-useast1 CloudWatch → Firehose role for main account in us-east-1
data "aws_iam_policy_document" "cwl_to_firehose_main_useast1_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_main_useast1_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.global_region}:${var.main_account_id}:deliverystream/lttm-firehose-main-useast1"]
  }
}

resource "aws_iam_role" "cwl_to_firehose_main_useast1" {
  provider           = aws.default_useast1
  name               = "lttm-cwl-to-firehose-main-useast1"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_main_useast1_trust.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_main_useast1" {
  provider = aws.default_useast1
  name     = "lttm-cwl-to-firehose-main-useast1-policy"
  role     = aws_iam_role.cwl_to_firehose_main_useast1.id
  policy   = data.aws_iam_policy_document.cwl_to_firehose_main_useast1_policy.json
}


# LTTMMacieReadRole - allow the agent (lttm-agent-role in the main account) to query Macie sensitive data findings in dev
data "aws_iam_policy_document" "macie_cross_account_dev_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "macie_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-macie-read"
  role     = aws_iam_role.macie_cross_account_dev.id
  policy   = data.aws_iam_policy_document.macie_cross_account_dev_policy.json
}


# LTTMMacieReadRole - allow the agent (lttm-agent-role in the main account) to query Macie sensitive data findings in prod
data "aws_iam_policy_document" "macie_cross_account_prod_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "macie_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-macie-read"
  role     = aws_iam_role.macie_cross_account_prod.id
  policy   = data.aws_iam_policy_document.macie_cross_account_prod_policy.json
}


# LTTMInspectorReadRole - allow the agent (lttm-agent-role in the main account) to query Inspector v2 vulnerability findings in dev
data "aws_iam_policy_document" "inspector_cross_account_dev_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "inspector_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-inspector-read"
  role     = aws_iam_role.inspector_cross_account_dev.id
  policy   = data.aws_iam_policy_document.inspector_cross_account_dev_policy.json
}


# LTTMInspectorReadRole - allow the agent (lttm-agent-role in the main account) to query Inspector v2 vulnerability findings in prod
data "aws_iam_policy_document" "inspector_cross_account_prod_trust" {
  statement {
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
  tags               = { Project = var.prefix }
}

resource "aws_iam_role_policy" "inspector_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-inspector-read"
  role     = aws_iam_role.inspector_cross_account_prod.id
  policy   = data.aws_iam_policy_document.inspector_cross_account_prod_policy.json
}
