# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
config_agent.py — AWS Config sub-agent for configuration change and inventory analysis.

Registered on supervisor_agent.py as @tool query_config.

Athena-based agent — translates natural-language questions about AWS
resource configuration into SQL against one of two tables and returns raw
result rows. Uses the sub-agent model (vars.SUBAGENT_MODEL) for table routing
and SQL generation; `SQLValidatorHook` blocks invalid SQL and `SQLRewriteHook`
caps result sizes before execution. `query_config` adds a line written by code
about where Config records (region_note).

Two tables:
- `lttm_logs.config_logs` — configuration CHANGES over time (what changed,
  when, by whom). Use for "changed", "modified", "deleted" questions.
- `lttm_logs.config_snapshot` — current INVENTORY / state (what exists
  right now). Use for "list", "all", "how many" questions.

Both tables share the same columns and partition keys
(account_id, year, month, day).
"""

import re

from strands import Agent, tool

import utils.agent_vars as vars

from tools.athena_tool import run_athena_query

from hooks.sql_validator_hook import SQLValidatorHook

from hooks.sql_rewrite_hook import SQLRewriteHook

from utils.agent_wrapper import _extract_raw_results, format_raw_results
from utils.agent_wrapper import _newest_question
from utils.sql_text import mask

from utils.delegation import delegated

from utils.sse_emitter import emit_status

CONFIG_SYSTEM_PROMPT = f"""
# Role
You are a Config log analyst — a senior AWS infrastructure engineer specializing in resource configuration change analysis via Athena SQL. You translate natural language questions about AWS resource configuration changes into precise SQL queries against Config tables.

# Instructions
Translate the user's natural language question about AWS resource configuration into an Athena SQL query, execute it using the run_athena_query tool, and return the raw result rows. Today is {{TODAY}} (UTC). Year={{YEAR}}, Month={{MONTH}}, Day={{DAY}}. Resolve all relative dates to explicit year/month/day values.

# CONFIG-SNAPSHOT: Table routing — choose the right table based on the question
You have TWO tables available. Choose the right one based on what the user is asking:

## lttm_logs.config_logs — for CHANGE and HISTORY questions
Use this table when the user asks about what CHANGED, when something was modified, deleted, or created, and for anything about the records AWS Config holds: which regions hold records, when Config recorded something, the oldest or newest record, how many configuration items.
Examples: "when did sg-abc123 last change?", "what resources were deleted last week?", "show me the config diff for this bucket", "in which regions does Config hold records?", "what is the oldest configuration item Config holds?"

## lttm_logs.config_snapshot — for INVENTORY / CURRENT STATE questions
Use this table when the user asks about what EXISTS now, how many resources there are, or wants a list of resources.
Examples: "list all S3 buckets", "how many EC2 instances in main?", "what resources exist in the dev account?", "in which regions do I have subnets?", "show me the current configuration of bucket X"

Both tables have the SAME columns and partition keys. Only the table name changes.
Choose by what the question is about, not by its wording: resources and their state now → config_snapshot, even for "which" / "list" / "how many"; Config's records themselves, and anything that changed, was deleted or modified → config_logs, even for "which" / "list" / "how many". config_snapshot holds only today's resources, from the regions Config records in today, so it cannot say where or when Config recorded something. A question about all of Config's records (which regions hold records, the oldest record) has no time limit: read every year.

# Steps
1. Parse the question to identify: target account, time range, resource type, resource ID, configuration status, and any filters on configuration fields.
2. Decide which table to use: config_logs (changes) or config_snapshot (inventory/current state).
3. Resolve any relative dates ("today", "yesterday", "this week") to explicit year/month/day values based on {{TODAY}}.
4. Map natural language resource names to AWS Config resourcetype values using the reference table below.
5. Build a SQL query against the chosen table with ALL FOUR partition keys (account_id, year, month, day) in the WHERE clause.
   - ALWAYS use `lttm_logs.config_logs` or `lttm_logs.config_snapshot` as the table name — NEVER prefix with a catalog name like `awsdatacatalog.lttm_logs.config_logs`. The two-part name is correct.
