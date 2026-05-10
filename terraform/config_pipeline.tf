# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# config_pipeline.tf
# Creates the AWS Config → EventBridge → Firehose → S3 data pipeline for all three LTTM accounts (main, dev, prod),
# using dedicated lambda function to transform the data
# =============================================================================


data "archive_file" "config_transform" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/config_transform"
  output_path = "${path.module}/lambda/config_transform.zip"
}

resource "aws_lambda_function" "config_transform" {
  function_name    = "lttm-config-transform"
  role             = aws_iam_role.config_lambda.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128
  filename         = data.archive_file.config_transform.output_path
  source_code_hash = data.archive_file.config_transform.output_base64sha256

  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "config_transform_dev" {
  statement_id   = "AllowFirehoseDevInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.config_transform.function_name
  principal      = "firehose.amazonaws.com"
  source_account = var.dev_account_id
  source_arn     = "arn:aws:firehose:${var.project_region}:${var.dev_account_id}:deliverystream/lttm-config-firehose-dev"
}

resource "aws_lambda_permission" "config_transform_prod" {
  statement_id   = "AllowFirehoseProdInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.config_transform.function_name
  principal      = "firehose.amazonaws.com"
  source_account = var.prod_account_id
  source_arn     = "arn:aws:firehose:${var.project_region}:${var.prod_account_id}:deliverystream/lttm-config-firehose-prod"
}

resource "aws_kinesis_firehose_delivery_stream" "config_main" {
  name        = "lttm-config-firehose-main"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.config_firehose_main.arn
    bucket_arn          = "arn:aws:s3:::${var.prefix}"
    prefix              = "config/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "config-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval  = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Lambda"
        parameters {
          parameter_name  = "LambdaArn"
          parameter_value = "${aws_lambda_function.config_transform.arn}:$LATEST"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{account_id:.awsaccountid, year:(.configurationitemcapturetime[0:4]), month:(.configurationitemcapturetime[5:7]), day:(.configurationitemcapturetime[8:10])}"
        }
      }
    }
  }
}

resource "aws_kinesis_firehose_delivery_stream" "config_dev" {
  provider    = aws.dev_eucentral1
  name        = "lttm-config-firehose-dev"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.config_firehose_cross_account_dev.arn
    bucket_arn          = "arn:aws:s3:::${var.prefix}"
    prefix              = "config/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "config-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval  = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Lambda"
        parameters {
          parameter_name  = "LambdaArn"
          parameter_value = "${aws_lambda_function.config_transform.arn}:$LATEST"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{account_id:.awsaccountid, year:(.configurationitemcapturetime[0:4]), month:(.configurationitemcapturetime[5:7]), day:(.configurationitemcapturetime[8:10])}"
        }
      }
    }
  }

  depends_on = [aws_iam_role.config_firehose_cross_account_dev]
}

resource "aws_kinesis_firehose_delivery_stream" "config_prod" {
  provider    = aws.prod_eucentral1
  name        = "lttm-config-firehose-prod"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.config_firehose_cross_account_prod.arn
    bucket_arn          = "arn:aws:s3:::${var.prefix}"
    prefix              = "config/account_id=!{partitionKeyFromQuery:account_id}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "config-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval  = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Lambda"
        parameters {
          parameter_name  = "LambdaArn"
          parameter_value = "${aws_lambda_function.config_transform.arn}:$LATEST"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{account_id:.awsaccountid, year:(.configurationitemcapturetime[0:4]), month:(.configurationitemcapturetime[5:7]), day:(.configurationitemcapturetime[8:10])}"
        }
      }
    }
  }

  depends_on = [aws_iam_role.config_firehose_cross_account_prod]
}

resource "aws_cloudwatch_event_rule" "config_main" {
  name        = "lttm-config-to-firehose-main"
  description = "Captures AWS Config configuration item changes in the main account and routes to Firehose"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_main" {
  rule     = aws_cloudwatch_event_rule.config_main.name
  arn      = aws_kinesis_firehose_delivery_stream.config_main.arn
  role_arn = aws_iam_role.config_eventbridge_main.arn
}

resource "aws_cloudwatch_event_rule" "config_dev" {
  provider    = aws.dev_eucentral1
  name        = "lttm-config-to-firehose-dev"
  description = "Captures AWS Config configuration item changes in the dev account and routes to Firehose"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_dev" {
  provider = aws.dev_eucentral1
  rule     = aws_cloudwatch_event_rule.config_dev.name
  arn      = aws_kinesis_firehose_delivery_stream.config_dev.arn
  role_arn = aws_iam_role.config_eventbridge_dev.arn
}

resource "aws_cloudwatch_event_rule" "config_prod" {
  provider    = aws.prod_eucentral1
  name        = "lttm-config-to-firehose-prod"
  description = "Captures AWS Config configuration item changes in the prod account and routes to Firehose"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_prod" {
  provider = aws.prod_eucentral1
  rule     = aws_cloudwatch_event_rule.config_prod.name
  arn      = aws_kinesis_firehose_delivery_stream.config_prod.arn
  role_arn = aws_iam_role.config_eventbridge_prod.arn
}

data "aws_iam_policy_document" "config_lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "config_lambda_policy" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-config-transform*",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-config-transform*:*",
    ]
  }
}

resource "aws_iam_role" "config_lambda" {
  name               = "lttm-config-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.config_lambda_trust.json
}

