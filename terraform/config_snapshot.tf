# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# config_snapshot.tf — Config Snapshot delivery pipeline
# The existing Config pipeline (config_pipeline.tf) only captures configuration CHANGES via EventBridge. 
# Resources that existed before the pipeline was set up, or that haven't changed since, are invisible.
# =============================================================================


data "archive_file" "config_snapshot_transform" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/config_snapshot_transform"
  output_path = "${path.module}/lambda/config_snapshot_transform.zip"
}

resource "aws_lambda_function" "config_snapshot_transform" {
  function_name    = "lttm-config-snapshot-transform"
  role             = aws_iam_role.config_snapshot_lambda.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 300 # 5 min — snapshot files can be large
  memory_size      = 256 # more memory for large snapshot files
  filename         = data.archive_file.config_snapshot_transform.output_path
  source_code_hash = data.archive_file.config_snapshot_transform.output_base64sha256

  environment {
    variables = {
      DEST_BUCKET = var.prefix
    }
  }

  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "config_snapshot_s3" {
  statement_id   = "AllowS3SnapshotInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.config_snapshot_transform.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = "arn:aws:s3:::${var.prefix}"
  source_account = var.main_account_id
}

resource "aws_s3_bucket_notification" "config_snapshot" {
  bucket = var.prefix

  lambda_function {
    lambda_function_arn = aws_lambda_function.config_snapshot_transform.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "AWSLogs/"
    filter_suffix       = ".json.gz"
  }

  depends_on = [aws_lambda_permission.config_snapshot_s3]
}

data "aws_iam_policy_document" "config_snapshot_lambda_trust" {
  statement {
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
  tags               = { Project = var.prefix }
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
