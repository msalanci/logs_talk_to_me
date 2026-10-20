# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# variables.tf — the inputs of the main root: the three account IDs, the Regions, the models the agents may call, the
# CLI profiles, and the settings of the lake, Config's snapshots, Cognito, the online evaluation and the streaming
# path. The values without a default come from terraform.tfvars, which stays private; terraform.tfvars.example lists
# them with placeholders.

variable "main_account_id" {
  type        = string
  description = "Main AWS account ID: the account of the lake and the rest of LTTM"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.main_account_id))
    error_message = "main_account_id must be a 12-digit AWS account ID."
  }
}

variable "dev_account_id" {
  type        = string
  description = "Dev AWS account ID: it sends its logs to the main account's lake"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.dev_account_id))
    error_message = "dev_account_id must be a 12-digit AWS account ID."
  }
}

variable "prod_account_id" {
  type        = string
  description = "Prod AWS account ID: it sends its logs to the main account's lake"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.prod_account_id))
    error_message = "prod_account_id must be a 12-digit AWS account ID."
  }
}

variable "project_region" {
  type        = string
  description = "AWS region of the project, where the lake and the data sources are"
  default     = "eu-central-1"
}

variable "global_region" {
  type        = string
  description = "AWS region of the global services, such as IAM"
  default     = "us-east-1"
}

variable "agentcore_region" {
  type        = string
  description = "AWS region of the AgentCore runtime"
  default     = "us-west-2"
}
# Models are also specified in agents/utils/agent_vars.py - make sure to update that file as well
variable "agent_models" {
  type        = list(string)
  description = "Inference profile IDs of the models the agents may call: every SUPERVISOR_MODEL, SUBAGENT_MODEL and JUDGE_MODEL in agents/utils/agent_vars.py. The agent role gets each profile's ARN and its foundation model's ARN in every Region (agents.tf)."
  default     = ["us.anthropic.claude-opus-4-6-v1", "us.anthropic.claude-haiku-4-5-20251001-v1:0"]

  validation {
    condition     = length(var.agent_models) >= 1 && alltrue([for profile in var.agent_models : can(regex("^[a-z]+\\.[a-z0-9-]+\\.", profile))])
    error_message = "Use inference profile IDs with their geography prefix, such as us.anthropic.claude-opus-4-6-v1."
  }
}

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
  description = "CLI profile to prod account"
}

variable "prefix" {
  type        = string
  description = "S3 bucket name of the lake, and the prefix of resource names. Bucket names are global: if it is taken, set another one in terraform.tfvars."
  default     = "lttm-datalake"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,33}[a-z0-9]$", var.prefix))
    error_message = "prefix must be 3 to 35 lowercase letters, digits or hyphens, start with a letter and end with a letter or digit: it is the lake bucket's name and the start of the AgentCore memory and evaluation names."
  }
}

variable "config_snapshot_frequency" {
  type        = string
  description = "Config's own periodic snapshot delivery: null (default) = off, the daily snapshot comes from var.config_snapshot_schedule instead (config_snapshot.tf); or One_Hour, Three_Hours, Six_Hours, Twelve_Hours, TwentyFour_Hours to switch Config's own cycle back on (its time of day cannot be set)"
  default     = null

  validation {
    condition     = var.config_snapshot_frequency == null || contains(["One_Hour", "Three_Hours", "Six_Hours", "Twelve_Hours", "TwentyFour_Hours"], coalesce(var.config_snapshot_frequency, "x"))
    error_message = "config_snapshot_frequency must be null, One_Hour, Three_Hours, Six_Hours, Twelve_Hours or TwentyFour_Hours."
  }
}

variable "config_snapshot_schedule" {
  type        = string
  description = "When every Config delivery channel delivers its daily snapshot (EventBridge Scheduler cron, UTC): 00:01 UTC, the end-of-day state of the day before (config_snapshot.tf). Keep it before 02:00 UTC: the snapshot Lambda files deliveries before then under the day before."
  default     = "cron(1 0 * * ? *)"

  validation {
    condition     = try(tonumber(regex("^cron\\([0-9]{1,2} ([0-9]{1,2}) [^)]+\\)$", var.config_snapshot_schedule)[0]) < 2, false)
    error_message = "config_snapshot_schedule must be a daily cron() expression with an hour of 0 or 1 UTC: the snapshot Lambda files deliveries before 02:00 UTC under the day before."
  }
}

