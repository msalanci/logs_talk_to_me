# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sql_rewrite_hook.py — Deterministic SQL rewriting hook for Athena sub-agents.

Registered in each Athena sub-agent (cloudtrail, cloudwatch, config, cur,
flowlogs, guardduty) as hook SQLRewriteHook — fires AFTER SQLValidatorHook so
validation runs first, then rewriting.

Sub-agent LLMs generate SQL that is structurally valid but produces oversized
results. This hook caps LIMIT to a safe default, injects LIMIT when missing, and
blocks retry loops where the LLM keeps fighting the cap after it already received
data. Within one question it also cancels a query that repeats an earlier
successful one, and it adds the source-file column to a row-level CloudTrail
query, from which run_athena_query makes each row's provenance label.

Deterministic regex — no LLM calls, <1ms, $0.
"""

import json
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
import utils.agent_vars as vars
import utils.sql_text as sql_text
import tools.cloudtrail_provenance as cloudtrail_provenance


REPEAT_MESSAGE = ("This query repeats an earlier query of this question, and its rows are already in your results: "
                  "running it again returns the same rows. Do NOT run it again. Return the results you already have.")


def _query_key(sql: str) -> str:
    """Make a query into a key for spotting a repeat in one question.

    Its own LIMIT is left out (the hook sets it), and so is the source-file column the hook adds (the model
    may copy it from its own history to another place in the SELECT).

    Args:
        sql: The query's SQL.

    Returns:
        The query in lower case, with single spaces and no spaces around commas and parentheses, without
        its own LIMIT and the source-file column.
    """
    own = sql_text.outer_limit(sql)
    if own:
        sql = sql[:own.start()] + sql[own.end():]
    text = re.sub(r"\s*([(),;])\s*", r"\1", " ".join(sql.split()).lower()).rstrip(";")
    column = re.escape(cloudtrail_provenance.SOURCE_COLUMN)
    return re.sub(rf',"\$path" as {column}\b|"\$path" as {column},|,{column}\b', "", text)


def _row_count(text: str) -> int:
    """Count the rows of a run_athena_query result.

    Args:
        text: The result's text, a JSON list of rows.

    Returns:
        The number of rows; 0 when the text is not a JSON list.
    """
    try:
        rows = json.loads(text)
    except (ValueError, TypeError):
        return 0
    return len(rows) if isinstance(rows, list) else 0


class SQLRewriteHook(HookProvider):
    """Rewrite sub-agent SQL to enforce LIMIT and block wasteful retries.

    Three-event contract per invocation:

    BeforeInvocationEvent — clears smart-retry flags so each user question
    starts from a clean slate.

    BeforeToolCallEvent — on every `run_athena_query` call, checks the SQL
    for a LIMIT clause. Injects `default_limit` when missing, caps any
    larger value down to `default_limit`. If a previous query in this
    invocation was already capped AND returned rows, cancels the tool call
    instead so the LLM stops fighting for more rows.

    AfterToolCallEvent — records whether `run_athena_query` returned non-empty
    data. That flag feeds the retry-block logic on the next tool call.

    Deterministic regex only; no LLM calls, no network.
    """

    def __init__(
        self,
        default_limit: int = 20,
        grouped_limit: int | None = vars.GROUPED_ROW_LIMIT,
        one_more_row: bool = True,
    ):
        """Initialize the rewrite hook.

        Args:
            default_limit: Max rows for normal queries. Injected when SQL
                has no LIMIT clause; also used as the cap when existing
                LIMIT is larger. Defaults to 20.
            grouped_limit: Max rows for a query whose own SELECT has a GROUP BY: one row per
                group, so a cut loses totals. The hook asks Athena for one row more, and
                `format_athena_rows` says when there were more groups than it shows. None keeps such
                queries at `default_limit` (the CUR agent: its code-made totals count on that cap).
            one_more_row: For a list (no GROUP BY of its own) whose LIMIT the hook sets, injects or
                caps, ask Athena for `default_limit` + 1 rows. When the extra row comes back, on_after_tool_call keeps
                the first `default_limit` and marks the result with `vars.MORE_ROWS_KEY`, so the answer says that
                more rows match. A LIMIT under `default_limit` (top-N) stays as written. False keeps the cap exact
                (the CUR agent: its code-made totals say themselves when a result may be cut).

        Returns:
            None. Also sets up the per-question state: the two retry-block flags, the GROUP BY calls that got
            the grouped cap from this hook (toolUseId -> that LIMIT), the list calls that got one row more
            (toolUseId -> the rows the answer keeps), and the keys (_query_key) of the queries that ran with
            success.
        """
        self._default_limit = default_limit
        self._grouped_limit = grouped_limit
        self._one_more_row = one_more_row
        self._limit_was_capped = False
        self._last_query_returned_rows = False
        self._grouped_calls: dict[str, int] = {}
        self._list_calls: dict[str, int] = {}
        self._done_queries: set[str] = set()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register callbacks for BeforeInvocationEvent, BeforeToolCallEvent, and AfterToolCallEvent.

        Args:
            registry: The sub-agent's HookRegistry.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            None.
        """
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)

    def on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Reset smart-retry flags at the start of each invocation.

        Args:
            event: The BeforeInvocationEvent of the sub-agent's question. Not read.

        Returns:
            None. Clears the flags, the GROUP BY and list calls, and the queries done.
        """
        self._limit_was_capped = False
        self._last_query_returned_rows = False
        self._grouped_calls.clear()
        self._list_calls.clear()
        self._done_queries.clear()

    def on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Enforce LIMIT on outgoing SQL; cancel the call if a prior capped query already returned data.

        Also cancels a query that repeats an earlier successful query of this question: it can only return
        the same rows (a CloudTrail sub-agent listed the rows, counted them, then ran the first list again).
        A query that failed is not recorded, so it may be asked again.

        The LIMIT: a GROUP BY query gets the grouped cap, and on_after_tool_call checks whether it filled it.
        A list whose LIMIT the hook sets (none, or `default_limit` or more) asks Athena for one row more than
        the answer shows, and on_after_tool_call keeps `default_limit` rows and marks the result when the
        extra row came back; a top-N under the cap stays as written, and so does every query of the CUR agent
        (one_more_row=False). The model's own LIMIT of `default_limit` is raised by one, without a status
        line. A model's own LIMIT of `default_limit` + 1 counts as no cap: the model copies the hook's LIMIT
        from its history, and flagging it ("Requested 21 lines") could block its next query. The status lines
        name the most rows the answer shows, never a list's extra row.

        Finally a row-level CloudTrail query also gets each row's source file
        (cloudtrail_provenance.with_source_file), which run_athena_query turns into a provenance label
        (signed by CloudTrail or not).

        Args:
            event: The BeforeToolCallEvent of the sub-agent's tool call. Calls of tools other than
                run_athena_query are left alone.

        Returns:
            None. Writes the rewritten SQL back into event.tool_use["input"]["sql"], or sets
            event.cancel_tool; emits status lines and logs [LTTM:SQLRewrite] lines.
        """
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

        if _query_key(sql) in self._done_queries:
            print("[LTTM:SQLRewrite] BLOCKED — repeats an earlier query of this question", flush=True)
            event.cancel_tool = REPEAT_MESSAGE
            return

        original_sql = sql
        current_limit = self._get_current_limit(sql)
        target_limit = self._target_limit(sql)

        limit_was_capped_this_call = False
        grouped = self._grouped_limit is not None and target_limit == self._grouped_limit + 1
        if grouped and (current_limit is None or current_limit > target_limit):
            self._grouped_calls[event.tool_use.get("toolUseId", "")] = target_limit
        listed = self._one_more_row and not grouped and (current_limit is None or current_limit >= self._default_limit)
        if listed:
            target_limit = self._default_limit + 1
            self._list_calls[event.tool_use.get("toolUseId", "")] = self._default_limit
        shown = self._grouped_limit if grouped else self._default_limit if listed else target_limit
        if current_limit is None:
            sql = self._set_limit(sql, target_limit)
            emit_status(
                f"Limited the result to {self._grouped_limit} groups" if grouped else
                f"Added LIMIT {shown} to prevent oversized results",
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
                f"limitations stripping to {shown}",
                source="sql_rewrite",
            )
            print(
                f"[LTTM:SQLRewrite] Capped LIMIT from {current_limit} "
                f"to {target_limit}",
                flush=True,
            )
            limit_was_capped_this_call = True
        elif listed and current_limit == self._default_limit:
            sql = self._set_limit(sql, target_limit)
            print(f"[LTTM:SQLRewrite] Raised LIMIT from {current_limit} to {target_limit}: the extra row tells "
                  f"whether more rows match", flush=True)

        if limit_was_capped_this_call:
            self._limit_was_capped = True

        sql = cloudtrail_provenance.with_source_file(sql)

        if sql != original_sql:
            event.tool_use["input"]["sql"] = sql
            print(
                f"[LTTM:SQLRewrite] Rewritten: {sql[:200]}...",
                flush=True,
            )
        else:
            print("[LTTM:SQLRewrite] PASSED — no rewrite needed", flush=True)

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        """Record whether the last run_athena_query call returned rows; cut a list that brought its extra row.

        A successful query's key is recorded, so a repeat is cancelled. A GROUP BY result that filled the cap
        this hook set blocks the next query, as after a cap: asking again only returns the same groups. When
        a list's extra row came back, more rows match than the answer shows: the result keeps the first
        `default_limit` rows, as the model saw before, and is marked with `vars.MORE_ROWS_KEY` (Strands never
        sends the model that key). A list that filled its cap does not block the next query.

        Args:
            event: The AfterToolCallEvent of the sub-agent's tool call. Calls of tools other than
                run_athena_query are left alone.

        Returns:
            None. Sets the retry-block flags, and may replace event.result with the cut, marked result.
        """
        if event.tool_use.get("name") != "run_athena_query":
            return
        cap = self._grouped_calls.pop(event.tool_use.get("toolUseId", ""), None)
        kept = self._list_calls.pop(event.tool_use.get("toolUseId", ""), None)
        if event.result is None or isinstance(event.result, Exception):
            self._last_query_returned_rows = False
            return
        result = event.result
        if isinstance(result, dict) and result.get("status") == "success":
            self._done_queries.add(_query_key(str(event.tool_use.get("input", {}).get("sql", ""))))
        if isinstance(result, dict):
            content = result.get("content", [])
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    text = block["text"]
                    if text and text.strip() and text.strip() != "[]":
                        self._last_query_returned_rows = True
                        if cap and _row_count(text) >= cap:
                            self._limit_was_capped = True
                            print(f"[LTTM:SQLRewrite] GROUP BY result filled LIMIT {cap} — no further query "
                                  f"in this question", flush=True)
                        if kept and _row_count(text) > kept:
                            event.result = {**result, "content": [{"text": json.dumps(json.loads(text)[:kept])}],
                                            vars.MORE_ROWS_KEY: kept}
                            print(f"[LTTM:SQLRewrite] More than {kept} rows match: the first {kept} kept, the result "
                                  f"marked", flush=True)
                        return
        self._last_query_returned_rows = False

    def _target_limit(self, sql: str) -> int:
        """Choose the LIMIT this query may have.

        A GROUP BY in a subquery, a CTE, a string or a comment does not count: the rows are then not one
        per group.

        Args:
            sql: The query's SQL.

        Returns:
            One more than `grouped_limit` when the statement's own SELECT has a GROUP BY (and
            `grouped_limit` is set); `default_limit` otherwise.
        """
        if self._grouped_limit is None:
            return self._default_limit
        masked = sql_text.mask(sql).lower()
        grouped = any(masked[:m.start()].count("(") == masked[:m.start()].count(")")
                      for m in re.finditer(r"\bgroup\s+by\b", masked))
        return self._grouped_limit + 1 if grouped else self._default_limit

    def _get_current_limit(self, sql: str) -> int | None:
        """Find the statement's own LIMIT; a subquery's or a CTE's LIMIT does not count.

        Args:
            sql: The query's SQL.

        Returns:
            The LIMIT's number; None when the statement has no LIMIT of its own.
        """
        match = sql_text.outer_limit(sql)
        return int(match.group(1)) if match else None

    def _set_limit(self, sql: str, limit: int) -> str:
        """Set the statement's own LIMIT to the given value, or append one; subquery and CTE LIMITs stay.

        Args:
            sql: The query's SQL.
            limit: The LIMIT to set.

        Returns:
            The SQL with its own LIMIT set to `limit`; without one, with "LIMIT <limit>" appended on a new
            line (a trailing ";" removed).
        """
        match = sql_text.outer_limit(sql)
        if match:
            return f"{sql[:match.start()]}LIMIT {limit}{sql[match.end():]}"
        sql = sql.rstrip().rstrip(';')
        return f"{sql}\nLIMIT {limit}"
