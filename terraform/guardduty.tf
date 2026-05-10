# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# guardduty.tf
# Enables GuardDuty across all three LTTM accounts using the AWS Organizations
# Captures GuardDuty findings in real-time via EventBridge and archives them to S3 
# for long-term Athena querying beyond the 90-day API retention, with Firehose
# =============================================================================


resource "aws_guardduty_detector" "main_eu" {
  enable = true
  tags   = { Project = var.prefix }
}

resource "aws_guardduty_detector" "main_uswest2" {
  provider = aws.default_uswest2
  enable   = true
  tags     = { Project = var.prefix }
}

resource "aws_guardduty_detector" "dev_eu" {
  provider = aws.dev_eucentral1
  enable   = true
  tags     = { Project = var.prefix }
}

resource "aws_guardduty_detector" "prod_eu" {
  provider = aws.prod_eucentral1
  enable   = true
  tags     = { Project = var.prefix }
}

resource "aws_guardduty_organization_admin_account" "main" {
  admin_account_id = var.main_account_id
  depends_on       = [aws_guardduty_detector.main_eu]
}

resource "aws_guardduty_organization_configuration" "main" {
  detector_id                      = aws_guardduty_detector.main_eu.id
  auto_enable_organization_members = "ALL"
  depends_on                       = [aws_guardduty_organization_admin_account.main]
}

resource "aws_guardduty_member" "dev" {
  detector_id = aws_guardduty_detector.main_eu.id
  account_id  = var.dev_account_id
  email       = var.dev_account_email
  invite      = false
  depends_on  = [aws_guardduty_organization_configuration.main]
  lifecycle {
    ignore_changes = [email, invite]
  }
}

resource "aws_guardduty_member" "prod" {
  detector_id = aws_guardduty_detector.main_eu.id
  account_id  = var.prod_account_id
  email       = var.prod_account_email
  invite      = false
  depends_on  = [aws_guardduty_organization_configuration.main]
  lifecycle {
    ignore_changes = [email, invite]
  }
}

resource "aws_kinesis_firehose_delivery_stream" "guardduty_main" {
  name        = "lttm-firehose-guardduty"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.guardduty_firehose_main.arn
    bucket_arn = "arn:aws:s3:::${var.prefix}"
    prefix = "guardduty/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "guardduty-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format = "UNCOMPRESSED"
    buffering_size     = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{account_id:.detail.accountId, year:(.time[0:4]), month:(.time[5:7]), day:(.time[8:10])}"
        }
      }
    }
  }
}

resource "aws_kinesis_firehose_delivery_stream" "guardduty_dev" {
  provider    = aws.dev_eucentral1
  name        = "lttm-firehose-guardduty-dev"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.guardduty_firehose_cross_account_dev.arn
    bucket_arn = "arn:aws:s3:::${var.prefix}"

    prefix              = "guardduty/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "guardduty-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64
    buffering_interval  = 60

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{account_id:.detail.accountId, year:(.time[0:4]), month:(.time[5:7]), day:(.time[8:10])}"
        }
      }
    }
  }

  depends_on = [aws_iam_role.guardduty_firehose_cross_account_dev]
}

resource "aws_kinesis_firehose_delivery_stream" "guardduty_prod" {
  provider    = aws.prod_eucentral1
  name        = "lttm-firehose-guardduty-prod"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.guardduty_firehose_cross_account_prod.arn
    bucket_arn = "arn:aws:s3:::${var.prefix}"

    prefix              = "guardduty/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "guardduty-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64
    buffering_interval  = 60

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{account_id:.detail.accountId, year:(.time[0:4]), month:(.time[5:7]), day:(.time[8:10])}"
        }
      }
    }
  }

  depends_on = [aws_iam_role.guardduty_firehose_cross_account_prod]
}

resource "aws_cloudwatch_event_rule" "guardduty_main" {
  name        = "lttm-guardduty-to-firehose"
  description = "Captures GuardDuty findings in the main account and routes to Firehose for S3 archival"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })
}

resource "aws_cloudwatch_event_target" "guardduty_main" {
  rule     = aws_cloudwatch_event_rule.guardduty_main.name
  arn      = aws_kinesis_firehose_delivery_stream.guardduty_main.arn
  role_arn = aws_iam_role.guardduty_eventbridge_main.arn
}

resource "aws_cloudwatch_event_rule" "guardduty_dev" {
  provider    = aws.dev_eucentral1
  name        = "lttm-guardduty-to-firehose-dev"
  description = "Captures GuardDuty findings in the dev account and routes to Firehose for S3 archival"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })
}

resource "aws_cloudwatch_event_target" "guardduty_dev" {
  provider = aws.dev_eucentral1
  rule     = aws_cloudwatch_event_rule.guardduty_dev.name
  arn      = aws_kinesis_firehose_delivery_stream.guardduty_dev.arn
  role_arn = aws_iam_role.guardduty_eventbridge_dev.arn
}

