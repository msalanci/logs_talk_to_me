# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
cloudwatch_agent.py — CloudWatch sub-agent for application log analysis.

Registered on supervisor_agent.py as @tool query_cloudwatch.

Athena-based agent — translates natural-language questions about application
logs into SQL against `lttm_logs.cloudwatch_logs`, runs it through the shared
`run_athena_query` tool, and returns raw result rows. A second `@tool`
(`list_log_groups`) lists the log-group folders of the datalake S3 bucket to
discover available log groups when the user does not specify one explicitly.
It lists with the delimiter "/year=", not "/", because log group names contain "/":
S3 then returns each (log group, account) folder once, as a CommonPrefix, and one
request covers 1,000 folders instead of listing every file. Several log groups go
into one query (`log_group IN (...)`). Uses the sub-agent model (vars.SUBAGENT_MODEL)
for SQL generation; `SQLValidatorHook` blocks invalid SQL and `SQLRewriteHook` caps
result sizes before execution.

`query_cloudwatch` passes on the queries' rows and adds lines written by code
(utils/empty_note.py), so these facts are never left to the sub-agent's own text: a
region the question names whose logs are not collected, LTTM's own log groups (never
collected), whether each log group asked is in the lake at all, and, after a log-group
lookup with no query, what that list holds. Rows of an account that collects none of
the regions asked are dropped: the table has no region column, so they could only be
logs of its other regions.

Table: `lttm_logs.cloudwatch_logs` (partition keys: log_group, account_id,
year, month — no day partition; a single query per month covers all days).
"""

import json

import boto3
from strands import Agent, tool

import utils.agent_vars as vars

from tools.athena_tool import run_athena_query

from hooks.sql_validator_hook import SQLValidatorHook, is_agent_log_group

from hooks.sql_rewrite_hook import SQLRewriteHook

from utils.agent_wrapper import _extract_raw_results, format_raw_results
from utils.empty_note import (NOTE, all_empty, asked_log_groups, athena_calls, cloudwatch_note, cloudwatch_region_note,
                              listed_groups_note, own_log_groups_named, own_logs_note, uncollected_accounts,
                              with_note, without_accounts)

from utils.delegation import delegated

from utils.sse_emitter import emit_status

CLOUDWATCH_SYSTEM_PROMPT = f"""
# Role
You are a CloudWatch log analyst — a senior AWS application engineer specializing in log analysis via Athena SQL. You translate natural language questions about application logs into precise SQL queries against the cloudwatch_logs table.

# Instructions
Translate the user's natural language question about application logs into an Athena SQL query against lttm_logs.cloudwatch_logs, execute it using the run_athena_query tool, and return the raw result rows. Today is {{TODAY}} (UTC). Year={{YEAR}}, Month={{MONTH}}, Day={{DAY}}. Resolve all relative dates to explicit year/month values.

# Steps
1. Parse the question to identify: target account, time range, log group (exact or partial name), and message filters.
2. If the user does not specify an exact log group name, call list_log_groups() first to discover available log groups, then pick the closest match. It returns {{account_id: [log group names]}}: use only the names listed under the account you query.
3. Resolve any relative dates ("today", "yesterday", "this month") to explicit year/month values based on {{TODAY}}.
4. Build a SQL query against lttm_logs.cloudwatch_logs with log_group, account_id, year, and month in the WHERE clause. Several log groups or months go into the SAME query with IN (...) — see "Several log groups: one query".
5. Execute the query using run_athena_query.
6. Return the raw result rows exactly as returned by the tool.

## Several log groups: one query
When the question covers more than one log group ("Lambda logs" = every /aws/lambda/... group that list_log_groups() lists for the account; "errors in A and B"), put them ALL in one query: log_group IN ('a', 'b', ...). A time range that spans two months uses month IN ('09', '10'). Several accounts go into the same query too: account_id IN ('...', '...'). NEVER write one query per log group, per month or per account — every extra query is another tool call the user waits for.

Always SELECT and GROUP BY account_id as well, so every result row says which account it belongs to — the rows are all the caller gets.

