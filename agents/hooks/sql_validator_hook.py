# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sql_validator_hook.py — Deterministic SQL validation hook for Athena sub-agents.

Registered in each Athena sub-agent (cloudtrail, cloudwatch, config, cur, flowlogs, dns, etc...) 
as hook SQLValidatorHook.
Fires BEFORE SQLRewriteHook so invalid SQL is rejected before LIMIT enforcement even runs.

Implements deterministic checks, that the LLM cannot bypass, leaving  `run_athena_query` 
a pure execute-only tool, while validation lives here as a guardrail layer.

Rules (table names, partition keys, blocked keywords) come from agent_vars.py
"""

import re
from typing import Any
from strands.hooks import (
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)
import utils.agent_vars as vars

def validate_sql(sql: str) -> list[str]:
    """
    Validate SQL against LTTM table rules and returns list of error strings (empty = valid).
    
    Runs five deterministic checks in sequence: 
    1. no `awsdatacatalog.` prefix,
    2. no blocked SQL keywords (DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE),
    3. fully qualified `lttm_logs.<table>` reference, all required partition
    4. keys present in WHERE, and no SELECT *. Rules come from
    5. `utils/agent_vars.py` (`TABLES`, `BLOCKED_SQL_KEYWORDS`).

    Args:
        sql: 
            The SQL statement to validate. 
            Typically extracted from`event.tool_use["input"]["sql"]` inside a `BeforeToolCallEvent`handler, 
            but the function is usable standalone for unit tests.

    Returns:
        List of error message strings, one per failed check. 
        An empty list means all checks passed and the SQL is safe to execute. 
        Error messages are user-facing text formatted for the LLM to read and fix on retry.
    """

    errors = []
    sql_lower = sql.lower().strip()
    sql_upper = sql.upper().strip()

    # Check 1: no awsdatacatalog prefix
    if "awsdatacatalog." in sql_lower:
        errors.append("Remove 'awsdatacatalog.' prefix — use 'lttm_logs.<table>' directly")

    # Check 2: blocked SQL keywords
    for kw in vars.BLOCKED_SQL_KEYWORDS:
        if re.search(rf'\b{kw}\b', sql_upper):
            errors.append(f"Blocked keyword '{kw}' — only SELECT queries are allowed")

    # Check 3: identify which table is being queried
    matched_table = None
    for table_name in vars.TABLES:
        if table_name in sql_lower:
            matched_table = table_name
            break

    if not matched_table:
        for table_name in vars.TABLES:
            bare_name = table_name.split(".")[1] if "." in table_name else table_name
            if bare_name in sql_lower and "lttm_logs." not in sql_lower:
                errors.append(f"Use fully qualified table name: '{table_name}' (not just '{bare_name}')")
                matched_table = table_name
                break

    if not matched_table:
        return errors

    # Check 4: if all required partition keys are in WHERE clause
    required_keys = vars.TABLES.get(matched_table, [])
    where_match = re.search(r'\bwhere\b(.+?)(?:\border\b|\bgroup\b|\blimit\b|\bhaving\b|$)',
                            sql_lower, re.DOTALL)
    if where_match:
        where_clause = where_match.group(1)
        missing_keys = [key for key in required_keys if key not in where_clause]
        if missing_keys:
            errors.append(f"Missing required partition keys in WHERE: {', '.join(missing_keys)}")
    else:
        if required_keys:
            errors.append(f"No WHERE clause — required partition keys: {', '.join(required_keys)}")

    # Check 5: no SELECT *
    if re.search(r'\bselect\s+\*\s+from\b', sql_lower):
        errors.append("Use explicit column names instead of SELECT *")

    return errors


class SQLValidatorHook(HookProvider):
    """
    Deterministic guardrail blocking bad SQL before Athena ever sees it.

    From every `run_athena_query` call, it extracts the SQL from `event.tool_use` and runs it through `validate_sql`. 

    If any rule fails (forbidden prefix, blocked keyword, unknown table, missing partition key, or SELECT *), the tool call is cancelled with 
    `event.cancel_tool = <error message>` and the LLM receives the error text  as the tool result, so it can fix the SQL and retry. 
    
    If all rules pass, the call goes through untouched.

    The SQL checks live in`validate_sql` function so they can be unit-tested independently.
    """

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the SQL validation callback for BeforeToolCallEvent."""
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)

    def on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Validate the SQL passed to run_athena_query; cancel the tool call with an error if invalid."""
        if event.tool_use.get("name") != "run_athena_query":
            return

        sql = event.tool_use.get("input", {}).get("sql", "")
        if not sql:
            return

        errors = validate_sql(sql)

        if errors:
            msg = f"SQL validation failed: {'; '.join(errors)}. Fix and retry."
            print(f"[LTTM:SQLValidator] BLOCKED — {msg}", flush=True)
            event.cancel_tool = msg
        else:
            print(f"[LTTM:SQLValidator] PASSED — {sql[:100]}...", flush=True)