resource "aws_iam_role_policy" "config_lambda" {
  name   = "lttm-config-lambda-policy"
  role   = aws_iam_role.config_lambda.id
  policy = data.aws_iam_policy_document.config_lambda_policy.json
}

data "aws_iam_policy_document" "config_firehose_main_trust" {
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

data "aws_iam_policy_document" "config_firehose_main_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/config/*",
      "arn:aws:s3:::${var.prefix}/config-errors/*",
    ]
  }

  statement {
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:${var.project_region}:${var.main_account_id}:function:lttm-config-transform",
      "arn:aws:lambda:${var.project_region}:${var.main_account_id}:function:lttm-config-transform:$LATEST",
    ]
  }
}

resource "aws_iam_role" "config_firehose_main" {
  name               = "lttm-config-firehose-main"
  assume_role_policy = data.aws_iam_policy_document.config_firehose_main_trust.json
}

resource "aws_iam_role_policy" "config_firehose_main" {
  name   = "lttm-config-firehose-main-policy"
  role   = aws_iam_role.config_firehose_main.id
  policy = data.aws_iam_policy_document.config_firehose_main_policy.json
}

data "aws_iam_policy_document" "config_firehose_cross_account_dev_trust" {
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

data "aws_iam_policy_document" "config_firehose_cross_account_dev_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/config/*",
      "arn:aws:s3:::${var.prefix}/config-errors/*",
    ]
  }

  statement {
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:${var.project_region}:${var.main_account_id}:function:lttm-config-transform",
      "arn:aws:lambda:${var.project_region}:${var.main_account_id}:function:lttm-config-transform:$LATEST",
    ]
  }
}

resource "aws_iam_role" "config_firehose_cross_account_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-config-firehose-cross-account-dev"
  assume_role_policy = data.aws_iam_policy_document.config_firehose_cross_account_dev_trust.json
}

resource "aws_iam_role_policy" "config_firehose_cross_account_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-config-firehose-cross-account-dev-policy"
  role     = aws_iam_role.config_firehose_cross_account_dev.id
  policy   = data.aws_iam_policy_document.config_firehose_cross_account_dev_policy.json
}

data "aws_iam_policy_document" "config_firehose_cross_account_prod_trust" {
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

data "aws_iam_policy_document" "config_firehose_cross_account_prod_policy" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.prefix}/config/*",
      "arn:aws:s3:::${var.prefix}/config-errors/*",
    ]
  }

  statement {
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:${var.project_region}:${var.main_account_id}:function:lttm-config-transform",
      "arn:aws:lambda:${var.project_region}:${var.main_account_id}:function:lttm-config-transform:$LATEST",
    ]
  }
}

resource "aws_iam_role" "config_firehose_cross_account_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-config-firehose-cross-account-prod"
  assume_role_policy = data.aws_iam_policy_document.config_firehose_cross_account_prod_trust.json
}

resource "aws_iam_role_policy" "config_firehose_cross_account_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-config-firehose-cross-account-prod-policy"
  role     = aws_iam_role.config_firehose_cross_account_prod.id
  policy   = data.aws_iam_policy_document.config_firehose_cross_account_prod_policy.json
}

data "aws_iam_policy_document" "config_eventbridge_main_trust" {
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

data "aws_iam_policy_document" "config_eventbridge_main_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.main_account_id}:deliverystream/lttm-config-firehose-main"]
  }
}

resource "aws_iam_role" "config_eventbridge_main" {
  name               = "lttm-config-eventbridge-main"
  assume_role_policy = data.aws_iam_policy_document.config_eventbridge_main_trust.json
}

resource "aws_iam_role_policy" "config_eventbridge_main" {
  name   = "lttm-config-eventbridge-main-policy"
  role   = aws_iam_role.config_eventbridge_main.id
  policy = data.aws_iam_policy_document.config_eventbridge_main_policy.json
}

data "aws_iam_policy_document" "config_eventbridge_dev_trust" {
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

data "aws_iam_policy_document" "config_eventbridge_dev_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.dev_account_id}:deliverystream/lttm-config-firehose-dev"]
  }
}

resource "aws_iam_role" "config_eventbridge_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-config-eventbridge-dev"
  assume_role_policy = data.aws_iam_policy_document.config_eventbridge_dev_trust.json
}

resource "aws_iam_role_policy" "config_eventbridge_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-config-eventbridge-dev-policy"
  role     = aws_iam_role.config_eventbridge_dev.id
  policy   = data.aws_iam_policy_document.config_eventbridge_dev_policy.json
}

data "aws_iam_policy_document" "config_eventbridge_prod_trust" {
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

data "aws_iam_policy_document" "config_eventbridge_prod_policy" {
  statement {
    effect = "Allow"
    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]
    resources = ["arn:aws:firehose:${var.project_region}:${var.prod_account_id}:deliverystream/lttm-config-firehose-prod"]
  }
}

resource "aws_iam_role" "config_eventbridge_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-config-eventbridge-prod"
  assume_role_policy = data.aws_iam_policy_document.config_eventbridge_prod_trust.json
}

resource "aws_iam_role_policy" "config_eventbridge_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-config-eventbridge-prod-policy"
  role     = aws_iam_role.config_eventbridge_prod.id
  policy   = data.aws_iam_policy_document.config_eventbridge_prod_policy.json
}
