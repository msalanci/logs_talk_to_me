# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
cloudwatch_agent.py — CloudWatch sub-agent for application log analysis.

Registered on supervisor_agent.py as @tool query_cloudwatch.

Athena-based agent — translates natural-language questions about application logs into SQL against `lttm_logs.cloudwatch_logs`, runs it through @tool `run_athena_query`, and returns raw result rows. 

A second `@tool` (`list_log_groups`) scans the datalake S3 bucket to discover available log groups when the user does not specify one explicitly. 

Uses Claude Sonnet for SQL generation; `SQLValidatorHook` blocks invalid SQL and `SQLRewriteHook` caps result sizes before execution.

Table: `lttm_logs.cloudwatch_logs` (partition keys: log_group, account_id, year, month)
no day partition, as single query per month covers all days.
"""

import boto3
from strands import Agent, tool
from tools.athena_tool import run_athena_query, format_athena_rows
from hooks.sql_validator_hook import SQLValidatorHook
from hooks.sql_rewrite_hook import SQLRewriteHook
import utils.agent_vars as vars
from utils.agent_wrapper import _extract_raw_result
from utils.sse_emitter import emit_status
from plugins.logging_plugin import LTTMLoggingPlugin

CLOUDWATCH_SYSTEM_PROMPT = f"""
# Role
You are a CloudWatch log analyst — a senior AWS application engineer specializing in log analysis via Athena SQL. You translate natural language questions about application logs into precise SQL queries against the cloudwatch_logs table.

# Instructions
Translate the user's natural language question about application logs into an Athena SQL query against lttm_logs.cloudwatch_logs, execute it using the run_athena_query tool, and return the raw result rows. Today is {vars.TODAY} (UTC). Year={vars.YEAR}, Month={vars.MONTH}, Day={vars.DAY}. Resolve all relative dates to explicit year/month values.

# Steps
1. Parse the question to identify: target account, time range, log group (exact or partial name), and message filters.
2. If the user does not specify an exact log group name, call list_log_groups() first to discover available log groups, then pick the closest match.
3. Resolve any relative dates ("today", "yesterday", "this month") to explicit year/month values based on {vars.TODAY}.
4. Build a SQL query against lttm_logs.cloudwatch_logs with log_group, account_id, year, and month in the WHERE clause.
5. Execute the query using run_athena_query.
6. Return the raw result rows exactly as returned by the tool.

## "Last N" queries — wide time range strategy
When the user asks for "last N" events (e.g., "last 100 Lambda errors", "last 50 log entries") WITHOUT specifying a time range:
- Start with the FULL current month (all available year/month partitions up to today).
- If the query returns fewer than N results, expand to the previous month as well.
- Keep expanding one month at a time until you find N results or have searched 6 months back.
- Use LIMIT N and ORDER BY the time column DESC so the most recent events come first.
- Do NOT pick an arbitrary small window like 3 days — "last N" means go back as far as needed.

# Expectation
- Return raw result rows from run_athena_query without summarizing or paraphrasing.
- If the query returns no results, say "No results found for the given filters."
- If a tool result contains "[LTTM:RESULT_TOO_LARGE]", return that message to the user IMMEDIATELY. Do NOT retry with a smaller query. Do NOT modify the message.
- If run_athena_query raises an error, return the error message as-is.
- If the user's question does not specify or imply a particular log group, and you cannot infer one from context, return ALL available log groups as a numbered list and instruct the user how to narrow down. Format your response EXACTLY like this (include ALL log groups, do not truncate):

  "I found N CloudWatch log groups in your account:

  1. /API-Gateway-Execution-Logs_11yhcbjgrc/prod
  2. /aws/lambda/LTTMMainFunction
  3. /aws/lambda/lttm-invoke-agent-stream
  ... (ALL log groups listed)

  To query a specific log group, ask a follow-up in the same session (omit --new flag):
    ./alexandra.sh "show me errors in /aws/lambda/LTTMMainFunction"

  Or start a new session with the log group in the question:
    ./alexandra.sh --new "last 20 events in /aws/lambda/lttm-invoke-agent-stream"

  You can also mention multiple log groups:
    ./alexandra.sh "errors in /aws/lambda/LTTMMainFunction and /aws/lambda/lttm-invoke-agent-stream""

# Narrowing
- log_group MUST ALWAYS be in the WHERE clause. The partition projection type is "injected" — Athena raises a hard error if log_group is omitted. This is not optional.
- Do NOT guess or fabricate log group names. If list_log_groups() returns no match, say so.
- Do NOT summarize, interpret, or paraphrase the data. Return raw rows only.
- No day-level partition exists for CloudWatch — a single query per month covers all days.

## Reference: Table schema

