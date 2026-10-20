# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
logging_plugin.py — Centralized lifecycle logging for the LTTM supervisor.

Registered on the supervisor (supervisor_agent.py) as plugin
LTTMLoggingPlugin via `plugins=[steering, LTTMLoggingPlugin()]`.

A single Strands Plugin that hooks into the agent lifecycle, instead of
scattered `print()` statements across agent and tool files. The plugin emits
five tagged log lines (INVOKE_START / INVOKE_END / TOOL_CALL / TOOL_DONE /
TOOL_ERROR) with names, input previews, result sizes, and durations so a
single `grep '[LTTM:Log]'` shows the full timeline of any invocation.

Does NOT replace:
    - Hook-specific logging ([LTTM:SQLValidator], [LTTM:OutputIntegrity], etc.)
    - Memory logging ([LTTM:Memory])
    - Athena execution logging ([LTTM:Athena])
    - SSE emit_status() calls
    - Tool-specific error logging

Based on an article by Morgan Willis and the Strands Plugin API.
"""

import time
from strands.plugins import Plugin, hook
from strands.hooks import (
    BeforeInvocationEvent,
    AfterInvocationEvent,
    BeforeToolCallEvent,
    AfterToolCallEvent,
)


class LTTMLoggingPlugin(Plugin):
    """Centralized lifecycle logger — prints five tagged events per invocation.

    On every agent invocation, emits:
      - INVOKE_START on `BeforeInvocationEvent` (user question preview).
      - INVOKE_END on `AfterInvocationEvent` (total duration in ms).

    On every tool call (any tool the agent invokes), emits:
      - TOOL_CALL on `BeforeToolCallEvent` (tool name + input preview).
      - TOOL_DONE on `AfterToolCallEvent` (tool name + duration) on success.
      - TOOL_ERROR on `AfterToolCallEvent` (tool name + exception + duration)
        when the tool raised.

    All output goes to stdout with the `[LTTM:Log]` prefix so it's easy to
    filter in CloudWatch. Per-tool start times are tracked in a dict keyed
    by `toolUseId` so concurrent tool calls measure correctly.
    """

    name = "lttm-logging"

    def __init__(self):
        """Initialize per-invocation tracking state (tool start times, invocation start).

        Args:
            None.

        Returns:
            None.
        """
        super().__init__()
        self._tool_start_times: dict[str, float] = {}
        self._invocation_start: float = 0.0

    @hook
    def on_invoke_start(self, event: BeforeInvocationEvent) -> None:
        """Log invocation start with a 200-char preview of the user question.

        Args:
            event: The BeforeInvocationEvent; the preview is the newest message's first text.

        Returns:
            None. Prints an INVOKE_START line, and starts the invocation's clock.
        """
        self._invocation_start = time.monotonic()
        preview = ""
        if event.messages:
            for msg in reversed(event.messages):
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and "text" in block:
                            preview = block["text"][:200]
                            break
                elif isinstance(content, str):
                    preview = content[:200]
                if preview:
                    break
        print(f"[LTTM:Log] INVOKE_START — {preview!r}", flush=True)

    @hook
    def on_invoke_end(self, event: AfterInvocationEvent) -> None:
        """Log invocation end with total duration in milliseconds.

        Args:
            event: The AfterInvocationEvent. Not read.

        Returns:
            None. Prints an INVOKE_END line.
        """
        duration_ms = int((time.monotonic() - self._invocation_start) * 1000)
        print(f"[LTTM:Log] INVOKE_END — {duration_ms}ms", flush=True)

    @hook
    def on_tool_start(self, event: BeforeToolCallEvent) -> None:
        """Log tool call start with tool name and a 200-char input preview.

        Args:
            event: The BeforeToolCallEvent of the tool call.

        Returns:
            None. Prints a TOOL_CALL line, and starts the call's clock under its toolUseId.
        """
        tool_name = event.tool_use.get("name", "unknown")
        tool_id = event.tool_use.get("toolUseId", tool_name)
        self._tool_start_times[tool_id] = time.monotonic()
        input_preview = str(event.tool_use.get("input", {}))[:200]
        print(f"[LTTM:Log] TOOL_CALL {tool_name} — {input_preview}", flush=True)

    @hook
    def on_tool_end(self, event: AfterToolCallEvent) -> None:
        """Log tool call end — TOOL_DONE with duration on success, TOOL_ERROR with exception on failure.

        Args:
            event: The AfterToolCallEvent of the tool call; on the supervisor, a sub-agent's result.

        Returns:
            None. Prints a TOOL_DONE or TOOL_ERROR line; the duration is -1 ms when the call's start
            was not seen.
        """
        tool_name = event.tool_use.get("name", "unknown")
        tool_id = event.tool_use.get("toolUseId", tool_name)
        start = self._tool_start_times.pop(tool_id, None)
        duration_ms = int((time.monotonic() - start) * 1000) if start else -1

        if event.exception:
            print(
                f"[LTTM:Log] TOOL_ERROR {tool_name} — {event.exception}, {duration_ms}ms",
                flush=True,
            )
        else:
            print(
                f"[LTTM:Log] TOOL_DONE {tool_name} — {duration_ms}ms",
                flush=True,
            )