variable "config_home_name" {
  type        = string
  description = "Name of the Config recorder, delivery channel and recorder status in the dev and prod home region"
  default     = "default"
}

variable "memory_retention_days" {
  type        = number
  description = "Number of days to retain AgentCore Memory events before automatic cleanup"
  default     = 7

  validation {
    condition     = var.memory_retention_days >= 3 && var.memory_retention_days <= 365
    error_message = "memory_retention_days must be 3 to 365 days (the AgentCore Memory API's range)."
  }
}

variable "cli_stream_runtime_arn" {
  type        = string
  description = "ARN of the AgentCore runtime that the stream shim, the health check and the online evaluation use. The default is a placeholder, so the Lambdas can be created before the agentcore CLI deploys the runtime; while it is the placeholder, evaluations.tf makes no evaluation configuration."
  default     = "arn:aws:bedrock-agentcore:us-west-2:012345678910:runtime/placeholder-stream-PlAcEhOlDeR"

  validation {
    condition     = strcontains(var.cli_stream_runtime_arn, "placeholder") || can(regex("^arn:aws[a-z-]*:bedrock-agentcore:[a-z0-9-]+:[0-9]{12}:runtime/[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10}$", var.cli_stream_runtime_arn))
    error_message = "cli_stream_runtime_arn must be an AgentCore runtime ARN (...:runtime/<name>-<10-character suffix>) or the placeholder."
  }
}

variable "dev_account_email" {
  type        = string
  description = "Email address of the dev AWS account. aws_guardduty_member requires one, though Organizations enrolls the member and sends no invitation."

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.dev_account_email))
    error_message = "dev_account_email must be an email address."
  }
}

variable "prod_account_email" {
  type        = string
  description = "Email address of the prod AWS account. aws_guardduty_member requires one, though Organizations enrolls the member and sends no invitation."

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.prod_account_email))
    error_message = "prod_account_email must be an email address."
  }
}

variable "hosted_zone_ids" {
  type        = map(string)
  description = "Map of Route 53 hosted zone ID => domain name to enable DNS query logging for. Find them with: aws route53 list-hosted-zones"

  validation {
    condition     = alltrue([for id in keys(var.hosted_zone_ids) : can(regex("^Z[A-Z0-9]{1,31}$", id))])
    error_message = "Each key of hosted_zone_ids must be a Route 53 hosted zone ID, such as Z0123456789ABCDEFGHIJ."
  }
}

variable "cognito_initial_user" {
  type        = string
  description = "Username for the initial Cognito test user"
  default     = "admin"
}

variable "cognito_initial_email" {
  type        = string
  description = "Email for the initial Cognito test user"

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.cognito_initial_email))
    error_message = "cognito_initial_email must be an email address."
  }
}

variable "lake_check_schedule" {
  type        = string
  description = "When the daily lake check runs (lake_check.tf; EventBridge Scheduler cron, UTC). After 02:00 UTC: the snapshot Lambda files a delivery before 02:00 UTC under the day before (DAY_BEFORE_UNTIL_HOUR), so only then is that day's snapshot final, the 00:01 UTC delivery (config_snapshot_schedule) included."
  default     = "cron(30 2 * * ? *)"

  validation {
    condition     = try(tonumber(regex("^cron\\([0-9]{1,2} ([0-9]{1,2}) [^)]+\\)$", var.lake_check_schedule)[0]) >= 2, false)
    error_message = "lake_check_schedule must be a daily cron() expression with an hour of 2 or later UTC: only then is the day before's snapshot final."
  }
}

variable "lake_alert_email" {
  type        = string
  description = "Where the lake's error-folder alarm emails go (alerts.tf). null (default) = the LTTM user's email, var.cognito_initial_email"
  default     = null

  validation {
    condition     = var.lake_alert_email == null || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.lake_alert_email))
    error_message = "lake_alert_email must be null or an email address."
  }
}

