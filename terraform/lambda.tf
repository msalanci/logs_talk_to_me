# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# lambda.tf — Lambda functions for LTTM project
#
# List of lambda functions:
    # - lttm-invoke-agent-stream
    # - lttm-list-conversations
    # - lttm-delete-conversation
    # - lttm-health-check
    # - lttm-list-services
# =============================================================================


# lttm-invoke-agent-stream
data "aws_iam_policy_document" "lambda_stream_trust" {
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

resource "aws_iam_role" "lambda_stream" {
  name               = "lttm-lambda-stream-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_stream_trust.json
  tags               = { Project = var.prefix }
}

data "aws_iam_policy_document" "lambda_stream_permissions" {
  statement {
    sid     = "InvokeStreamAgentRuntime"
    effect  = "Allow"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      var.cli_stream_runtime_arn,
      "${var.cli_stream_runtime_arn}/runtime-endpoint/*",
    ]
  }
  statement {
    sid     = "DynamoDBConversationsWrite"
    effect  = "Allow"
    actions = ["dynamodb:UpdateItem"]
    resources = [
      aws_dynamodb_table.conversations.arn,
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
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-invoke-agent-stream*",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-invoke-agent-stream*:*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_stream" {
  name   = "lttm-lambda-stream-permissions"
  role   = aws_iam_role.lambda_stream.id
  policy = data.aws_iam_policy_document.lambda_stream_permissions.json
}

data "archive_file" "invoke_agent_stream" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/invoke_agent_stream"
  output_path = "${path.module}/lambda/invoke_agent_stream.zip"
}

resource "aws_lambda_function" "invoke_agent_stream" {
  function_name = "lttm-invoke-agent-stream"
  role          = aws_iam_role.lambda_stream.arn
  handler          = "index.handler"
  runtime          = "nodejs20.x"
  timeout          = 300
  memory_size      = 128
  filename         = data.archive_file.invoke_agent_stream.output_path
  source_code_hash = data.archive_file.invoke_agent_stream.output_base64sha256
  environment {
    variables = {
      AGENT_RUNTIME_ARN   = var.cli_stream_runtime_arn
      AGENTCORE_REGION    = var.agentcore_region
      CONVERSATIONS_TABLE = aws_dynamodb_table.conversations.name
    }
  }
  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "apigw_stream" {
  statement_id  = "AllowAPIGatewayStreamInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.invoke_agent_stream.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.lttm_stream.execution_arn}/*/*"
}


# lttm-list-conversations
data "aws_iam_policy_document" "lambda_list_conversations_trust" {
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

resource "aws_iam_role" "lambda_list_conversations" {
  name               = "lttm-lambda-list-conversations-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_list_conversations_trust.json
  tags               = { Project = var.prefix }
}

data "aws_iam_policy_document" "lambda_list_conversations_permissions" {
  statement {
    sid    = "DynamoDBConversationsRead"
    effect = "Allow"
    actions = [
      "dynamodb:Scan",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.conversations.arn,
      "${aws_dynamodb_table.conversations.arn}/index/*",
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
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-list-conversations*",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-list-conversations*:*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_list_conversations" {
  name   = "lttm-lambda-list-conversations-permissions"
  role   = aws_iam_role.lambda_list_conversations.id
  policy = data.aws_iam_policy_document.lambda_list_conversations_permissions.json
}

data "archive_file" "list_conversations" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/list_conversations"
  output_path = "${path.module}/lambda/list_conversations.zip"
}

resource "aws_lambda_function" "list_conversations" {
  function_name    = "lttm-list-conversations"
  role             = aws_iam_role.lambda_list_conversations.arn
  handler          = "index.handler"
  runtime          = "nodejs20.x"
  timeout          = 10
  memory_size      = 128
  filename         = data.archive_file.list_conversations.output_path
  source_code_hash = data.archive_file.list_conversations.output_base64sha256

  environment {
    variables = {
      CONVERSATIONS_TABLE = aws_dynamodb_table.conversations.name
    }
  }

  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "apigw_list_conversations" {
  statement_id  = "AllowAPIGatewayListConversationsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.list_conversations.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.lttm_stream.execution_arn}/*/*"
}


# lttm-delete-conversation
data "aws_iam_policy_document" "lambda_delete_conversation_trust" {
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

resource "aws_iam_role" "lambda_delete_conversation" {
  name               = "lttm-lambda-delete-conversation-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_delete_conversation_trust.json
  tags               = { Project = var.prefix }
}

data "aws_iam_policy_document" "lambda_delete_conversation_permissions" {
  statement {
    sid     = "DynamoDBConversationsDelete"
    effect  = "Allow"
    actions = ["dynamodb:DeleteItem"]
    resources = [
      aws_dynamodb_table.conversations.arn,
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
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-delete-conversation*",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-delete-conversation*:*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_delete_conversation" {
  name   = "lttm-lambda-delete-conversation-permissions"
  role   = aws_iam_role.lambda_delete_conversation.id
  policy = data.aws_iam_policy_document.lambda_delete_conversation_permissions.json
}

data "archive_file" "delete_conversation" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/delete_conversation"
  output_path = "${path.module}/lambda/delete_conversation.zip"
}

resource "aws_lambda_function" "delete_conversation" {
  function_name    = "lttm-delete-conversation"
  role             = aws_iam_role.lambda_delete_conversation.arn
  handler          = "index.handler"
  runtime          = "nodejs20.x"
  timeout          = 10
  memory_size      = 128
  filename         = data.archive_file.delete_conversation.output_path
  source_code_hash = data.archive_file.delete_conversation.output_base64sha256

  environment {
    variables = {
      CONVERSATIONS_TABLE = aws_dynamodb_table.conversations.name
    }
  }

  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "apigw_delete_conversation" {
  statement_id  = "AllowAPIGatewayDeleteConversationInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.delete_conversation.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.lttm_stream.execution_arn}/*/*"
}


# lttm-health-check
data "aws_iam_policy_document" "lambda_health_check_trust" {
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

resource "aws_iam_role" "lambda_health_check" {
  name               = "lttm-lambda-health-check-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_health_check_trust.json
  tags               = { Project = var.prefix }
}

data "aws_iam_policy_document" "lambda_health_check_permissions" {
  statement {
    sid     = "GetAgentRuntimeStatus"
    effect  = "Allow"
    actions = ["bedrock-agentcore:GetAgentRuntime"]
    resources = [
      var.cli_stream_runtime_arn,
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
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-health-check*",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-health-check*:*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_health_check" {
  name   = "lttm-lambda-health-check-permissions"
  role   = aws_iam_role.lambda_health_check.id
  policy = data.aws_iam_policy_document.lambda_health_check_permissions.json
}

data "archive_file" "health_check" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/health_check"
  output_path = "${path.module}/lambda/health_check.zip"
}

resource "aws_lambda_function" "health_check" {
  function_name    = "lttm-health-check"
  role             = aws_iam_role.lambda_health_check.arn
  handler          = "index.handler"
  runtime          = "nodejs20.x"
  timeout          = 10
  memory_size      = 128
  filename         = data.archive_file.health_check.output_path
  source_code_hash = data.archive_file.health_check.output_base64sha256

  environment {
    variables = {
      AGENT_RUNTIME_ARN = var.cli_stream_runtime_arn
    }
  }

  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "apigw_health_check" {
  statement_id  = "AllowAPIGatewayHealthCheckInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.health_check.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.lttm_stream.execution_arn}/*/*"
}


# lttm-list-services
data "aws_iam_policy_document" "lambda_list_services_trust" {
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

resource "aws_iam_role" "lambda_list_services" {
  name               = "lttm-lambda-list-services-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_list_services_trust.json
  tags               = { Project = var.prefix }
}

data "aws_iam_policy_document" "lambda_list_services_permissions" {
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-list-services*",
      "arn:aws:logs:${var.project_region}:${var.main_account_id}:log-group:/aws/lambda/lttm-list-services*:*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_list_services" {
  name   = "lttm-lambda-list-services-permissions"
  role   = aws_iam_role.lambda_list_services.id
  policy = data.aws_iam_policy_document.lambda_list_services_permissions.json
}

data "archive_file" "list_services" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/list_services"
  output_path = "${path.module}/lambda/list_services.zip"
}

resource "aws_lambda_function" "list_services" {
  function_name    = "lttm-list-services"
  role             = aws_iam_role.lambda_list_services.arn
  handler          = "index.handler"
  runtime          = "nodejs20.x"
  timeout          = 10
  memory_size      = 128
  filename         = data.archive_file.list_services.output_path
  source_code_hash = data.archive_file.list_services.output_base64sha256

  tags = { Project = var.prefix }
}

resource "aws_lambda_permission" "apigw_list_services" {
  statement_id  = "AllowAPIGatewayListServicesInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.list_services.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.lttm_stream.execution_arn}/*/*"
}
