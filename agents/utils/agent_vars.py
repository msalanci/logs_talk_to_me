# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
agent_vars.py — Centralized variables for all LTTM agents and tools.

Every value that was previously hardcoded across multiple agent/tool fileslives here. 

To change any value: edit this file, redeploy (agentcore launch).

IMPORTANT INFORMATION FOR USER - Please fill up following sections:
    - AWS Accounts
    - DATALAKE_BUCKET 
"""

import os
from datetime import datetime, timezone

# AWS Accounts
ACC1_ID = "<PUT_ACCOUNT-1_NUMBER_HERE>" # Example: 012345678910
ACC1_LABEL = "<PUT_ACCOUNT-1_NAME_HERE>" # Example: main
ACC1_EMAIL = "<PUT_ACCOUNT-1_EMAIL_HERE>" # Example: myemail@somedomain.com

ACC2_ID = "<PUT_ACCOUNT-2_NUMBER_HERE>"
ACC2_LABEL = "<PUT_ACCOUNT-2_NAME_HERE>"
ACC2_EMAIL = "<PUT_ACCOUNT-2_EMAIL_HERE>"

ACC3_ID = "<PUT_ACCOUNT-3_NUMBER_HERE>"
ACC3_LABEL = "<PUT_ACCOUNT-3_NAME_HERE>"
ACC3_EMAIL = "<PUT_ACCOUNT-3_EMAIL_HERE>"

# AWS Account dict — used by API-based tools for STS cross-account role assumption
ACCOUNTS = {
    ACC1_ID: {"label": ACC1_LABEL, "email": ACC1_EMAIL},
    ACC2_ID: {"label": ACC2_LABEL, "email": ACC2_EMAIL},
    ACC3_ID: {"label": ACC3_LABEL, "email": ACC3_EMAIL},
}

# LLM Models
US_SONNET = "us.anthropic.claude-sonnet-4-20250514-v1:0"  # Claude Sonnet 4 in us-west-2
# US_NOVA_PRO = "us.amazon.nova-pro-v1:0"                 # Amazon Nova Pro in us-west-2
# US_NOVA_LITE = "us.amazon.nova-lite-v1:0"               # Amazon Nova Lite in us-west-2
US_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # Claude Haiku 4.5 in us-west-2

# Defaults
DEFAULT_ACCOUNT = ACC1_ID
DEFAULT_REGION = "eu-central-1"
DATALAKE_BUCKET = "<YOUR_BUCKET_NAME>"

# Date/time
_NOW = datetime.now(timezone.utc)
TODAY = _NOW.strftime("%Y-%m-%d")
YEAR = _NOW.strftime("%Y")
MONTH = _NOW.strftime("%m")
DAY = _NOW.strftime("%d")

# Cross-account role template
CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTM{service}ReadRole"

# SQL Guardrails
## used by sql_validator_hook.py
## exactly what glue creates
## if new table is added to glue, it must be hardcoded here
TABLES = {
    "lttm_logs.cloudtrail_logs": ["account_id", "region", "year", "month", "day"],
    "lttm_logs.cloudwatch_logs": ["log_group", "account_id", "year", "month"],
    "lttm_logs.config_logs": ["account_id", "year", "month", "day"],
    "lttm_logs.config_snapshot": ["account_id", "year", "month", "day"],
    "lttm_logs.cur_data": ["billing_period"],
    "lttm_logs.flowlogs": ["aws_account_id", "aws_region", "year", "month", "day"],
    "lttm_logs.guardduty_findings": ["account_id", "year", "month", "day"],
}

BLOCKED_SQL_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE"]

# Limits
MAX_RESULT_CHARS = 50000

# Bedrock Managed Guardrail
GUARDRAIL_ID = os.environ.get("LTTM_GUARDRAIL_ID")
GUARDRAIL_VERSION = os.environ.get("LTTM_GUARDRAIL_VERSION")

# Token Cost Attribution
def extract_token_usage(result, agent_name: str) -> dict:
    """Extract and log token usage from a Strands AgentResult.

    Args:
        result: The AgentResult returned by agent(question).
        agent_name: Human-readable name for logging (e.g., "cloudtrail", "supervisor").

    Returns:
        Dict with inputTokens, outputTokens, totalTokens (all int, 0 on failure).
    """
    usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
    try:
        if hasattr(result, "metrics") and result.metrics is not None:
            acc = getattr(result.metrics, "accumulated_usage", None)
            if acc and isinstance(acc, dict):
                usage["inputTokens"] = acc.get("inputTokens", 0)
                usage["outputTokens"] = acc.get("outputTokens", 0)
                usage["totalTokens"] = acc.get("totalTokens", 0)
                print(
                    f"[LTTM:Tokens] {agent_name} — "
                    f"input={usage['inputTokens']}, "
                    f"output={usage['outputTokens']}, "
                    f"total={usage['totalTokens']}",
                    flush=True,
                )
    except Exception as e:
        print(f"[LTTM:Tokens] {agent_name} — extraction failed: {e}", flush=True)
    return usage

# Internal Names
## used by ArchitectureGuardHook to detect leaked internals
INTERNAL_NAMES = [
    # Hooks, tools, plugins, classes and function names
    "query_cloudtrail", "query_cloudwatch", "query_config", "query_access_analyzer", "query_health", "query_cur",
    "query_organizations", "query_quotas", "query_flowlogs","query_guardduty","run_athena_query", "run_subagent", 
    "query_access_analyzer_api", "query_health_api", "query_organizations_api", "query_quotas_api", 
    "query_guardduty_findings", "query_macie", "query_macie_findings",
    "query_inspector", "query_inspector_findings",
    "SQLValidatorHook", "SQLRewriteHook", "ResultSizeGuardHook", 
    "OutputIntegrityHook", "ArchitectureGuardHook", "SupervisorSteeringHandler", "LTTMLoggingPlugin", 
    "LTTMMemoryHook", "_extract_raw_result", "emit_status", "emit_result", "emit_error", "emit_done", 
    "BedrockModel", "BedrockAgentCoreApp",

    # Project files
    ".py", "sql_validator_hook", "sql_rewrite_hook", "result_size_guard_hook", "output_integrity_hook", 
    "architecture_guard_hook", "supervisor_steering", "logging_plugin", "athena_tool", "access_analyzer_tool", 
    "health_tool", "organizations_tool", "quotas_tool", "guardduty_tool", "macie_tool", "inspector_tool", "agent_wrapper", "agent_vars", 
    "sse_emitter", "supervisor_agent", "cloudtrail_agent", "cloudwatch_agent", "config_agent", "cur_agent", 
    "flowlogs_agent", "guardduty_agent", "macie_agent", "inspector_agent", "health_agent", "organizations_agent", "quotas_agent", 
    "access_analyzer_agent",
    
    # Variables
    "SUPERVISOR_SYSTEM_PROMPT", "CLOUDTRAIL_SYSTEM_PROMPT","CLOUDWATCH_SYSTEM_PROMPT", "CONFIG_SYSTEM_PROMPT",
    "CUR_SYSTEM_PROMPT", "FLOWLOGS_SYSTEM_PROMPT", "GUARDDUTY_SYSTEM_PROMPT", "MACIE_SYSTEM_PROMPT",
    "INSPECTOR_SYSTEM_PROMPT", "HEALTH_SYSTEM_PROMPT",
    "ACCESS_ANALYZER_SYSTEM_PROMPT", "ORGANIZATIONS_SYSTEM_PROMPT", "QUOTAS_SYSTEM_PROMPT","ROUTING_JUDGE_PROMPT", 
    "INTEGRITY_JUDGE_PROMPT","JUDGE_SYSTEM_PROMPT", "ACC1_ID", "ACC1_LABEL", "ACC1_EMAIL", "ACC2_ID", "ACC2_LABEL", 
    "ACC2_EMAIL", "ACC3_ID", "ACC3_LABEL", "ACC3_EMAIL", "US_SONNET", "US_HAIKU", "DEFAULT_ACCOUNT", 
    "DEFAULT_REGION", "DATALAKE_BUCKET", "CROSS_ACCOUNT_ROLE_TEMPLATE", "BLOCKED_SQL_KEYWORDS", "MAX_RESULT_CHARS",
    "GUARDRAIL_ID", "GUARDRAIL_VERSION", "INTERNAL_NAMES",    
    # Removed from table, due to false positives: "ACCOUNTS", "TABLES"
]
