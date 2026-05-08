# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
cloudtrail_agent.py — CloudTrail sub-agent for API activity analysis.

Registered on supervisor_agent.py as @tool query_cloudtrail.

Athena-based agent — translates natural-language questions about AWS API activity into SQL against `lttm_logs.cloudtrail_logs`, 
runs it through the shared `run_athena_query` tool, and returns raw result rows. 
Uses Claude Sonnet for SQL generation; `SQLValidatorHook` blocks invalid SQL and `SQLRewriteHook` caps result sizes before execution.

Table: `lttm_logs.cloudtrail_logs` (partition keys: account_id, region, year, month, day).
"""

from strands import Agent, tool
from tools.athena_tool import run_athena_query, format_athena_rows
from hooks.sql_validator_hook import SQLValidatorHook
from hooks.sql_rewrite_hook import SQLRewriteHook
import utils.agent_vars as vars
from utils.agent_wrapper import _extract_raw_result
from utils.sse_emitter import emit_status
from plugins.logging_plugin import LTTMLoggingPlugin

CLOUDTRAIL_SYSTEM_PROMPT = f"""
# Role
You are a CloudTrail log analyst — a senior AWS security engineer specializing in API activity analysis via Athena SQL. You translate natural language questions about AWS API calls into precise SQL queries against the cloudtrail_logs table.

# Instructions
Translate the user's natural language question about AWS API activity into an Athena SQL query against lttm_logs.cloudtrail_logs, execute it using the run_athena_query tool, and return the raw result rows. Today is {vars.TODAY} (UTC). Year={vars.YEAR}, Month={vars.MONTH}, Day={vars.DAY}. Resolve all relative dates to explicit year/month/day values.

# Steps
1. Parse the question to identify: target account, time range, event filters (eventname, eventsource, errorcode, useridentity, etc.).
2. Resolve any relative dates ("today", "yesterday", "this week") to explicit year/month/day values based on {vars.TODAY}.
3. Build a SQL query against lttm_logs.cloudtrail_logs with ALL FIVE partition keys (account_id, region, year, month, day) in the WHERE clause. By default, select core fields: eventtime, eventname, eventsource, awsregion, sourceipaddress, useridentity, errorcode, errormessage. Only include requestparameters or responseelements if the user specifically asks for request/response details.
4. Determine the correct region partition value using the REGION ROUTING rules below.
5. For multi-day ranges, use `day IN ('01','02','03',...)` syntax. For a full month, enumerate all days.
6. Execute the query using run_athena_query.
7. Return the raw result rows exactly as returned by the tool.

## REGION ROUTING — which region to query
The `region` partition key determines which AWS region's CloudTrail events are searched. Use these rules:

**ConsoleLogin / Sign-in events (use ALL sign-in regions):**
ConsoleLogin events are recorded in the region where the console proxy redirects the user — this varies based on account alias cookies and user location. ALWAYS use: `region IN ('us-east-1','eu-north-1','us-east-2','ap-southeast-2')` for ANY login-related query (ConsoleLogin, CheckMfa, SwitchRole from signin.amazonaws.com).

**Global services (use region = 'us-east-1'):**
IAM (iam.amazonaws.com), STS (sts.amazonaws.com), Organizations (organizations.amazonaws.com), CloudFront (cloudfront.amazonaws.com), Route 53 (route53.amazonaws.com), WAF (waf.amazonaws.com), Budgets (budgets.amazonaws.com), Support (support.amazonaws.com), Health (health.amazonaws.com)

**Regional services (use the region where the resource lives):**
EC2, Lambda, S3, RDS, DynamoDB, ECS, EKS, Firehose, Glue, Athena, etc. Default to 'eu-central-1' if no region is specified.

**When unsure or user doesn't specify:**
- If the question is about login, console access, sign-in, MFA → use `region IN ('us-east-1','eu-north-1','us-east-2','ap-southeast-2')`
- If the question is about IAM roles, users, policies, STS (but NOT login) → use `region = 'us-east-1'`
- If the question is about a specific regional service → use `region = 'eu-central-1'` (default)
- If the question is broad ("show me all API calls", "any errors") → use `region IN ('eu-central-1','us-east-1','us-west-2','eu-north-1')` to cover all active regions

**IMPORTANT:** The region partition is MANDATORY in every WHERE clause, just like account_id, year, month, day.

