# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
output_integrity_hook.py — Deterministic output-integrity guard for the supervisor.

Registered in supervisor_agent.py as hook OutputIntegrityHook.

Catches two common supervisor failure modes after the model runs:
    H1 — claims "no results" when a query_* tool actually returned data.
    H8 — asks follow-up questions instead of delivering the answer.

Only a tool result's first line says whether it is empty or an error: a CloudWatch result can hold
application log lines such as "ERROR: …" further down. In an answer about several sources or
accounts, one part can rightly have nothing ("No results found." for dev) while another has rows, so
H1 stays quiet when any tool result of the question has an empty part. H1 and H8 phrases inside
quoted data (tables, code blocks, quotes) do not count.

Cost: $0, <1ms — pure string pattern matching, no LLM involvement.
"""

import re
from typing import Any
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    HookProvider,
    HookRegistry,
)

import utils.agent_vars as vars

CONTRADICTION_PATTERNS = [
    "no results found",
    "no results were found",
    "didn't return any",
    "did not return any",
    "couldn't find",
    "could not find",
    "no data available",
    "no data was found",
    "appears the query didn't return",
    "query returned no results",
    "unable to retrieve",
    "no information available",
    "no records found",
    "no findings found",
    "no events found",
    "no logs found",
    "returned empty",
    "no matching",
]

FOLLOWUP_PATTERNS = [
    "would you like me to",
    "shall i",
    "do you want me to",
    "would you like to know",
    "what would you like",
    "is there anything else",
    "would you like more",
    "should i check",
    "do you need me to",
    "want me to",
]

EMPTY_RESULT_PATTERNS = [
    "no findings found",
    "no results found",
    "no events found",
    "no logs found",
    "no data found",
    "error:",
    "error querying",
]

EMPTY_PART_PATTERNS = re.compile(
    r"no (results|findings|events|logs|data|records|rows)\b|\b0 rows\b|\bfindings: 0\b|returned no |not enabled in",
    re.IGNORECASE)


def _prose(text: str) -> str:
    """Take the quoted data out of an answer: code blocks, inline code, table rows and quoted lines.

    Args:
        text: The model's answer.

    Returns:
        The answer's prose, with each code block and inline code made a space and the table and quote
        lines left out.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", " ", text)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(("|", ">")))