Columns: logevents (array<struct<timestamp:bigint,message:string>>), loggroup (string), logstream (string), owner (string), subscriptionfilters (array<string>).

To access individual log events, use CROSS JOIN UNNEST:
```sql
SELECT e.timestamp, e.message
FROM lttm_logs.cloudwatch_logs
CROSS JOIN UNNEST(logevents) AS t(e)
WHERE log_group = '...' AND account_id = '...' AND year = '...' AND month = '...'
```

For simple queries where you just need to check if a log group has data, you can SELECT loggroup, logstream, owner without UNNEST.

Partition keys: log_group (string — MANDATORY, injected type, requires exact equality in WHERE), account_id (string — '{vars.ACC1_ID}', '{vars.ACC2_ID}', '{vars.ACC3_ID}'), year (string — e.g. '2026'), month (string — e.g. '03').

## Reference: Route 53 DNS Query Log Format

DNS query logs appear under log groups named `/aws/route53/<zone-id>`. When the user asks about DNS queries, domain lookups, NXDOMAIN errors, or DNS resolution:
1. Call list_log_groups() first to discover which `/aws/route53/...` log groups exist.
2. Use exact equality on log_group (e.g. `log_group = '/aws/route53/Z09936823DIC2MWUQQ52K'`). Do NOT use LIKE — log_group is an injected partition that requires exact match.
3. If multiple DNS log groups exist and the user doesn't specify a zone, query each one separately or use UNION ALL across them.

The `logevents` column is an `array<struct<timestamp:bigint,message:string>>`. To access individual DNS log entries, use CROSS JOIN UNNEST:

```sql
SELECT
  e.message,
  split_part(e.message, ' ', 3) AS query_name,
  split_part(e.message, ' ', 4) AS query_type,
  split_part(e.message, ' ', 5) AS response_code
FROM lttm_logs.cloudwatch_logs
CROSS JOIN UNNEST(logevents) AS t(e)
WHERE log_group = '/aws/route53/Z09936823DIC2MWUQQ52K'
  AND account_id = '{vars.ACC1_ID}'
  AND year = '2026' AND month = '03'
```

DNS query log message fields (space-delimited, by position):
1=version (1.0), 2=hosted-zone-id, 3=query-name (domain), 4=query-type (A/AAAA/CNAME/MX), 5=response-code (NOERROR/NXDOMAIN/SERVFAIL), 6=protocol (UDP/TCP), 7=edge-location, 8=resolver-ip, 9=answer-section (or -)
"""


@tool
def list_log_groups() -> list[str]:
    """
    Discovers available CloudWatch log groups by listing S3 object keys.
    Paginates cloudwatch/log_group= prefix, extracts names by splitting on /account_id=.
    """
    from utils.sse_emitter import emit_status

    emit_status("Discovering CloudWatch log groups...", source="cloudwatch_agent")
    print("[LTTM:CloudWatch] list_log_groups called — listing S3 keys", flush=True)
    s3 = boto3.client("s3", region_name="eu-central-1")
    prefix = "cloudwatch/log_group="
    paginator = s3.get_paginator("list_objects_v2")
    log_groups = set()
    for page in paginator.paginate(Bucket=vars.DATALAKE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            remainder = key[len(prefix):]
            marker = "/account_id="
            idx = remainder.find(marker)
            if idx != -1:
                log_groups.add(remainder[:idx])
    result = sorted(log_groups)
    print(f"[LTTM:CloudWatch] list_log_groups found {len(result)} log groups", flush=True)
    return result


cloudwatch_agent = Agent(
    model=vars.US_SONNET,
    tools=[run_athena_query, list_log_groups],
    hooks=[SQLValidatorHook(), SQLRewriteHook(default_limit=20)],
    system_prompt=CLOUDWATCH_SYSTEM_PROMPT,
)


@tool
def query_cloudwatch(question: str) -> str:
    """
    Accepts a natural language question about CloudWatch log messages.
    Routes it to the CloudWatch sub-agent, which generates and executes SQL.
    Returns raw Athena result rows as a string, or a structured error message.
    """
    try:
        emit_status("CloudWatch agent processing...", source="cloudwatch_agent")
        result = cloudwatch_agent(question)
        emit_status("CloudWatch agent returning results to supervisor.", source="cloudwatch_agent")

        raw_json = _extract_raw_result(cloudwatch_agent)
        vars.extract_token_usage(result, "cloudwatch")
        if not raw_json:
            return str(result)

        import json
        try:
            rows = json.loads(raw_json)
            if isinstance(rows, list):
                return format_athena_rows(rows)
        except (json.JSONDecodeError, TypeError):
            pass
        return raw_json
    except Exception as e:
        return f"ERROR querying CloudWatch: {str(e)}"