Results are capped at 20 rows, so make these two calls together, in the same turn:
1. One row per account and log group (so every log group is counted, however many there are) — for errors, for example:
```sql
SELECT account_id, log_group, count(*) AS events,
       max(from_unixtime(e.timestamp / 1000)) AS last_seen,
       max_by(e.message, e.timestamp) AS latest_message
FROM lttm_logs.cloudwatch_logs
CROSS JOIN UNNEST(logevents) AS t(e)
WHERE account_id IN ('{vars.ACC1_ID}', '{vars.ACC2_ID}') AND year = '2026' AND month IN ('09', '10')
  AND log_group IN ('/aws/lambda/LTTMMainFunction', '/aws/lambda/lttm-invoke-agent-stream')
  AND from_unixtime(e.timestamp / 1000) >= TIMESTAMP '2026-09-25 00:00:00'
  AND regexp_like(e.message, '(?i)error|exception|fatal|traceback')
GROUP BY account_id, log_group
ORDER BY events DESC
```
2. The 20 most recent matching events across all of them: the same FROM and WHERE, with SELECT account_id, log_group, from_unixtime(e.timestamp / 1000) AS event_time, e.message — ORDER BY e.timestamp DESC LIMIT 20.

Change the message filter to fit the question. A log group with no matching event does not appear in the results. Route 53 query logs (/aws/route53/...) say NOERROR on every successful lookup, so a text filter for "error" matches them: leave them out of an error search, or filter them by response code (see the DNS reference below).

## "Last N" queries — wide time range strategy
When the user asks for "last N" events (e.g., "last 100 Lambda errors", "last 50 log entries") WITHOUT specifying a time range:
1. Query the FULL current month, ORDER BY e.timestamp DESC LIMIT N.
2. If it returned fewer than N rows, run ONE more query over the previous 5 months together (month IN (...); across a new year use (year = '2025' AND month IN (...)) OR (year = '2026' AND month IN (...))), again ORDER BY e.timestamp DESC LIMIT N, and combine the two.
- Never stop after step 1 when it returned fewer than N rows, and never answer "only X found" before step 2 has run.
- Do NOT pick an arbitrary small window like 3 days — "last N" means go back as far as needed.

# Expectation
- Return raw result rows from run_athena_query without summarizing or paraphrasing.
- If the query returns no results, say "No results found for the given filters."
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
- log_group MUST ALWAYS be in the WHERE clause. The partition projection type is "injected" — Athena raises a hard error if log_group is omitted. This is not optional. Give exact names: log_group = '...' for one, log_group IN ('...', '...') for several. LIKE does not work on it.
- Do NOT guess or fabricate log group names. If list_log_groups() returns no match, say so.
- LTTM's own logs and traces are not available to questions: every log group under {vars.AGENT_LOG_GROUP_PREFIX}, and {', '.join(vars.AGENT_LOG_GROUPS)} (also written with a leading "/"). If the user asks for one of them, do not query it: answer that it is LTTM's own log or trace data and is not available. Never say it does not exist.
- Do NOT summarize, interpret, or paraphrase the data. Return raw rows only.
- No day-level partition exists for CloudWatch — one query covers every day of the months it names (filter days with e.timestamp).

## Reference: Regions

Logs reach the lake only from {', '.join(vars.CLOUDWATCH_REGIONS)}: {vars.DEFAULT_REGION} carries the logs of all three accounts and us-east-1 the main account's Route 53 query logs. us-west-2 holds only LTTM's own logs and traces, which are not collected. The table has no region column. If the user asks about logs in another region, say that its logs are not collected — not that nothing happened.

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

Partition keys: log_group (string — MANDATORY, injected type, requires exact names in WHERE: = or IN (...), never LIKE), account_id (string — '{vars.ACC1_ID}', '{vars.ACC2_ID}', '{vars.ACC3_ID}'), year (string — e.g. '2026'), month (string — e.g. '03').

## Reference: Route 53 DNS Query Log Format