6. For config_snapshot queries: read only the NEWEST snapshot of each account. Config delivers a full snapshot once a day at 00:01 UTC, filed under the day it describes (the day before), so find each account's newest snapshot day and read only that day, as in the config_snapshot example below — never assume it is today's or yesterday's. Always SELECT that snapshot day too, so the answer says how current the inventory is. Count and list with DISTINCT resourceid: a resource can appear in more than one region's snapshot.
7. For multi-day ranges, use `day IN ('01','02','03',...)` syntax. For a full month, enumerate all days.
8. Execute the query using run_athena_query.
9. Return the raw result rows exactly as returned by the tool.

## "Last N" queries — wide time range strategy
When the user asks for "last N" events (e.g., "last 100 config changes", "last 50 security group modifications") WITHOUT specifying a time range:
- Start with the FULL current month (day 01 through today).
- If the query returns fewer than N results, expand to the previous month.
- Keep expanding one month at a time until you find N results or have searched 6 months back. Each new query covers only the older month not yet searched, again with LIMIT N and ORDER BY configurationitemcapturetime DESC: every query's rows reach the answer, so a month queried twice would show its events twice.
- Use LIMIT N and ORDER BY configurationitemcapturetime DESC so the most recent events come first.
- Do NOT pick an arbitrary small window like 3 days — "last N" means go back as far as needed.

# Expectation
- Return raw result rows from run_athena_query without summarizing or paraphrasing.
- If the query returns no results, say "No results found for the given filters."
- If run_athena_query raises an error, return the error message as-is.

# Narrowing
- ALWAYS include all four partition keys (account_id, year, month, day) in the WHERE clause. Omitting them causes Athena to scan all partitions — potentially years of data across three accounts.
- Several accounts go into ONE query: account_id IN ('...', '...'). When the answer needs a number or rows per account, SELECT account_id and GROUP BY it, so every row says its account; for one total over all accounts, don't group by account.
- One question's time range is ONE query too, also across months and years: `((year = '2026' AND month = '09' AND day IN ('29','30')) OR (year = '2026' AND month = '10' AND day IN ('01','02')))`. When the answer needs a number per day or month, SELECT year, month and day (or year and month) and GROUP BY them, so every row says what it covers; for one total over the range, don't group. A "Last N" search has no range of its own: it follows "Last N" queries above.
- NEVER omit the day partition — even for month-wide queries, enumerate the days.
- Do NOT summarize, interpret, or paraphrase the data. Return raw rows only.
- When querying the `configuration` field (which contains nested JSON), use `json_extract()` or `json_extract_scalar()` in Athena to access specific sub-fields.
- The `configurationitemdiff` field contains a JSON diff showing what changed — useful for "what changed" questions.
- The `relatedevents` field links to CloudTrail event IDs that caused the change.

## Reference: Resource type mapping

| Natural Language | AWS Config resourcetype |
|-----------------|------------------------|
| EC2 instance | AWS::EC2::Instance |
| Security group | AWS::EC2::SecurityGroup |
| S3 bucket | AWS::S3::Bucket |
| IAM role | AWS::IAM::Role |
| IAM policy | AWS::IAM::Policy |
| Lambda function | AWS::Lambda::Function |
| RDS instance | AWS::RDS::DBInstance |
| VPC | AWS::EC2::VPC |
| Subnet | AWS::EC2::Subnet |
| ELB / Load balancer | AWS::ElasticLoadBalancingV2::LoadBalancer |

## Reference: Regions

Config's recorders run today in {', '.join(vars.CONFIG_REGIONS)} (all three accounts). Region is not a partition: filter with `awsregion = '...'` when the user names a region. IAM roles, policies, users and groups have awsregion = 'global'; IAM instance profiles, Route 53 hosted zones, Organizations and ECR Public repositories appear under us-east-1. config_logs can also hold records from a region whose recorder is gone; config_snapshot never does. If the user asks about another region, check config_logs first: report the records it holds there and say that Config does not record there today; if it holds none, say that Config does not record there — not that nothing changed.

## Reference: Table schema

### config_logs (changes)
Columns: configurationitemcapturetime (string), resourcetype (string), resourceid (string), awsregion (string), availabilityzone (string), resourcecreationtime (string), configuration (string), supplementaryconfiguration (string), configurationitemstatus (string), configurationstateid (string), arn (string), awsaccountid (string), configurationitemdiff (string), relatedevents (array<string>).

Partition keys: account_id (string — '{vars.ACC1_ID}', '{vars.ACC2_ID}', '{vars.ACC3_ID}'), year (string — e.g. '2026'), month (string — e.g. '03'), day (string — e.g. '02').

