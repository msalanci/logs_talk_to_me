# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# iam_cloudwatch_pipeline.tf — the IAM roles of the CloudWatch Logs pipeline in cloudwatch.tf: the Firehose roles write
# to the lake under cloudwatch/ and cloudwatch-errors/, and the CloudWatch Logs roles put records into Firehose. Each
# role is in the account of the stream or account policy that uses it: neither takes another account's role. The
# CloudWatch Logs roles' policies build the stream's ARN from its name: with an ARN unknown at plan time,
# PutAccountPolicy fails.

data "aws_iam_policy_document" "firehose_main_trust" {
  statement {
    sid     = "AllowFirehoseAssumeRole"
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
    sid     = "WriteCloudWatchLogsToLake"
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

data "aws_iam_policy_document" "firehose_cross_account_dev_trust" {
  statement {
    sid     = "AllowFirehoseAssumeRole"
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
    sid     = "WriteCloudWatchLogsToLake"
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

data "aws_iam_policy_document" "firehose_cross_account_prod_trust" {
  statement {
    sid     = "AllowFirehoseAssumeRole"
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
    sid     = "WriteCloudWatchLogsToLake"
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

data "aws_iam_policy_document" "cwl_to_firehose_main_trust" {
  statement {
    sid     = "AllowCloudWatchLogsAssumeRole"
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

data "aws_iam_policy_document" "cwl_to_firehose_dev_trust" {
  statement {
    sid     = "AllowCloudWatchLogsAssumeRole"
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

data "aws_iam_policy_document" "cwl_to_firehose_prod_trust" {
  statement {
    sid     = "AllowCloudWatchLogsAssumeRole"
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

data "aws_iam_policy_document" "cwl_to_firehose_main_policy" {
  statement {
    sid    = "PutRecordsToFirehoseStream"
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]

    resources = ["arn:aws:firehose:${var.project_region}:${var.main_account_id}:deliverystream/lttm-firehose-main"]
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_dev_policy" {
  statement {
    sid    = "PutRecordsToFirehoseStream"
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]

    resources = ["arn:aws:firehose:${var.project_region}:${var.dev_account_id}:deliverystream/lttm-firehose-dev"]
  }
}

data "aws_iam_policy_document" "cwl_to_firehose_prod_policy" {
  statement {
    sid    = "PutRecordsToFirehoseStream"
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]

    resources = ["arn:aws:firehose:${var.project_region}:${var.prod_account_id}:deliverystream/lttm-firehose-prod"]
  }
}

resource "aws_iam_role" "cwl_to_firehose_main" {
  name               = "lttm-cwl-to-firehose-main"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_main_trust.json
}

resource "aws_iam_role" "cwl_to_firehose_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-cwl-to-firehose-dev"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_dev_trust.json
}

resource "aws_iam_role" "cwl_to_firehose_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-cwl-to-firehose-prod"
  assume_role_policy = data.aws_iam_policy_document.cwl_to_firehose_prod_trust.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_main" {
  name   = "lttm-cwl-to-firehose-main-policy"
  role   = aws_iam_role.cwl_to_firehose_main.id
  policy = data.aws_iam_policy_document.cwl_to_firehose_main_policy.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-cwl-to-firehose-dev-policy"
  role     = aws_iam_role.cwl_to_firehose_dev.id
  policy   = data.aws_iam_policy_document.cwl_to_firehose_dev_policy.json
}

resource "aws_iam_role_policy" "cwl_to_firehose_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-cwl-to-firehose-prod-policy"
  role     = aws_iam_role.cwl_to_firehose_prod.id
  policy   = data.aws_iam_policy_document.cwl_to_firehose_prod_policy.json
}

data "aws_iam_policy_document" "firehose_main_uswest2_trust" {
  statement {
    sid     = "AllowFirehoseAssumeRole"
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
    sid     = "WriteCloudWatchLogsToLake"
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

data "aws_iam_policy_document" "cwl_to_firehose_main_uswest2_trust" {
  statement {
    sid     = "AllowCloudWatchLogsAssumeRole"
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
    sid    = "PutRecordsToFirehoseStream"
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

data "aws_iam_policy_document" "firehose_main_useast1_trust" {
  statement {
    sid     = "AllowFirehoseAssumeRole"
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
    sid     = "WriteCloudWatchLogsToLake"
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

data "aws_iam_policy_document" "cwl_to_firehose_main_useast1_trust" {
  statement {
    sid     = "AllowCloudWatchLogsAssumeRole"
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
    sid    = "PutRecordsToFirehoseStream"
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