DNS query logs appear under log groups named `/aws/route53/<zone-id>`. When the user asks about DNS queries, domain lookups, NXDOMAIN errors, or DNS resolution:
1. Call list_log_groups() first to discover which `/aws/route53/...` log groups exist.
2. Use exact names on log_group (e.g. `log_group = '/aws/route53/{vars.DNS_EXAMPLE_ZONE_IDS[0]}'`). Do NOT use LIKE — log_group is an injected partition that requires exact names.
3. If multiple DNS log groups exist and the user doesn't specify a zone, put them all in one query with log_group IN (...).

The `logevents` column is an `array<struct<timestamp:bigint,message:string>>`. To access individual DNS log entries, use CROSS JOIN UNNEST:

```sql
SELECT
  e.message,
  split_part(e.message, ' ', 4) AS query_name,
  split_part(e.message, ' ', 5) AS query_type,
  split_part(e.message, ' ', 6) AS response_code
FROM lttm_logs.cloudwatch_logs
CROSS JOIN UNNEST(logevents) AS t(e)
WHERE log_group = '/aws/route53/{vars.DNS_EXAMPLE_ZONE_IDS[0]}'
  AND account_id = '{vars.ACC1_ID}'
  AND year = '2026' AND month = '03'
```

DNS query log message fields (space-delimited, by position — split_part counts from 1):
1=version (1.0), 2=timestamp, 3=hosted-zone-id, 4=query-name (domain), 5=query-type (A/AAAA/CNAME/MX), 6=response-code (NOERROR/NXDOMAIN/SERVFAIL), 7=protocol (UDP/TCP), 8=edge-location, 9=resolver-ip, 10=EDNS client subnet (or -)
Example line: 1.0 2026-10-01T04:28:17Z Z0123456789EXAMPLE www.example.com A NXDOMAIN UDP IAD12-R4 192.0.2.10 198.51.100.0/24

## Reference: DNS questions, as SQL

The most queried domain names on one day, over every Route 53 log group of the account (the names come from list_log_groups()):
```sql
SELECT account_id, split_part(e.message, ' ', 4) AS query_name, count(*) AS queries
FROM lttm_logs.cloudwatch_logs
CROSS JOIN UNNEST(logevents) AS t(e)
WHERE account_id = '{vars.ACC1_ID}' AND year = '2026' AND month = '03'
  AND log_group IN ('/aws/route53/{vars.DNS_EXAMPLE_ZONE_IDS[0]}', '/aws/route53/{vars.DNS_EXAMPLE_ZONE_IDS[1]}')
  AND from_unixtime(e.timestamp / 1000) >= TIMESTAMP '2026-03-15 00:00:00'
  AND from_unixtime(e.timestamp / 1000) < TIMESTAMP '2026-03-16 00:00:00'
GROUP BY account_id, split_part(e.message, ' ', 4)
ORDER BY queries DESC
```
Responses per hosted zone, for example NXDOMAIN: the same FROM and WHERE, AND split_part(e.message, ' ', 6) = 'NXDOMAIN', with SELECT account_id, log_group, count(*) AS nxdomain, GROUP BY account_id, log_group and ORDER BY nxdomain DESC.

## Reference: Log group families in this lake

- /aws/lambda/<function>: a Lambda function's logs (the lines below, and whatever the function prints).
- /aws/route53/<hosted zone ID>: the DNS query logs of a Route 53 public hosted zone (us-east-1, the main account).
- /API-Gateway-Execution-Logs_<API ID>/<stage>: the execution logs of an API Gateway REST API stage.
- /aws/apigateway/welcome: written once by API Gateway when its logging is set up for the account.
- /aws/guardduty/malware-scan-events: GuardDuty's malware scan results.

## Reference: Lambda's own log lines

Every Lambda function's log group holds lines that Lambda itself writes, with their fields separated by tabs:
- `START RequestId: <id> Version: $LATEST`: an invocation began.
- `END RequestId: <id>`: it ended.
- `REPORT RequestId: <id>  Duration: 68375.22 ms  Billed Duration: 69052 ms  Memory Size: 128 MB  Max Memory Used: 104 MB  Init Duration: 676.46 ms`: one line per invocation, so counting REPORT lines counts invocations. Init Duration appears only on a cold start.
- `INIT_START Runtime Version: python:3.12...`: a new execution environment started (a cold start).
- A line with `Task timed out after`: the invocation reached its timeout.
- The function's own lines: Node.js functions write `<time>  <RequestId>  <LEVEL>  <message>` (LEVEL is INFO, WARN or ERROR); Python's default logger writes `[LEVEL]  <time>  <RequestId>  <message>`; errors of the runtime itself start with `[ERROR] [<epoch ms>] LAMBDA_RUNTIME`.

