# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# guardduty_archive.tf — the archive of GuardDuty findings in the lake, which the API keeps for only 90 days: an
# EventBridge rule in each account → one Firehose stream per account → guardduty/. A rule in the main account's
# var.agentcore_region forwards its findings to the main account's bus in var.project_region. Firehose takes an
# execution role only from its own account, and dynamic partitioning needs a buffer of at least 64 MB.

resource "aws_kinesis_firehose_delivery_stream" "guardduty_main" {
  name        = "lttm-firehose-guardduty"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.guardduty_firehose_main.arn
    bucket_arn = "arn:aws:s3:::${var.prefix}"

    prefix = "guardduty/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"

    error_output_prefix = "guardduty-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"

    compression_format = "UNCOMPRESSED"
    buffering_size     = 64
    buffering_interval = 60

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      # Without a newline after each record, Athena's JSON reader sees only the first record of each file.
      processors {
        type = "AppendDelimiterToRecord"
      }

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
        type = "AppendDelimiterToRecord"
      }

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
        type = "AppendDelimiterToRecord"
      }

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

# As the delegated administrator, the main account's bus also receives the members' eu-central-1 findings, which
# their own rules archive: this pattern takes only the main account's own findings and those forwarded from another
# Region, so no finding is archived twice.
resource "aws_cloudwatch_event_rule" "guardduty_main" {
  name        = "lttm-guardduty-to-firehose"
  description = "Captures GuardDuty findings in the main account and routes to Firehose for S3 archival"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    "$or" = [
      { detail = { accountId = [var.main_account_id] } },
      { region = [{ "anything-but" = var.project_region }] },
    ]
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

resource "aws_cloudwatch_event_rule" "guardduty_forward_main_uswest2" {
  region      = var.agentcore_region
  name        = "lttm-guardduty-forward-to-${var.project_region}"
  description = "Forwards GuardDuty findings from ${var.agentcore_region} to ${var.project_region} for S3 archival"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })
}

resource "aws_cloudwatch_event_target" "guardduty_forward_main_uswest2" {
  region    = var.agentcore_region
  rule      = aws_cloudwatch_event_rule.guardduty_forward_main_uswest2.name
  target_id = "forward-to-${var.project_region}"
  arn       = "arn:aws:events:${var.project_region}:${var.main_account_id}:event-bus/default"
  role_arn  = aws_iam_role.guardduty_cross_region_main.arn
}

data "aws_iam_policy_document" "guardduty_firehose_main_trust" {
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

data "aws_iam_policy_document" "guardduty_firehose_main_policy" {
  statement {
    sid     = "WriteGuardDutyFindingsToLake"
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

data "aws_iam_policy_document" "guardduty_firehose_cross_account_dev_policy" {
  statement {
    sid     = "WriteGuardDutyFindingsToLake"
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

data "aws_iam_policy_document" "guardduty_firehose_cross_account_prod_policy" {
  statement {
    sid     = "WriteGuardDutyFindingsToLake"
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
    sid     = "AllowEventBridgeAssumeRole"
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
    sid    = "SendFindingsToFirehose"
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
    sid     = "AllowEventBridgeAssumeRole"
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
    sid    = "SendFindingsToFirehose"
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
    sid     = "AllowEventBridgeAssumeRole"
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
    sid    = "SendFindingsToFirehose"
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

data "aws_iam_policy_document" "guardduty_cross_region_main_trust" {
  statement {
    sid     = "AllowEventBridgeAssumeRole"
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

data "aws_iam_policy_document" "guardduty_cross_region_main_policy" {
  statement {
    sid       = "ForwardFindingsToProjectRegionBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = ["arn:aws:events:${var.project_region}:${var.main_account_id}:event-bus/default"]
  }
}

resource "aws_iam_role" "guardduty_cross_region_main" {
  name               = "lttm-guardduty-eventbridge-cross-region"
  assume_role_policy = data.aws_iam_policy_document.guardduty_cross_region_main_trust.json
}

resource "aws_iam_role_policy" "guardduty_cross_region_main" {
  name   = "lttm-guardduty-cross-region-forward"
  role   = aws_iam_role.guardduty_cross_region_main.id
  policy = data.aws_iam_policy_document.guardduty_cross_region_main_policy.json
}
