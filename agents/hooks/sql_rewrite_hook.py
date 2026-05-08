# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sql_rewrite_hook.py — Deterministic SQL rewriting hook for Athena sub-agents.

Registered in every Athena sub-agent (cloudtrail, cloudwatch, config, cur,
flowlogs, dns) as hook SQLRewriteHook.
Fires after SQLValidatorHook so validation runs first, then rewriting.

Deterministic regex — no LLM calls

"""

import re
from typing import Any
from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)
from utils.sse_emitter import emit_status


class SQLRewriteHook(HookProvider):
    """
    Rewrite sub-agent SQL to enforce LIMIT and block wasteful retries.

    Three-event contract per invocation:

    BeforeInvocationEvent — clears smart-retry flags so each user question starts from a clean slate.

    BeforeToolCallEvent — on every `run_athena_query` call, checks the SQL for a LIMIT clause. 
    Injects `default_limit` when missing, caps any larger value down to `default_limit`. 
    If a previous query in this invocation was already capped AND returned rows,  cancels the tool call instead so the LLM stops fighting for more rows.

    AfterToolCallEvent — records whether `run_athena_query` returned non-empty data. 
    That flag feeds the retry-block logic on the next tool call.
    """

    def __init__(
        self,
        verbose_columns: list[str] | None = None,
        default_limit: int = 20,
        verbose_limit: int = 5,
    ):
        """
        Initialize the rewrite hook to LIMIT defaults and empty smart-retry tracking state

        Args:
            verbose_columns: Column names to manage. Currently unused after the refactor to 1-summarizer; kept for future use.
            default_limit: Max rows for normal queries. Injected when SQL has no LIMIT clause; also used as the cap when existing LIMIT is larger. 
                           Defaults to 20.
            verbose_limit: Max rows when verbose columns are present.
                           Currently unused alongside `verbose_columns`. Defaults to 5.
        """
        self._verbose_columns = [c.lower() for c in (verbose_columns or [])]
        self._default_limit = default_limit
        self._verbose_limit = verbose_limit
        self._limit_was_capped = False
        self._last_query_returned_rows = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register callbacks for BeforeInvocationEvent, BeforeToolCallEvent, and AfterToolCallEvent"""
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)


    def on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Reset smart-retry flags at the start of each invocation"""
        self._limit_was_capped = False
        self._last_query_returned_rows = False

    def on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Enforce LIMIT on outgoing SQL; cancel the call if a prior capped query already returned data"""
        if event.tool_use.get("name") != "run_athena_query":
            return

        if self._limit_was_capped and self._last_query_returned_rows:
            print(
                "[LTTM:SQLRewrite] BLOCKED — LIMIT was capped and previous "
                "query returned data. Do not retry for more rows.",
                flush=True,
            )
            event.cancel_tool = (
                "Your previous query already returned data with the maximum "
                "allowed rows. Do NOT retry for more rows. Return the results "
                "you already have to the user."
            )
            return

        sql = event.tool_use.get("input", {}).get("sql", "")
        if not sql:
            return

        original_sql = sql
        current_limit = self._get_current_limit(sql)
        target_limit = self._default_limit

        limit_was_capped_this_call = False
        if current_limit is None:
            sql = self._set_limit(sql, target_limit)
            emit_status(
                f"Added LIMIT {target_limit} to prevent oversized results",
                source="sql_rewrite",
            )
            print(
                f"[LTTM:SQLRewrite] Injected LIMIT {target_limit} (was missing)",
                flush=True,
            )
        elif current_limit > target_limit:
            sql = self._set_limit(sql, target_limit)
            emit_status(
                f"Requested {current_limit} lines, but due to context "
                f"limitations stripping to {target_limit}",
                source="sql_rewrite",
            )
            print(
                f"[LTTM:SQLRewrite] Capped LIMIT from {current_limit} "
                f"to {target_limit}",
                flush=True,
            )
            limit_was_capped_this_call = True

        if limit_was_capped_this_call:
            self._limit_was_capped = True

        if sql != original_sql:
            event.tool_use["input"]["sql"] = sql
            print(
                f"[LTTM:SQLRewrite] Rewritten: {sql[:200]}...",
                flush=True,
            )
        else:
            print("[LTTM:SQLRewrite] PASSED — no rewrite needed", flush=True)

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        """Record whether the last run_athena_query call returned rows"""
        if event.tool_use.get("name") != "run_athena_query":
            return
        if event.result is None or isinstance(event.result, Exception):
            self._last_query_returned_rows = False
            return
        result = event.result
        if isinstance(result, dict):
            content = result.get("content", [])
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    text = block["text"]
                    if text and text.strip() and text.strip() != "[]":
                        self._last_query_returned_rows = True
                        return
        self._last_query_returned_rows = False

    def _get_current_limit(self, sql: str) -> int | None:
        """Return the LIMIT value already present in the SQL, or None if absent"""
        match = re.search(r'\bLIMIT\s+(\d+)', sql, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _set_limit(self, sql: str, limit: int) -> str:
        """Replace any existing LIMIT with the given value, or append one if the SQL has none"""
        if re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            return re.sub(
                r'\bLIMIT\s+\d+', f'LIMIT {limit}',
                sql, flags=re.IGNORECASE,
            )
        else:
            sql = sql.rstrip().rstrip(';')
            return f"{sql}\nLIMIT {limit}"