variable "cognito_initial_temp_password" {
  type        = string
  description = "Temporary password for the initial Cognito test user, replaced at the first sign-in: alexandra.sh asks for the new one. It must meet the pool policy: 12+ chars, upper, lower, number, symbol."
  sensitive   = true

  validation {
    condition = (
      length(var.cognito_initial_temp_password) >= 12 &&
      can(regex("[a-z]", var.cognito_initial_temp_password)) &&
      can(regex("[A-Z]", var.cognito_initial_temp_password)) &&
      can(regex("[0-9]", var.cognito_initial_temp_password)) &&
      can(regex("[^A-Za-z0-9]", var.cognito_initial_temp_password))
    )
    error_message = "cognito_initial_temp_password must meet the user pool's password policy: at least 12 characters, with an uppercase letter, a lowercase letter, a number and a symbol."
  }
}

variable "athena_bytes_scanned_cutoff_per_query" {
  description = "Most bytes one Athena query in the LTTM workgroup may scan; Athena cancels a query that would scan more. 10 GiB is twice the largest real query (4.9 GB)."
  type        = number
  default     = 10737418240

  validation {
    condition     = var.athena_bytes_scanned_cutoff_per_query >= 10485760
    error_message = "Athena's minimum per-query cutoff is 10 MB (10485760 bytes)."
  }
}

variable "evaluation_sampling_percentage" {
  description = "Share of the supervisor's questions the online evaluation scores, in percent (evaluations.tf). Each scored question is read by every evaluator, so 100 roughly doubles the cost of a question; 0 removes the evaluation configuration."
  type        = number
  default     = 10

  validation {
    condition     = var.evaluation_sampling_percentage == 0 || (var.evaluation_sampling_percentage >= 0.01 && var.evaluation_sampling_percentage <= 100)
    error_message = "Use 0 (no evaluation) or 0.01 to 100 (the API's range)."
  }
}

variable "online_evaluators" {
  description = "Evaluators the online evaluation runs on each scored question. The default is five built-in evaluators; `aws bedrock-agentcore-control list-evaluators` lists the rest."
  type        = list(string)
  default = [
    "Builtin.Helpfulness",
    "Builtin.ToolSelectionAccuracy",
    "Builtin.GoalSuccessRate",
    "Builtin.Correctness",
    "Builtin.Faithfulness",
  ]

  validation {
    condition     = length(var.online_evaluators) >= 1 && length(var.online_evaluators) <= 10
    error_message = "An online evaluation configuration takes 1 to 10 evaluators."
  }
}

variable "athena_results_retention_days" {
  description = "Days the lake keeps Athena query results (athena-results/) before S3 deletes them. The agents read each result right after its query."
  type        = number
  default     = 30

  validation {
    condition     = var.athena_results_retention_days >= 1
    error_message = "Keep results at least 1 day."
  }
}

variable "main_log_groups_not_ingested" {
  description = "Main-account eu-central-1 log groups the account subscription policy does not send to the lake. Exact names; the default is Control Tower's copy of its org trail, a duplicate of cloudtrail/. That group no longer exists; the default stays as a guard in case Control Tower's CloudTrail integration is turned on again."
  type        = list(string)
  default     = ["aws-controltower/CloudTrailLogs"]
}

variable "uswest2_telemetry_log_groups" {
  description = "us-west-2 log groups of LTTM's own telemetry that the account subscription policy does not send to the lake: the traces (aws/spans), Application Signals, and the toolkit's observability group. Keep in step with AGENT_LOG_GROUPS in agents/utils/agent_vars.py."
  type        = list(string)
  default     = ["aws/spans", "/aws/application-signals/data", "agentcore-observability"]
}

variable "stream_timeout_seconds" {
  description = "Longest answer the CLI's streaming path delivers, in seconds. One value for both limits on the path: the API Gateway /ask integration (in milliseconds) and the stream shim Lambda. 900 is the maximum of both (API Gateway streams for up to 15 minutes, a Lambda runs for up to 900 s). A stream that stays silent for 5 minutes ends earlier either way: API Gateway's idle timeout and the agent's own 300 s wait for the next event."
  type        = number
  default     = 900

  validation {
    condition     = var.stream_timeout_seconds >= 30 && var.stream_timeout_seconds <= 900
    error_message = "stream_timeout_seconds must be between 30 and 900 (the Lambda and API Gateway maximum)."
  }
}