### config_snapshot (current state / inventory) — CONFIG-SNAPSHOT
Same columns and partition keys as config_logs. Contains a full snapshot of ALL resources (not just changes), delivered once a day at 00:01 UTC and filed under the day it describes (the day before: the state at the end of that day). Each account's newest day is the current inventory.
Use DISTINCT on resourceid to deduplicate when listing resources.

Example — S3 buckets per account, from each account's newest snapshot:
```sql
WITH newest AS (
  SELECT account_id, max(year || month || day) AS snapshot_day
  FROM lttm_logs.config_snapshot
  WHERE account_id IN ('{vars.ACC1_ID}', '{vars.ACC2_ID}', '{vars.ACC3_ID}')
    AND year IN ('{{PREV_YEAR}}', '{{YEAR}}') AND month BETWEEN '01' AND '12' AND day BETWEEN '01' AND '31'
  GROUP BY account_id
)
SELECT s.account_id, n.snapshot_day, count(DISTINCT s.resourceid) AS buckets
FROM lttm_logs.config_snapshot s
JOIN newest n ON s.account_id = n.account_id AND s.year || s.month || s.day = n.snapshot_day
WHERE s.account_id IN ('{vars.ACC1_ID}', '{vars.ACC2_ID}', '{vars.ACC3_ID}')
  AND s.year IN ('{{PREV_YEAR}}', '{{YEAR}}') AND s.month BETWEEN '01' AND '12' AND s.day BETWEEN '01' AND '31'
  AND s.resourcetype = 'AWS::S3::Bucket'
GROUP BY s.account_id, n.snapshot_day
```

## Reference: Configuration item status values

- OK — resource exists and is recording normally
- ResourceDeleted — resource was deleted
- ResourceDiscovered — resource was newly discovered by Config
- ResourceNotRecorded — resource type is not being recorded

## Reference: Date range resolution

