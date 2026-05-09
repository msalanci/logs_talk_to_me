# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

# AWS Accounts
variable "main_account_id" {
  type        = string
  description = "Main AWS account ID"
}
variable "dev_account_id" {
  type        = string
  description = "Dev AWS account ID"
}
variable "prod_account_id" {
  type        = string
  description = "Prod AWS account ID"
}

# AWS regions
variable "project_region" {
  type        = string
  description = "AWS region of the project"
}
variable "global_region" {
  type        = string
  description = "AWS region of the global services"
}
variable "agentcore_region" {
  type        = string
  description = "AWS region of AgentCore"
}

# AWS profiles
variable "main_profile" {
  type        = string
  description = "CLI profile to main account"
}
variable "dev_profile" {
  type        = string
  description = "CLI profile to dev account"
}
variable "prod_profile" {
  type        = string
  description = "CLI profile to dev account"
}

# S3 bucket prefix
variable "prefix" {
  type        = string
  description = "S3 bucket name and resource prefix"
  default     = "lttm-datalake"
}

# Agentcore variables
variable "memory_retention_days" {
  type        = number
  description = "Number of days to retain AgentCore Memory events before automatic cleanup"
  default     = 7
}
variable "cli_runtime_arn" {
  type        = string
  description = "agentocre runtime ID - placeholder used before agentcore is created, so lambda functons can be created"
  default     = "arn:aws:bedrock-agentcore:us-west-2:012345678910:runtime/placeholder-PLACEHOLDER-PlAcEhOlDeR"
}
variable "cli_stream_runtime_arn" {
  type        = string
  description = "agentocre runtime ID - placeholder used before agentcore is created, so lambda functons can be created"
  default     = "arn:aws:bedrock-agentcore:us-west-2:012345678910:runtime/placeholder-stream-PlAcEhOlDeR"
}

# AWS Coount emails
variable "dev_account_email" {
  type        = string
  description = "Email address associated with the dev AWS account"
}
variable "prod_account_email" {
  type        = string
  description = "Email address associated with the prod AWS account"
}

# Route53 hosted zones
variable "hosted_zone_ids" {
  type        = map(string)
  description = "Map of Route 53 hosted zone ID => domain name to enable DNS query logging for. Find them with: aws route53 list-hosted-zones"
}

# Cognito variables
variable "cognito_initial_user" {
  type        = string
  description = "Username for the initial Cognito test user"
  default     = "admin"
}
variable "cognito_initial_email" {
  type        = string
  description = "Email for the initial Cognito test user"
}
variable "cognito_initial_temp_password" {
  type        = string
  description = "Temporary password for the initial Cognito test user (must meet pool policy: 12+ chars, upper, lower, number, symbol)"
  sensitive   = true
}

# Project name
variable "project_name" {
  type        = string
  description = "Name of the project"
}