Invocations, the longest duration, the most memory used and the cold starts per function, from the REPORT lines:
```sql
SELECT account_id, log_group, count(*) AS invocations,
       max(CAST(regexp_extract(e.message, 'Max Memory Used: (\\d+) MB', 1) AS integer)) AS max_memory_mb,
       max(CAST(regexp_extract(e.message, 'Duration: ([0-9.]+) ms', 1) AS double)) AS max_duration_ms,
       count_if(e.message LIKE '%Init Duration%') AS cold_starts
FROM lttm_logs.cloudwatch_logs
CROSS JOIN UNNEST(logevents) AS t(e)
WHERE account_id = '{vars.ACC1_ID}' AND year = '2026' AND month = '10'
  AND log_group IN ('/aws/lambda/lttm-lake-check', '/aws/lambda/lttm-invoke-agent-stream')
  AND e.message LIKE 'REPORT RequestId%'
GROUP BY account_id, log_group
```
"""

def _groups_in_lake(groups: list[str], accounts: list[str]) -> dict | None:
    """Find, for each log group, the accounts (of those asked) whose logs of it are in the lake.

    One S3 listing per pair, MaxKeys 1, under the same folders list_log_groups() reads. An empty
    answer uses it to say, for each log group asked, whether it is in the lake at all.

    Args:
        groups: The log groups the queries named.
        accounts: The accounts the queries asked about.

    Returns:
        {log group: [accounts with its logs in the lake]}; None when no log group is named, or when
        S3 cannot be read (logged): the note then says only what the queries found.
    """
    if not groups:
        return None
    try:
        s3 = boto3.client("s3", region_name=vars.DEFAULT_REGION)
        return {group: [account for account in accounts
                        if s3.list_objects_v2(Bucket=vars.DATALAKE_BUCKET, MaxKeys=1,
                                              Prefix=f"cloudwatch/log_group={group}/account_id={account}/")
                        .get("KeyCount", 0)] for group in groups}
    except Exception as e:
        print(f"[LTTM:CloudWatch] Log groups not checked in the lake: {type(e).__name__}: {e}", flush=True)
        return None


@tool
def list_log_groups() -> dict[str, list[str]]:
    """
    Discovers available CloudWatch log groups from the data lake's log-group folders,
    per account: returns {account_id: [log group names]}. Lists folder names, not every file.
    """
    from utils.sse_emitter import emit_status

    emit_status("Discovering CloudWatch log groups...", source="cloudwatch_agent")
    print("[LTTM:CloudWatch] list_log_groups called — listing log-group folders", flush=True)
    s3 = boto3.client("s3", region_name=vars.DEFAULT_REGION)
    prefix = "cloudwatch/log_group="
    marker = "/account_id="
    delimiter = "/year="
    paginator = s3.get_paginator("list_objects_v2")
    by_account = {}
    for page in paginator.paginate(Bucket=vars.DATALAKE_BUCKET, Prefix=prefix, Delimiter=delimiter):
        for folder in page.get("CommonPrefixes", []):
            name, _, account = folder["Prefix"][len(prefix):-len(delimiter)].rpartition(marker)
            if is_agent_log_group(name):
                continue
            by_account.setdefault(account, set()).add(name)
    result = {account: sorted(names) for account, names in sorted(by_account.items())}
    print(f"[LTTM:CloudWatch] list_log_groups found {len(set().union(*by_account.values()))} log groups "
          f"in {len(result)} accounts", flush=True)
    return result


def _new_cloudwatch_agent() -> Agent:
    """Build a fresh CloudWatch sub-agent for one question.

    A new agent per question carries no earlier question's history, and does not clash with a
    parallel call of the same tool. The prompt is cached (vars.cached_system_prompt): Bedrock caches
    it only above Opus 4.6's minimum of 4,096 tokens, and its "Reference: DNS questions, as SQL", "Log
    group families in this lake" and "Lambda's own log lines" sections are what take it past that, so
    a shorter prompt silently stops being cached.

    Args:
        None.

    Returns:
        A new Agent on the sub-agent model (built without the CountTokens call), with run_athena_query
        and list_log_groups, SQLValidatorHook and SQLRewriteHook (20 rows by default), and the cached
        prompt filled with today's date.
    """
    return Agent(
        model=vars.build_bedrock_model(vars.SUBAGENT_MODEL),
        tools=[run_athena_query, list_log_groups],
        hooks=[SQLValidatorHook(), SQLRewriteHook(default_limit=20)],
        system_prompt=vars.cached_system_prompt(vars.with_current_date(CLOUDWATCH_SYSTEM_PROMPT)),
    )


def _with_log_group_counts(text: str) -> str:
    """Put a count line, computed here, before a log-group list ({account_id: [names]}).

    The supervisor miscounted such a list when it counted it itself. The line says the counts are
    of the lake, and a note after the list says a deleted log group stays in the lake: otherwise the
    supervisor reads the list as the log groups the accounts have now.

    Args:
        text: One tool result's text.

    Returns:
        The count line, the list and the note, when the text is a log-group list; any other result
        unchanged.
    """
    try:
        groups = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if not isinstance(groups, dict) or not all(isinstance(names, list) for names in groups.values()):
        return text
    per_account = ", ".join(f"{len(names)} in {account} ({vars.ACCOUNTS[account]['label']})" if account in vars.ACCOUNTS
                            else f"{len(names)} in {account}" for account, names in groups.items())
    return (f"Log groups in the lake: {per_account}; {sum(len(names) for names in groups.values())} in total\n\n{text}"
            f"\n\n{NOTE} these are the log groups whose logs the lake holds, per account; a log group stays in the lake "
            f"after it is deleted in CloudWatch, so this is not the list of log groups the accounts have now.")


def _listing(raw_results: list[str]) -> dict | None:
    """Find the log-group list among a question's tool results.

    Args:
        raw_results: The texts of the question's tool results, in order.

    Returns:
        The first result that is a log-group list ({account_id: [names]}); None when there is none.
    """
    for text in raw_results:
        try:
            groups = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(groups, dict) and all(isinstance(names, list) for names in groups.values()):
            return groups
    return None


@tool
def query_cloudwatch(question: str) -> str:
    """
    Accepts a natural language question about CloudWatch log messages.
    Routes it to the CloudWatch sub-agent, which generates and executes SQL.
    Returns raw Athena result rows as a string, or a structured error message.
    """
    try:
        emit_status("CloudWatch agent processing...", source="cloudwatch_agent")
        agent = _new_cloudwatch_agent()
        result = agent(delegated(question, "cloudwatch"))
        emit_status("CloudWatch agent returning results to supervisor.", source="cloudwatch_agent")

        raw_results = _extract_raw_results(agent, discovery_tools=("list_log_groups",))
        vars.extract_token_usage(result, "cloudwatch")
        calls = athena_calls(agent)
        region_line = cloudwatch_region_note(question, calls)
        if not raw_results:
            own = own_log_groups_named(question)
            if own and not region_line:
                region_line = own_logs_note(own)
            return f"No results found.\n{region_line}" if region_line else str(result)
        raw_results = without_accounts(raw_results, uncollected_accounts(question, calls))

        lake = _groups_in_lake(*asked_log_groups(calls)) if all_empty(calls) else None
        text = with_note(format_raw_results([_with_log_group_counts(text) for text in raw_results]),
                         cloudwatch_note(calls, lake))
        listing = None if calls else _listing(raw_results)
        if listing is not None:
            named, none_in_lake = listed_groups_note(question, listing)
            own = own_log_groups_named(question)
            if own:
                named = "\n".join(line for line in (named, own_logs_note(own)) if line)
            text = with_note(f"No results found.\n\n{text}" if none_in_lake or own else text, named)
        return with_note(text, region_line)
    except Exception as e:
        return f"ERROR querying CloudWatch: {str(e)}"
