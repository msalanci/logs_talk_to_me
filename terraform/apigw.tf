# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# apigw.tf — REST API Gateway for LTTM v3 streaming endpoint
# =============================================================================


resource "aws_api_gateway_rest_api" "lttm_stream" {
  name        = "lttm-stream-api"
  description = "LTTM streaming endpoint — bypasses 29s HTTP API timeout via Lambda Response Streaming"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = { Project = var.prefix }
}

resource "aws_api_gateway_resource" "stream_root" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  parent_id   = aws_api_gateway_rest_api.lttm_stream.root_resource_id
  path_part   = "ask"
}

resource "aws_api_gateway_method" "stream_post" {
  rest_api_id   = aws_api_gateway_rest_api.lttm_stream.id
  resource_id   = aws_api_gateway_resource.stream_root.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "stream" {
  rest_api_id             = aws_api_gateway_rest_api.lttm_stream.id
  resource_id             = aws_api_gateway_resource.stream_root.id
  http_method             = aws_api_gateway_method.stream_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.invoke_agent_stream.response_streaming_invoke_arn
  response_transfer_mode  = "STREAM"
  timeout_milliseconds    = 300000
}

resource "aws_api_gateway_method_response" "stream_post_200" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.stream_root.id
  http_method = aws_api_gateway_method.stream_post.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "stream_post" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.stream_root.id
  http_method = aws_api_gateway_method.stream_post.http_method
  status_code = aws_api_gateway_method_response.stream_post_200.status_code

  depends_on = [aws_api_gateway_integration.stream]
}

resource "aws_api_gateway_resource" "conversations" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  parent_id   = aws_api_gateway_rest_api.lttm_stream.root_resource_id
  path_part   = "conversations"
}

resource "aws_api_gateway_method" "conversations_get" {
  rest_api_id   = aws_api_gateway_rest_api.lttm_stream.id
  resource_id   = aws_api_gateway_resource.conversations.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "conversations_get" {
  rest_api_id             = aws_api_gateway_rest_api.lttm_stream.id
  resource_id             = aws_api_gateway_resource.conversations.id
  http_method             = aws_api_gateway_method.conversations_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.list_conversations.invoke_arn
}

resource "aws_api_gateway_resource" "conversations_id" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  parent_id   = aws_api_gateway_resource.conversations.id
  path_part   = "{id}"
}

resource "aws_api_gateway_method" "conversations_delete" {
  rest_api_id   = aws_api_gateway_rest_api.lttm_stream.id
  resource_id   = aws_api_gateway_resource.conversations_id.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "conversations_delete" {
  rest_api_id             = aws_api_gateway_rest_api.lttm_stream.id
  resource_id             = aws_api_gateway_resource.conversations_id.id
  http_method             = aws_api_gateway_method.conversations_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.delete_conversation.invoke_arn
}

resource "aws_api_gateway_method_response" "conversations_get_200" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.conversations.id
  http_method = aws_api_gateway_method.conversations_get.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "conversations_get" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.conversations.id
  http_method = aws_api_gateway_method.conversations_get.http_method
  status_code = aws_api_gateway_method_response.conversations_get_200.status_code

  depends_on = [aws_api_gateway_integration.conversations_get]
}

resource "aws_api_gateway_method_response" "conversations_delete_200" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.conversations_id.id
  http_method = aws_api_gateway_method.conversations_delete.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "conversations_delete" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.conversations_id.id
  http_method = aws_api_gateway_method.conversations_delete.http_method
  status_code = aws_api_gateway_method_response.conversations_delete_200.status_code

  depends_on = [aws_api_gateway_integration.conversations_delete]
}

resource "aws_api_gateway_resource" "health" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  parent_id   = aws_api_gateway_rest_api.lttm_stream.root_resource_id
  path_part   = "health"
}

resource "aws_api_gateway_method" "health_get" {
  rest_api_id   = aws_api_gateway_rest_api.lttm_stream.id
  resource_id   = aws_api_gateway_resource.health.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "health_get" {
  rest_api_id             = aws_api_gateway_rest_api.lttm_stream.id
  resource_id             = aws_api_gateway_resource.health.id
  http_method             = aws_api_gateway_method.health_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.health_check.invoke_arn
}

resource "aws_api_gateway_resource" "services" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  parent_id   = aws_api_gateway_rest_api.lttm_stream.root_resource_id
  path_part   = "services"
}

resource "aws_api_gateway_method" "services_get" {
  rest_api_id   = aws_api_gateway_rest_api.lttm_stream.id
  resource_id   = aws_api_gateway_resource.services.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "services_get" {
  rest_api_id             = aws_api_gateway_rest_api.lttm_stream.id
  resource_id             = aws_api_gateway_resource.services.id
  http_method             = aws_api_gateway_method.services_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.list_services.invoke_arn
}

resource "aws_api_gateway_method_response" "health_get_200" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.health.id
  http_method = aws_api_gateway_method.health_get.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "health_get" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.health.id
  http_method = aws_api_gateway_method.health_get.http_method
  status_code = aws_api_gateway_method_response.health_get_200.status_code

  depends_on = [aws_api_gateway_integration.health_get]
}

resource "aws_api_gateway_method_response" "services_get_200" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.services.id
  http_method = aws_api_gateway_method.services_get.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "services_get" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id
  resource_id = aws_api_gateway_resource.services.id
  http_method = aws_api_gateway_method.services_get.http_method
  status_code = aws_api_gateway_method_response.services_get_200.status_code

  depends_on = [aws_api_gateway_integration.services_get]
}

resource "aws_api_gateway_deployment" "stream" {
  rest_api_id = aws_api_gateway_rest_api.lttm_stream.id

  depends_on = [
    aws_api_gateway_method.stream_post,
    aws_api_gateway_integration.stream,
    aws_api_gateway_integration_response.stream_post,
    aws_api_gateway_method.conversations_get,
    aws_api_gateway_integration.conversations_get,
    aws_api_gateway_integration_response.conversations_get,
    aws_api_gateway_method.conversations_delete,
    aws_api_gateway_integration.conversations_delete,
    aws_api_gateway_integration_response.conversations_delete,
    aws_api_gateway_method.health_get,
    aws_api_gateway_integration.health_get,
    aws_api_gateway_integration_response.health_get,
    aws_api_gateway_method.services_get,
    aws_api_gateway_integration.services_get,
    aws_api_gateway_integration_response.services_get,
  ]

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.stream_root.id,
      aws_api_gateway_method.stream_post.id,
      aws_api_gateway_integration.stream.id,
      aws_api_gateway_authorizer.cognito.id,
      aws_api_gateway_resource.conversations.id,
      aws_api_gateway_method.conversations_get.id,
      aws_api_gateway_integration.conversations_get.id,
      aws_api_gateway_resource.conversations_id.id,
      aws_api_gateway_method.conversations_delete.id,
      aws_api_gateway_integration.conversations_delete.id,
      aws_api_gateway_resource.health.id,
      aws_api_gateway_method.health_get.id,
      aws_api_gateway_integration.health_get.id,
      aws_api_gateway_resource.services.id,
      aws_api_gateway_method.services_get.id,
      aws_api_gateway_integration.services_get.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "stream_prod" {
  deployment_id = aws_api_gateway_deployment.stream.id
  rest_api_id   = aws_api_gateway_rest_api.lttm_stream.id
  stage_name    = "prod"
  tags          = { Project = var.prefix }
}
