# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
output_integrity_hook.py — Deterministic output-integrity guard for the supervisor.

Registered in supervisor_agent.py as hook OutputIntegrityHook.

Catches two common supervisor failure modes after the model runs:
    H1 — claims "no results" when a query_* tool actually returned data.
    H8 — asks follow-up questions instead of delivering the answer.
"""

from typing import Any
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    HookProvider,
    HookRegistry,
)

# Sub-agent tools called by the supervisor
QUERY_TOOLS = {
    "query_cloudtrail",
    "query_cloudwatch",
    "query_config",
    "query_cur",
    "query_flowlogs",
    "query_guardduty",
    "query_access_analyzer",
    "query_health",
    "query_organizations",
    "query_quotas",
}

# H1: Supervisor says "no results" when tool actually returned data
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

# H8: Supervisor asks follow-up questions instead of answering
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

# Patterns that indicate a tool returned legitimately empty/error results
EMPTY_RESULT_PATTERNS = [
    "no findings found",
    "no results found",
    "no events found",
    "no logs found",
    "no data found",
    "error:",
    "error querying",
    "[lttm:result_too_large]",
]


class OutputIntegrityHook(HookProvider):
    """
    Deterministic guard that reconciles supervisor output against tool results.

    Runs across three events to build a per-invocation verdict:

    BeforeInvocationEvent — clears `_retry_count` and `_tools_with_data` so each user question starts from a clean slate.

    AfterToolCallEvent — records the name of any `query_*` tool that returned a non-empty, non-error payload in `_tools_with_data`.

    AfterModelCallEvent — scans the model's final text. If it matches a contradiction pattern or a follow-up-question pattern and 
    at least one tool returned real data, sets `event.retry = True` so the model regenerates against the actual results.

    Retry is capped at one per invocation to prevent loops.
    """

    def __init__(self, max_retries: int = 1):
        """
        Initialize the guard with a retry cap and empty per-invocation tracking state
        
        Args:
        max_retries: Maximum number of times to request a regenerate when the model output contradicts tool results,
                     or asks a follow-up instead of delivering the answer. 
                     Defaults to 1.
                     Setting to 0 disables retries entirely — contradictions are still logged, but the model's response is not re-run.
        """
        self._max_retries = max_retries
        self._retry_count: int = 0
        self._tools_with_data: set[str] = set()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register callbacks for BeforeInvocationEvent, AfterToolCallEvent, and AfterModelCallEvent."""
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)
        registry.add_callback(AfterModelCallEvent, self.on_after_model_call)

    def on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Reset tracking state at the start of each invocation."""
        self._retry_count = 0
        self._tools_with_data.clear()

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        """Track which query_* tools returned non-empty, non-error data."""
        tool_name = event.tool_use.get("name", "")
        if tool_name not in QUERY_TOOLS:
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
            return

        result_lower = result_text.lower()
        for pattern in EMPTY_RESULT_PATTERNS:
            if pattern in result_lower:
                return

        self._tools_with_data.add(tool_name)
        print(f"[LTTM:OutputIntegrity] Tracking data from {tool_name} ({len(result_text)} chars)", flush=True)

    def on_after_model_call(self, event: AfterModelCallEvent) -> None:
        """Scan model output for H1 (contradiction) and H8 (follow-up) patterns; retry if found."""
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

        output_lower = output_text.lower()

        # H1: Check for "no results" contradiction
        for pattern in CONTRADICTION_PATTERNS:
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

        # H8: Check for follow-up questions instead of answering
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
