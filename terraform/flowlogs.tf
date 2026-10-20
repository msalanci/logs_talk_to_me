# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# flowlogs.tf — a flow log to the lake (flowlogs/, Parquet) for every VPC of local.flow_log_targets. Flow logs have no
# organization-wide setting, so the Lambda lttm-flowlog-enforcer gives each VPC without one a flow log: every 15
# minutes, and at once on a CreateVpc in the main account's project Region. In dev and prod it works through a role
# of its own there. The flow logs Terraform made before stay in place (the removed blocks) and count as present.

locals {
  flow_log_destination = "arn:aws:s3:::${var.prefix}/flowlogs/"

  # Where every VPC gets a flow log: the accounts and regions the flow-log table covers
  # (FLOWLOGS_REGIONS in agents/utils/agent_vars.py). An empty role means the Lambda's own account.
  flow_log_targets = [
    { account = var.main_account_id, region = var.project_region, role = "" },
    { account = var.main_account_id, region = var.agentcore_region, role = "" },
    { account = var.dev_account_id, region = var.project_region, role = aws_iam_role.flowlog_enforcer_dev.arn },
    { account = var.prod_account_id, region = var.project_region, role = aws_iam_role.flowlog_enforcer_prod.arn },
  ]

  # The logs:*LogDelivery actions are required for an S3 destination too.
  flow_log_actions = [
    "ec2:DescribeVpcs",
    "ec2:DescribeFlowLogs",
    "ec2:CreateFlowLogs",
    "ec2:CreateTags",
    "logs:CreateLogDelivery",
    "logs:DeleteLogDelivery",
  ]
}

removed {
  from = aws_flow_log.main_eu
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_flow_log.main_uswest2
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_flow_log.dev_eu
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_flow_log.prod_eu
  lifecycle {
    destroy = false
  }
}

data "archive_file" "flowlog_enforcer" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/flowlog_enforcer"
  output_path = "${path.module}/lambda/flowlog_enforcer.zip"
  excludes    = ["__pycache__", "__pycache__/**"]
}

resource "aws_lambda_function" "flowlog_enforcer" {
  function_name    = "lttm-flowlog-enforcer"
  role             = aws_iam_role.flowlog_enforcer.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 128
  filename         = data.archive_file.flowlog_enforcer.output_path
  source_code_hash = data.archive_file.flowlog_enforcer.output_base64sha256

  environment {
    variables = {
      FLOW_LOG_DESTINATION = local.flow_log_destination
      TARGETS              = jsonencode(local.flow_log_targets)
    }
  }
}

data "aws_iam_policy_document" "flowlog_enforcer_trust" {
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

resource "aws_iam_role" "flowlog_enforcer" {
  name               = "lttm-flowlog-enforcer-role"
  assume_role_policy = data.aws_iam_policy_document.flowlog_enforcer_trust.json
}

data "aws_iam_policy_document" "flowlog_enforcer" {
  statement {
    sid       = "FlowLogsHere"
    effect    = "Allow"
    actions   = local.flow_log_actions
    resources = ["*"]
  }

  statement {
    sid     = "FlowLogsInDevAndProd"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    resources = [
      aws_iam_role.flowlog_enforcer_dev.arn,
      aws_iam_role.flowlog_enforcer_prod.arn,
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

resource "aws_iam_role_policy" "flowlog_enforcer" {
  name   = "lttm-flowlog-enforcer-policy"
  role   = aws_iam_role.flowlog_enforcer.id
  policy = data.aws_iam_policy_document.flowlog_enforcer.json
}

resource "aws_cloudwatch_event_rule" "flowlog_enforcer_schedule" {
  name                = "lttm-flowlog-enforcer-schedule"
  description         = "Gives every LTTM VPC a flow log to the data lake, as flow logs have no organization-wide setting"
  schedule_expression = "rate(15 minutes)"
}

resource "aws_cloudwatch_event_rule" "flowlog_enforcer_create_vpc" {
  name        = "lttm-flowlog-enforcer-create-vpc"
  description = "A new VPC gets its flow log at once"
  event_pattern = jsonencode({
    source        = ["aws.ec2"]
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["ec2.amazonaws.com"]
      eventName   = ["CreateVpc", "CreateDefaultVpc"]
    }
  })
}

resource "aws_cloudwatch_event_target" "flowlog_enforcer_schedule" {
  rule = aws_cloudwatch_event_rule.flowlog_enforcer_schedule.name
  arn  = aws_lambda_function.flowlog_enforcer.arn
}

resource "aws_cloudwatch_event_target" "flowlog_enforcer_create_vpc" {
  rule = aws_cloudwatch_event_rule.flowlog_enforcer_create_vpc.name
  arn  = aws_lambda_function.flowlog_enforcer.arn
}

resource "aws_lambda_permission" "flowlog_enforcer_schedule" {
  statement_id  = "AllowScheduleInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.flowlog_enforcer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.flowlog_enforcer_schedule.arn
}

resource "aws_lambda_permission" "flowlog_enforcer_create_vpc" {
  statement_id  = "AllowCreateVpcInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.flowlog_enforcer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.flowlog_enforcer_create_vpc.arn
}

data "aws_iam_policy_document" "flowlog_enforcer_cross_account_trust" {
  statement {
    sid     = "AllowFlowLogEnforcerAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.flowlog_enforcer.arn]
    }
  }
}

data "aws_iam_policy_document" "flowlog_enforcer_cross_account" {
  statement {
    sid       = "FlowLogsHere"
    effect    = "Allow"
    actions   = local.flow_log_actions
    resources = ["*"]
  }
}

resource "aws_iam_role" "flowlog_enforcer_dev" {
  provider           = aws.dev_eucentral1
  name               = "LTTMFlowLogEnforcerRole"
  assume_role_policy = data.aws_iam_policy_document.flowlog_enforcer_cross_account_trust.json
}

resource "aws_iam_role_policy" "flowlog_enforcer_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-flowlog-enforcer"
  role     = aws_iam_role.flowlog_enforcer_dev.id
  policy   = data.aws_iam_policy_document.flowlog_enforcer_cross_account.json
}

resource "aws_iam_role" "flowlog_enforcer_prod" {
  provider           = aws.prod_eucentral1
  name               = "LTTMFlowLogEnforcerRole"
  assume_role_policy = data.aws_iam_policy_document.flowlog_enforcer_cross_account_trust.json
}

resource "aws_iam_role_policy" "flowlog_enforcer_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-flowlog-enforcer"
  role     = aws_iam_role.flowlog_enforcer_prod.id
  policy   = data.aws_iam_policy_document.flowlog_enforcer_cross_account.json
}
