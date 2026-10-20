# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# config_snapshot.tf — the config_snapshot table's pipeline. EventBridge Scheduler asks each Config delivery channel
# (3 accounts x 3 Regions) for a snapshot at var.config_snapshot_schedule, as Config's own cycle can't be set to a time
# of day. Config delivers it to the lake under AWSLogs/, where s3_notification.tf starts the Lambda
# lttm-config-snapshot-transform, which files its items under config-snapshot/. A delivery before 02:00 UTC is filed
# under the day before, so the schedules are in UTC and their retries stay within the first hour.

data "archive_file" "config_snapshot_transform" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/config_snapshot_transform"
  excludes    = ["__pycache__"]
  output_path = "${path.module}/lambda/config_snapshot_transform.zip"
}

resource "aws_lambda_function" "config_snapshot_transform" {
  function_name    = "lttm-config-snapshot-transform"
  role             = aws_iam_role.config_snapshot_lambda.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256
  filename         = data.archive_file.config_snapshot_transform.output_path
  source_code_hash = data.archive_file.config_snapshot_transform.output_base64sha256

  environment {
    variables = {
      DEST_BUCKET = var.prefix
    }
  }
}

resource "aws_lambda_permission" "config_snapshot_s3" {
  statement_id   = "AllowS3SnapshotInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.config_snapshot_transform.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = "arn:aws:s3:::${var.prefix}"
  source_account = var.main_account_id
}

data "aws_iam_policy_document" "config_snapshot_lambda_trust" {
  statement {
    sid     = "AllowLambdaAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "config_snapshot_lambda" {
  name               = "lttm-config-snapshot-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.config_snapshot_lambda_trust.json
}

data "aws_iam_policy_document" "config_snapshot_lambda_policy" {
  statement {
    sid     = "ReadConfigSnapshots"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/AWSLogs/*/Config/*/ConfigSnapshot/*",
    ]
  }

  statement {
    sid     = "WriteConfigSnapshotData"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/config-snapshot/*",
    ]
  }

  statement {
    sid       = "WriteUnreadableSnapshotRecords"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${var.prefix}/config-errors/snapshot-unreadable/*"]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "config_snapshot_lambda" {
  name   = "lttm-config-snapshot-lambda-policy"
  role   = aws_iam_role.config_snapshot_lambda.id
  policy = data.aws_iam_policy_document.config_snapshot_lambda_policy.json
}

locals {
  config_snapshot_channels = {
    main = merge({ (var.project_region) = aws_config_delivery_channel.main_home.name },
    { for r, c in aws_config_delivery_channel.main_multiregion : r => c.name })
    dev = merge({ (var.project_region) = aws_config_delivery_channel.dev_home.name },
    { for r, c in aws_config_delivery_channel.dev_multiregion : r => c.name })
    prod = merge({ (var.project_region) = aws_config_delivery_channel.prod_home.name },
    { for r, c in aws_config_delivery_channel.prod_multiregion : r => c.name })
  }
  config_snapshot_target = "arn:aws:scheduler:::aws-sdk:config:deliverConfigSnapshot"
}

data "aws_iam_policy_document" "config_snapshot_scheduler_trust" {
  for_each = {
    main = var.main_account_id
    dev  = var.dev_account_id
    prod = var.prod_account_id
  }
  statement {
    sid     = "AllowSchedulerAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [each.value]
    }
  }
}

data "aws_iam_policy_document" "config_snapshot_scheduler" {
  statement {
    sid       = "DeliverConfigSnapshot"
    effect    = "Allow"
    actions   = ["config:DeliverConfigSnapshot"]
    resources = ["*"] # the action has no resource-level permissions
  }
}

resource "aws_iam_role" "config_snapshot_scheduler_main" {
  name               = "lttm-config-snapshot-scheduler"
  assume_role_policy = data.aws_iam_policy_document.config_snapshot_scheduler_trust["main"].json
}

resource "aws_iam_role_policy" "config_snapshot_scheduler_main" {
  name   = "lttm-config-snapshot-scheduler-policy"
  role   = aws_iam_role.config_snapshot_scheduler_main.id
  policy = data.aws_iam_policy_document.config_snapshot_scheduler.json
}

resource "aws_iam_role" "config_snapshot_scheduler_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-config-snapshot-scheduler"
  assume_role_policy = data.aws_iam_policy_document.config_snapshot_scheduler_trust["dev"].json
}

resource "aws_iam_role_policy" "config_snapshot_scheduler_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-config-snapshot-scheduler-policy"
  role     = aws_iam_role.config_snapshot_scheduler_dev.id
  policy   = data.aws_iam_policy_document.config_snapshot_scheduler.json
}

resource "aws_iam_role" "config_snapshot_scheduler_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-config-snapshot-scheduler"
  assume_role_policy = data.aws_iam_policy_document.config_snapshot_scheduler_trust["prod"].json
}

resource "aws_iam_role_policy" "config_snapshot_scheduler_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-config-snapshot-scheduler-policy"
  role     = aws_iam_role.config_snapshot_scheduler_prod.id
  policy   = data.aws_iam_policy_document.config_snapshot_scheduler.json
}

resource "aws_scheduler_schedule" "config_snapshot_main" {
  for_each = local.config_snapshot_channels.main

  region                       = each.key
  name                         = "lttm-config-snapshot-daily"
  description                  = "Config delivers its daily snapshot once, at a fixed time: its own cycle cannot be set to a time of day"
  schedule_expression          = var.config_snapshot_schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = local.config_snapshot_target
    role_arn = aws_iam_role.config_snapshot_scheduler_main.arn
    input    = jsonencode({ DeliveryChannelName = each.value })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
}

resource "aws_scheduler_schedule" "config_snapshot_dev" {
  for_each = local.config_snapshot_channels.dev
  provider = aws.dev_eucentral1

  region                       = each.key
  name                         = "lttm-config-snapshot-daily"
  description                  = "Config delivers its daily snapshot once, at a fixed time: its own cycle cannot be set to a time of day"
  schedule_expression          = var.config_snapshot_schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = local.config_snapshot_target
    role_arn = aws_iam_role.config_snapshot_scheduler_dev.arn
    input    = jsonencode({ DeliveryChannelName = each.value })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
}

resource "aws_scheduler_schedule" "config_snapshot_prod" {
  for_each = local.config_snapshot_channels.prod
  provider = aws.prod_eucentral1

  region                       = each.key
  name                         = "lttm-config-snapshot-daily"
  description                  = "Config delivers its daily snapshot once, at a fixed time: its own cycle cannot be set to a time of day"
  schedule_expression          = var.config_snapshot_schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = local.config_snapshot_target
    role_arn = aws_iam_role.config_snapshot_scheduler_prod.arn
    input    = jsonencode({ DeliveryChannelName = each.value })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
}