resource "aws_cloudwatch_event_rule" "guardduty_prod" {
  provider    = aws.prod_eucentral1
  name        = "lttm-guardduty-to-firehose-prod"
  description = "Captures GuardDuty findings in the prod account and routes to Firehose for S3 archival"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })
}

resource "aws_cloudwatch_event_target" "guardduty_prod" {
  provider = aws.prod_eucentral1
  rule     = aws_cloudwatch_event_rule.guardduty_prod.name
  arn      = aws_kinesis_firehose_delivery_stream.guardduty_prod.arn
  role_arn = aws_iam_role.guardduty_eventbridge_prod.arn
}

data "aws_iam_policy_document" "guardduty_firehose_main_trust" {
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

data "aws_iam_policy_document" "guardduty_firehose_main_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/guardduty/*",
      "arn:aws:s3:::${var.prefix}/guardduty-errors/*",
    ]
  }
}

resource "aws_iam_role" "guardduty_firehose_main" {
  name               = "lttm-guardduty-firehose-main"
  assume_role_policy = data.aws_iam_policy_document.guardduty_firehose_main_trust.json
}

resource "aws_iam_role_policy" "guardduty_firehose_main" {
  name   = "lttm-guardduty-firehose-main-policy"
  role   = aws_iam_role.guardduty_firehose_main.id
  policy = data.aws_iam_policy_document.guardduty_firehose_main_policy.json
}

data "aws_iam_policy_document" "guardduty_firehose_cross_account_dev_trust" {
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

data "aws_iam_policy_document" "guardduty_firehose_cross_account_dev_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/guardduty/*",
      "arn:aws:s3:::${var.prefix}/guardduty-errors/*",
    ]
  }
}

resource "aws_iam_role" "guardduty_firehose_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-guardduty-firehose-dev"
  assume_role_policy = data.aws_iam_policy_document.guardduty_firehose_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "guardduty_firehose_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-guardduty-firehose-dev-policy"
  role     = aws_iam_role.guardduty_firehose_cross_account_dev.id
  policy   = data.aws_iam_policy_document.guardduty_firehose_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "guardduty_firehose_cross_account_prod_trust" {
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

data "aws_iam_policy_document" "guardduty_firehose_cross_account_prod_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/guardduty/*",
      "arn:aws:s3:::${var.prefix}/guardduty-errors/*",
    ]
  }
}

resource "aws_iam_role" "guardduty_firehose_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-guardduty-firehose-prod"
  assume_role_policy = data.aws_iam_policy_document.guardduty_firehose_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "guardduty_firehose_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-guardduty-firehose-prod-policy"
  role     = aws_iam_role.guardduty_firehose_cross_account_prod.id
  policy   = data.aws_iam_policy_document.guardduty_firehose_cross_account_prod_policy.json
}

data "aws_iam_policy_document" "guardduty_eventbridge_main_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "guardduty_eventbridge_main_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.main_account_id}:deliverystream/lttm-firehose-guardduty"]
  }
}

resource "aws_iam_role" "guardduty_eventbridge_main" {
  name               = "lttm-guardduty-eventbridge-main"
  assume_role_policy = data.aws_iam_policy_document.guardduty_eventbridge_main_trust.json
}

resource "aws_iam_role_policy" "guardduty_eventbridge_main" {
  name   = "lttm-guardduty-eventbridge-main-policy"
  role   = aws_iam_role.guardduty_eventbridge_main.id
  policy = data.aws_iam_policy_document.guardduty_eventbridge_main_policy.json
}

data "aws_iam_policy_document" "guardduty_eventbridge_dev_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.dev_account_id]
    }
  }
}

data "aws_iam_policy_document" "guardduty_eventbridge_dev_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.dev_account_id}:deliverystream/lttm-firehose-guardduty-dev"]
  }
}

resource "aws_iam_role" "guardduty_eventbridge_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-guardduty-eventbridge-dev"
  assume_role_policy = data.aws_iam_policy_document.guardduty_eventbridge_dev_trust.json
}

resource "aws_iam_role_policy" "guardduty_eventbridge_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-guardduty-eventbridge-dev-policy"
  role     = aws_iam_role.guardduty_eventbridge_dev.id
  policy   = data.aws_iam_policy_document.guardduty_eventbridge_dev_policy.json
}

data "aws_iam_policy_document" "guardduty_eventbridge_prod_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.prod_account_id]
    }
  }
}

data "aws_iam_policy_document" "guardduty_eventbridge_prod_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.prod_account_id}:deliverystream/lttm-firehose-guardduty-prod"]
  }
}

resource "aws_iam_role" "guardduty_eventbridge_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-guardduty-eventbridge-prod"
  assume_role_policy = data.aws_iam_policy_document.guardduty_eventbridge_prod_trust.json
}

resource "aws_iam_role_policy" "guardduty_eventbridge_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-guardduty-eventbridge-prod-policy"
  role     = aws_iam_role.guardduty_eventbridge_prod.id
  policy   = data.aws_iam_policy_document.guardduty_eventbridge_prod_policy.json
}