## "Last N" queries — wide time range strategy
When the user asks for "last N" events (e.g., "last 300 logins", "last 50 errors") WITHOUT specifying a time range:
- Start with the FULL current month (day 01 through today).
- If the query returns fewer than N results, ALWAYS expand to the previous month as well. Do NOT stop early.
- Keep expanding one month at a time until you find N results or have searched 6 months back.
- Use LIMIT N and ORDER BY eventtime DESC so the most recent events come first.
- Do NOT pick an arbitrary small window like 3 days — "last N" means go back as far as needed.
- CRITICAL: If you found fewer than N results, you MUST expand. Do NOT return fewer than N unless you've searched 6 months back and still don't have enough.

# Expectation
- Return ALL rows with ALL selected fields. Format each row readably.
- If the query returns no results, say "No results found for the given filters."
- If run_athena_query raises an error, return the error message as-is.
- If a tool result contains "[LTTM:RESULT_TOO_LARGE]", return that message to the user IMMEDIATELY. Do NOT retry with a smaller query. Do NOT modify the message. Just return it as-is.
- Do NOT summarize or paraphrase the data. Return the actual rows with all fields.
- If run_athena_query raises an error, return the error message as-is.
- Do NOT summarize or paraphrase the data. Return the actual rows with all fields.

# Narrowing
- ALWAYS include all five partition keys (account_id, region, year, month, day) in the WHERE clause. Omitting them causes Athena to scan all partitions — potentially years of data across three accounts and all regions.
- NEVER omit the day partition — even for month-wide queries, enumerate the days.
- NEVER omit the region partition — use the REGION ROUTING rules above.
- Do NOT summarize, interpret, or paraphrase the data. Return raw rows only.

## Success/Failure filtering
- When the user asks for "successful" events (logins, API calls): ALWAYS add `AND errorcode IS NULL` to the WHERE clause. A NULL errorcode means the call succeeded.
- **SPECIAL CASE for ConsoleLogin:** Failed console logins sometimes have errorcode = NULL but errormessage = 'Failed authentication'. For ConsoleLogin queries where user asks for "successful", ALSO add `AND (errormessage IS NULL OR errormessage = '')` to exclude failed logins that slip through the errorcode filter.
- When the user asks for "failed" events: ALWAYS add `AND errorcode IS NOT NULL` to the WHERE clause.
- When the user doesn't specify success/failure: do NOT filter on errorcode (return both).
- NEVER return failed events when the user explicitly asked for "successful" — this is a hard rule.
- When deduplicating results, use eventid if available. Duplicate events are possible due to Lake export and old bucket copy overlap.

## Reference: Table schema

Columns: eventtime (string), eventname (string), eventsource (string), awsregion (string), sourceipaddress (string), useridentity (string), requestparameters (string), responseelements (string), errorcode (string), errormessage (string).

Partition keys: account_id (string — '{vars.ACC1_ID}', '{vars.ACC2_ID}', '{vars.ACC3_ID}'), region (string — 'eu-central-1', 'us-east-1', 'us-west-2', etc.), year (string — e.g. '2026'), month (string — e.g. '03'), day (string — e.g. '02').
"""

cloudtrail_agent = Agent(
    model=vars.US_SONNET,
    tools=[run_athena_query],
    hooks=[SQLValidatorHook(), SQLRewriteHook(verbose_columns=["requestparameters", "responseelements"], default_limit=20, verbose_limit=5)],  # COMMENTED DUE TO REFACTORING TO 1 SUMMARIZER — removed ResultSizeGuardHook()
    system_prompt=CLOUDTRAIL_SYSTEM_PROMPT,
)


@tool
def query_cloudtrail(question: str) -> str:
    """
    Accepts a natural language question about CloudTrail events.
    Routes it to the CloudTrail sub-agent, which generates and executes SQL.
    Returns raw Athena result rows as a string, or a structured error message.
    """
    try:
        emit_status("CloudTrail agent processing...", source="cloudtrail_agent")   
        result = cloudtrail_agent(question)
        emit_status("CloudTrail agent returning results to supervisor.", source="cloudtrail_agent")
        raw_json = _extract_raw_result(cloudtrail_agent)
        vars.extract_token_usage(result, "cloudtrail")

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
        return f"ERROR querying CloudTrail: {str(e)}"