- "today" → year={{YEAR}}, month={{MONTH}}, day={{DAY}}
- "yesterday" → compute the previous calendar day from {{TODAY}}
- "this week" → Monday through today of the current week (ISO week)
- "last week" → Monday through Sunday of the previous ISO week
- "this month" → all days from the 1st to today in the current month
- "last month" → all days of the previous calendar month
"""

def _new_config_agent() -> Agent:
    """Build a fresh Config sub-agent for one question.

    A new agent per question carries no earlier question's history, and does not clash with a
    parallel call of the same tool.

    Args:
        None.

    Returns:
        A new Agent on the sub-agent model (built without the CountTokens call), with run_athena_query,
        SQLValidatorHook and SQLRewriteHook (20 rows by default), and the prompt filled with today's date.
    """
    return Agent(
        model=vars.build_bedrock_model(vars.SUBAGENT_MODEL),
        tools=[run_athena_query],
        hooks=[SQLValidatorHook(), SQLRewriteHook(default_limit=20)],
        system_prompt=vars.with_current_date(CONFIG_SYSTEM_PROMPT),
    )


_REGION_FILTER = re.compile(r"\bawsregion\s*(=|\bIN\b)\s*", re.IGNORECASE)
_LITERAL = re.compile(r"'([^']*)'")


def _athena_calls(agent) -> list[tuple[str, str, str]]:
    """List every run_athena_query call of the agent's newest question, in order.

    Args:
        agent: The Config sub-agent after it answered (anything with a `messages` list).

    Returns:
        (SQL, status, result text) for each call that has a result; an empty list when no query ran.
    """
    sql_by_id, calls = {}, []
    for msg in _newest_question(agent):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            use = item.get("toolUse") if msg.get("role") == "assistant" else None
            if use and use.get("name") == "run_athena_query":
                sql_by_id[use.get("toolUseId")] = (use.get("input") or {}).get("sql", "")
            result = item.get("toolResult") if msg.get("role") == "user" else None
            if result and result.get("toolUseId") in sql_by_id:
                text = "\n".join(block["text"] for block in result.get("content", [])
                                 if isinstance(block, dict) and block.get("text"))
                calls.append((sql_by_id[result["toolUseId"]], result.get("status", "success"), text))
    return calls


def _filtered_regions(sql: str) -> set[str]:
    """Find the regions named by the query's `awsregion = '…'` and `awsregion IN ('…', …)` filters.

    Found in the masked SQL, so a filter inside a string literal or a comment does not count; NOT IN does not either.

    Args:
        sql: The query's SQL.

    Returns:
        The region names; an empty set when the query has no such filter.
    """
    regions = set()
    for match in _REGION_FILTER.finditer(mask(sql)):
        rest = sql[match.end():]
        if match.group(1) == "=":
            literal = _LITERAL.match(rest)
            if literal:
                regions.add(literal.group(1))
        elif rest.startswith("(") and ")" in rest:
            regions |= set(_LITERAL.findall(rest[1:rest.index(")")]))
    return regions


def region_note(calls: list[tuple[str, str, str]]) -> str:
    """Write the line that says what is true about where Config records.

    The supervisor sees only what code writes, never a sub-agent's own text, so it is the only one who explains;
    given "No results found." alone, it guessed a cause ("may not be enabled", "no changes occurred"). It holds no
    region list of its own either: with one, it answered without asking. So it gets a fact to repeat instead of a
    gap to explain:
    - a successful query filtered awsregion to a region where Config has no recorder today (not in CONFIG_REGIONS,
      not the global resources' "global"): "these queries found no records in ..." when every query came back empty
      for such regions only; otherwise "any records from there come from a recorder that is no longer there";
    - every query succeeded and came back empty, for regions Config records in or with no region filter: its
      recorders run there (the regions the queries named, when every query named some), and Config holds no record
      that matches.
    Rows from regions Config records in need no line. The line says "recorders run in", never "AWS Config records
    in …", which was read as the noun "records"; and it ends with what an empty answer means, so there is no gap
    left to explain.

    Args:
        calls: (SQL, status, result text) of every query of the question, as _athena_calls returns them.

    Returns:
        The line, starting with "Note (written by code):"; "" when no line is needed.
    """
    succeeded = [(sql, text) for sql, status, text in calls if status != "error"]
    recorded = set(vars.CONFIG_REGIONS) | {vars.CONFIG_GLOBAL_REGION}
    filtered = [_filtered_regions(sql) for sql, _ in succeeded]
    other = sorted({region for regions in filtered for region in regions if region not in recorded})
    recorders = ", ".join(vars.CONFIG_REGIONS)
    all_empty = bool(succeeded) and len(succeeded) == len(calls) and all(text.strip() == "[]" for _, text in succeeded)
    if other:
        names = ", ".join(other)
        if all_empty and all(regions and not regions & recorded for regions in filtered):
            return (f"Note (written by code): these queries found no records in {names}. AWS Config does not record in "
                    f"{names}: its recorders run in {recorders} (all three accounts).")
        return (f"Note (written by code): AWS Config does not record in {names} today: its recorders run in {recorders} "
                f"(all three accounts). Any records from {names} come from a recorder that is no longer there.")
    if not all_empty:
        return ""
    found_nothing = "and these queries found no records: AWS Config holds no record that matches them."
    if all(filtered):
        asked = set().union(*filtered)
        where = [f"run in {', '.join(sorted(asked - {vars.CONFIG_GLOBAL_REGION}))}"] if asked - {vars.CONFIG_GLOBAL_REGION} else []
        where += ["cover the global resources (IAM)"] if vars.CONFIG_GLOBAL_REGION in asked else []
        return f"Note (written by code): AWS Config's recorders {' and '.join(where)} for all three accounts, {found_nothing}"
    return f"Note (written by code): AWS Config's recorders run in {recorders} for all three accounts, {found_nothing}"


@tool
def query_config(question: str) -> str:
    """
    Accepts a natural language question about AWS Config data.
    Routes it to the Config sub-agent, which generates and executes SQL.
    Returns raw Athena result rows as a string, or a structured error message.
    """
    try:
        emit_status("Config agent processing...", source="config_agent")
        agent = _new_config_agent()
        result = agent(delegated(question, "config"))
        emit_status("Config agent returning results to supervisor.", source="config_agent")

        raw_results = _extract_raw_results(agent)
        vars.extract_token_usage(result, "config")
        if not raw_results:
            return str(result)

        text = format_raw_results(raw_results)
        note = region_note(_athena_calls(agent))
        return f"{text}\n\n{note}" if note else text
    except Exception as e:
        return f"ERROR querying Config: {str(e)}"
