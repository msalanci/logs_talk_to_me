# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# lake_check.tf — the Lambda lttm-lake-check, run daily by EventBridge Scheduler (var.lake_check_schedule, UTC), that
# checks the lake's rows of the day before. With Athena: Config times in UTC under their own day, the account ID
# present, no Config item twice, UTC times in the CloudTrail and GuardDuty tables. With S3: every Config snapshot
# delivery has its config_snapshot file with the same records and the other way round, and every delivery channel
# delivered. A finding, or a check that could not run, is emailed through the lake alarm topic (alerts.tf).

locals {
  lake_check_tables = {
    config_logs        = "config/"
    config_snapshot    = "config-snapshot/"
    cloudtrail_logs    = "cloudtrail/"
    guardduty_findings = "guardduty/"
  }

  lake_check_snapshot_channels = flatten([
    for label, account in { main = var.main_account_id, dev = var.dev_account_id, prod = var.prod_account_id } :
    [for region in sort(keys(local.config_snapshot_channels[label])) : "${account}/${region}"]
  ])
}

data "archive_file" "lake_check" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/lake_check"
  output_path = "${path.module}/lambda/lake_check.zip"
  excludes    = ["__pycache__"]
}

resource "aws_lambda_function" "lake_check" {
  function_name    = "lttm-lake-check"
  role             = aws_iam_role.lake_check.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256
  filename         = data.archive_file.lake_check.output_path
  source_code_hash = data.archive_file.lake_check.output_base64sha256

  environment {
    variables = {
      DATABASE          = aws_glue_catalog_database.lttm_logs.name
      WORKGROUP         = aws_athena_workgroup.lttm.name
      TOPIC_ARN         = aws_sns_topic.lake_error_files.arn
      BUCKET            = var.prefix
      SNAPSHOT_CHANNELS = join(",", local.lake_check_snapshot_channels)
    }
  }
}

data "aws_iam_policy_document" "lake_check_trust" {
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

resource "aws_iam_role" "lake_check" {
  name               = "lttm-lake-check-role"
  assume_role_policy = data.aws_iam_policy_document.lake_check_trust.json
}

data "aws_iam_policy_document" "lake_check" {
  statement {
    sid     = "OwnLogs"
    effect  = "Allow"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-lake-check",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-lake-check:*",
    ]
  }

  statement {
    sid       = "AthenaQueries"
    effect    = "Allow"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution"]
    resources = [aws_athena_workgroup.lttm.arn]
  }

  statement {
    sid     = "GlueCatalogRead"
    effect  = "Allow"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"]
    resources = concat(
      [
        "arn:aws:glue:${var.project_region}:${var.main_account_id}:catalog",
        "arn:aws:glue:${var.project_region}:${var.main_account_id}:database/${aws_glue_catalog_database.lttm_logs.name}",
      ],
      [for t in keys(local.lake_check_tables) : "arn:aws:glue:${var.project_region}:${var.main_account_id}:table/${aws_glue_catalog_database.lttm_logs.name}/${t}"],
    )
  }

  statement {
    sid       = "ReadCheckedFolders"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = [for prefix in values(local.lake_check_tables) : "arn:aws:s3:::${var.prefix}/${prefix}*"]
  }

  statement {
    sid       = "ReadConfigSnapshotDeliveries"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.prefix}/AWSLogs/*/Config/*/ConfigSnapshot/*"]
  }

  statement {
    sid       = "AthenaResults"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${var.prefix}/athena-results/*"]
  }

  statement {
    sid       = "ListLake"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.prefix}"]
  }

  statement {
    sid       = "EmailFindings"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.lake_error_files.arn]
  }
}

resource "aws_iam_role_policy" "lake_check" {
  name   = "lttm-lake-check-policy"
  role   = aws_iam_role.lake_check.id
  policy = data.aws_iam_policy_document.lake_check.json
}

resource "aws_lakeformation_permissions" "lake_check_db" {
  principal   = aws_iam_role.lake_check.arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.lttm_logs.name
  }
}

resource "aws_lakeformation_permissions" "lake_check_tables" {
  for_each    = local.lake_check_tables
  principal   = aws_iam_role.lake_check.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.lttm_logs.name
    name          = each.key
  }

  depends_on = [
    aws_glue_catalog_table.config_logs,
    aws_glue_catalog_table.config_snapshot,
    aws_glue_catalog_table.cloudtrail_logs,
    aws_glue_catalog_table.guardduty_findings,
  ]
}

data "aws_iam_policy_document" "lake_check_scheduler_trust" {
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
      values   = [var.main_account_id]
    }
  }
}

resource "aws_iam_role" "lake_check_scheduler" {
  name               = "lttm-lake-check-scheduler"
  assume_role_policy = data.aws_iam_policy_document.lake_check_scheduler_trust.json
}

data "aws_iam_policy_document" "lake_check_scheduler" {
  statement {
    sid       = "InvokeLakeCheck"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.lake_check.arn]
  }
}

resource "aws_iam_role_policy" "lake_check_scheduler" {
  name   = "lttm-lake-check-scheduler"
  role   = aws_iam_role.lake_check_scheduler.id
  policy = data.aws_iam_policy_document.lake_check_scheduler.json
}

resource "aws_scheduler_schedule" "lake_check" {
  name                         = "lttm-lake-check-daily"
  schedule_expression          = var.lake_check_schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.lake_check.arn
    role_arn = aws_iam_role.lake_check_scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
}
