# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# outputs.tf — the main root's outputs: what the CLI, the agentcore CLI's configuration and launch, and a reader of the
# deployment need, from the lake bucket and the Firehose streams to the streaming API's URL, the Cognito IDs, the
# AgentCore memory, the guardrail, the Config snapshot delivery and the online evaluation.

output "lake_bucket_arn" {
  description = "ARN of the LTTM S3 data lake bucket"
  value       = aws_s3_bucket.lake.arn
}

output "lake_bucket_name" {
  description = "Name of the LTTM S3 data lake bucket"
  value       = aws_s3_bucket.lake.id
}

output "lake_bucket_id" {
  description = "ID (name) of the LTTM S3 data lake bucket — convenience alias for lake_bucket_name"
  value       = aws_s3_bucket.lake.id
}

output "athena_workgroup_name" {
  description = "Name of the Athena workgroup used for all LTTM queries"
  value       = aws_athena_workgroup.lttm.name
}

output "firehose_main_arn" {
  description = "ARN of the main-account Firehose delivery stream"
  value       = aws_kinesis_firehose_delivery_stream.main.arn
}

output "firehose_dev_arn" {
  description = "ARN of the dev-account Firehose delivery stream"
  value       = aws_kinesis_firehose_delivery_stream.dev.arn
}

output "firehose_prod_arn" {
  description = "ARN of the prod-account Firehose delivery stream"
  value       = aws_kinesis_firehose_delivery_stream.prod.arn
}

output "firehose_main_uswest2_arn" {
  description = "ARN of the us-west-2 Firehose delivery stream (main account)"
  value       = aws_kinesis_firehose_delivery_stream.main_uswest2.arn
}

output "cloudtrail_trail_arn" {
  description = "ARN of the CloudTrail organization trail"
  value       = aws_cloudtrail.lttm_org_trail.arn
}

output "firehose_main_useast1_arn" {
  description = "ARN of the us-east-1 Firehose delivery stream (main account)"
  value       = aws_kinesis_firehose_delivery_stream.main_useast1.arn
}

output "dns_query_log_group_arns" {
  description = "Map of hosted zone ID => CloudWatch log group ARN for Route 53 DNS query logs"
  value       = { for k, v in aws_cloudwatch_log_group.dns_query_log : k => v.arn }
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID — used in alexandra.sh for InitiateAuth and in .bedrock_agentcore.yaml discovery URL"
  value       = aws_cognito_user_pool.lttm.id
}

output "cognito_app_client_id" {
  description = "Cognito App Client ID — used in alexandra.sh for InitiateAuth and in .bedrock_agentcore.yaml allowedClients"
  value       = aws_cognito_user_pool_client.lttm_cli.id
}

output "cognito_oidc_discovery_url" {
  description = "OIDC discovery URL — used in .bedrock_agentcore.yaml authorizer_configuration"
  value       = "https://cognito-idp.${var.project_region}.amazonaws.com/${aws_cognito_user_pool.lttm.id}/.well-known/openid-configuration"
}

output "lttm_stream_api_url" {
  description = "Streaming API Gateway invoke URL — export as LTTM_STREAM_API_URL"
  value       = "${aws_api_gateway_stage.stream_prod.invoke_url}/ask"
}

output "lttm_memory_arn" {
  description = "AgentCore Memory resource ARN — set as LTTM_MEMORY_ARN in .bedrock_agentcore.yaml"
  value       = aws_bedrockagentcore_memory.lttm.arn
}

output "lttm_memory_id" {
  description = "AgentCore Memory resource ID — used in memory strategy references"
  value       = aws_bedrockagentcore_memory.lttm.id
}

output "conversations_table_arn" {
  description = "ARN of the DynamoDB conversations metadata table"
  value       = aws_dynamodb_table.conversations.arn
}

output "conversations_table_name" {
  description = "Name of the DynamoDB conversations metadata table"
  value       = aws_dynamodb_table.conversations.name
}

output "guardrail_id" {
  description = "Bedrock Guardrail ID, which agentcore deploy passes to the runtime: --env LTTM_GUARDRAIL_ID=<this>"
  value       = aws_bedrock_guardrail.lttm.guardrail_id
}

output "guardrail_version" {
  description = "The guardrail's newest published version, which agentcore deploy passes to the runtime: --env LTTM_GUARDRAIL_VERSION=<this>"
  value       = aws_bedrock_guardrail_version.lttm.version
}

output "config_snapshot_delivery" {
  description = "Config delivery channels that deliver a full snapshot to the data lake, and how often"
  value = {
    frequency = var.config_snapshot_frequency
    bucket    = var.prefix
    channels = merge(
      { "main ${var.project_region}" = aws_config_delivery_channel.main_home.name },
      { for r, c in aws_config_delivery_channel.main_multiregion : "main ${r}" => c.name },
      { "dev ${var.project_region}" = aws_config_delivery_channel.dev_home.name },
      { for r, c in aws_config_delivery_channel.dev_multiregion : "dev ${r}" => c.name },
      { "prod ${var.project_region}" = aws_config_delivery_channel.prod_home.name },
      { for r, c in aws_config_delivery_channel.prod_multiregion : "prod ${r}" => c.name },
    )
  }
}

output "evaluation_config_id" {
  description = "ID of the online evaluation configuration; null while there is none"
  value       = one(aws_bedrockagentcore_online_evaluation_config.quality[*].online_evaluation_config_id)
}

output "evaluation_results_log_group" {
  description = "Log group (us-west-2) the evaluation scores are written to; null while there is no online evaluation configuration"
  value       = try(aws_bedrockagentcore_online_evaluation_config.quality[0].output_config[0].cloudwatch_config[0].log_group_name, null)
}