class OutputIntegrityHook(HookProvider):
    """Deterministic guard that reconciles supervisor output against tool results.

    Runs across three events to build a per-invocation verdict:

    BeforeInvocationEvent — clears `_retry_count` and `_tools_with_data` so
    each user question starts from a clean slate.

    AfterToolCallEvent — records the name of any `query_*` tool that returned
    a non-empty, non-error payload in `_tools_with_data`.

    AfterModelCallEvent — scans the model's final text. If it matches a
    contradiction pattern (H1) or a follow-up-question pattern (H8) AND at
    least one tool returned real data, sets `event.retry = True` so the
    model regenerates against the actual results.

    Retry is capped at one per invocation to prevent loops.
    """

    def __init__(self, max_retries: int = 1):
        """Initialize the guard.

        Args:
            max_retries: Maximum number of times to request a regenerate when
                the model output contradicts tool results (H1) or asks a
                follow-up instead of delivering the answer (H8). Defaults to 1.
                Setting to 0 disables retries entirely — contradictions are
                still logged, but the model's response is not re-run.

        Returns:
            None. Also starts with no tool counted as having data, and no result with an empty part
            (a part with nothing: an account, region or source without rows).
        """
        self._max_retries = max_retries
        self._retry_count: int = 0
        self._tools_with_data: set[str] = set()
        self._some_part_empty = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register callbacks for BeforeInvocationEvent, AfterToolCallEvent, and AfterModelCallEvent.

        Args:
            registry: The supervisor's HookRegistry.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            None.
        """
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)
        registry.add_callback(AfterModelCallEvent, self.on_after_model_call)

    def on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Reset tracking state at the start of each invocation.

        Args:
            event: The BeforeInvocationEvent of the supervisor's question. Not read.

        Returns:
            None. Clears the retry count, the tools with data and the empty-part flag.
        """
        self._retry_count = 0
        self._tools_with_data.clear()
        self._some_part_empty = False

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        """Track which query_* tools returned non-empty, non-error data.

        A result says on its first line whether it is empty or an error (EMPTY_RESULT_PATTERNS); log
        data further down may say "ERROR:". A result shorter than 20 characters, an empty or error
        result, and a result with a part that rightly has nothing anywhere in it (EMPTY_PART_PATTERNS:
        an account, region or source without rows) set the empty-part flag.

        Args:
            event: The AfterToolCallEvent of a supervisor tool call. Calls of tools outside vars.QUERY_TOOLS
                are left alone.

        Returns:
            None. Adds the tool's name to the tools with data, and logs it, when its result has data.
        """
        tool_name = event.tool_use.get("name", "")
        if tool_name not in vars.QUERY_TOOLS:
            return

        result_text = ""
        if event.result and not isinstance(event.result, Exception):
            if isinstance(event.result, dict):
                content = event.result.get("content", [])
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        result_text = block["text"]
                        break
            else:
                result_text = str(event.result)

        if not result_text or len(result_text) < 20:
            self._some_part_empty = True
            return

        first_line = result_text.strip().split("\n", 1)[0].lower()
        if any(pattern in first_line for pattern in EMPTY_RESULT_PATTERNS):
            self._some_part_empty = True
            return
        if EMPTY_PART_PATTERNS.search(result_text):
            self._some_part_empty = True

        self._tools_with_data.add(tool_name)
        print(f"[LTTM:OutputIntegrity] Tracking data from {tool_name} ({len(result_text)} chars)", flush=True)

    def on_after_model_call(self, event: AfterModelCallEvent) -> None:
        """Scan model output for H1 (contradiction) and H8 (follow-up) patterns; retry if found.

        Only the answer's prose is scanned (_prose). H1 is not checked when some tool result of the
        question rightly had an empty part: then "no results found" can be true of that part. Nothing
        is checked when no tool returned data, or once max_retries is reached.

        Args:
            event: The AfterModelCallEvent of a supervisor model call. Its stop_response holds the
                answer; a call without one (None) is left alone.

        Returns:
            None. On a match, sets event.retry to True and counts the retry. Logs an
            [LTTM:OutputIntegrity] line with the verdict.
        """
        if not self._tools_with_data:
            return

        if self._retry_count >= self._max_retries:
            print("[LTTM:OutputIntegrity] SKIP — max retries reached", flush=True)
            return

        if event.stop_response is None:
            return

        message = event.stop_response.message
        output_text = ""
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    output_text += block["text"] + " "
        elif isinstance(content, str):
            output_text = content

        if not output_text.strip():
            return

        output_lower = _prose(output_text).lower()

        for pattern in ([] if self._some_part_empty else CONTRADICTION_PATTERNS):
            if pattern in output_lower:
                tools_str = ", ".join(sorted(self._tools_with_data))
                print(
                    f"[LTTM:OutputIntegrity] H1 CONTRADICTION — model said '{pattern}' "
                    f"but tools returned data: {tools_str}",
                    flush=True,
                )
                self._retry_count += 1
                event.retry = True
                return

        for pattern in FOLLOWUP_PATTERNS:
            if pattern in output_lower:
                tools_str = ", ".join(sorted(self._tools_with_data))
                print(
                    f"[LTTM:OutputIntegrity] H8 FOLLOWUP — model asked '{pattern}' "
                    f"instead of delivering results from: {tools_str}",
                    flush=True,
                )
                self._retry_count += 1
                event.retry = True
                return

        print("[LTTM:OutputIntegrity] PASSED — output consistent with tool results", flush=True)
