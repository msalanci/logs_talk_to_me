# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# Project name
project_name = "<project_name>" # Ex: lttm-cia

# AWS Accounts
main_account_id = "<main_accound_id>"
dev_account_id  = "<dev_account_id>"
prod_account_id = "<prod_account_id>"

# AWS Account emails, required by the aws_guardduty_member API even with Organizations auto-enable.
dev_account_email  = "<example1@mydomain.com>"
prod_account_email = "<example2@mydomain.com>"

# AWS regions
project_region   = "<aws_project_region>" # AWS Region where the project is (lambda functions, etc...) - in my case it was eu-central-1
global_region    = "us-east-1"            # AWS Region for global services like IAM, etc... - should be us-east-1
agentcore_region = "<agentcore_region>"   # AWS Region where you run AgentCore - in my case it was us-west-2

# AWS Profiles
main_profile = "<main_accound_cli_profile>"
dev_profile  = "<dev_accound_cli_profile>"
prod_profile = "<prod_accound_cli_profile>"

# S3 Datalake bucket
prefix = "<my_datalake_bucket>" # Datalake where Athena based datasources (cloudtrail, config, cloudwatch...) will send logs

# Route 53 hosted zones to enable DNS query logging for.
# Map of zone_id => domain_name. All zones in the main account.
# If you have no zones, delete it or comment it our - project should work without it
hosted_zone_ids = {
  "<zone_ID_1>" = "domain_1"
  "<zone_ID_2>" = "domain_2"
}

# Cognito initial user
# Temporary password — user must change on first login via alexandra.sh.
cognito_initial_user          = "<username>"
cognito_initial_email         = "example@mydomain.com"
cognito_initial_temp_password = "<MyP4ssR0rd$%#>"

# AgentCore vars
memory_retention_days  = 7

# IMPORTANT!
# Keep this placeholder before deploying afgentcore, otherwise you own't be able to deploy lambda functions
# Once terraform is deployed with this AND AgentCore is configured and deployes, get agentcore runtime ID and stream runtime ID, place it here instead of placeholders and re-deploy terraform again
# cli_runtime_arn        = "arn:aws:bedrock-agentcore:us-west-2:012345678910:runtime/placeholder_placeholder-PlaCEhol0der"
# cli_stream_runtime_arn = "arn:aws:bedrock-agentcore:us-west-2:012345678910:runtime/placeholder_placeholder_stream-PlaCEhol0der"
