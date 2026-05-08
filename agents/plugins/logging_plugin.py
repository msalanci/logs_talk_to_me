# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
logging_plugin.py — Centralized lifecycle logging for all LTTM agents.

Replaces print() statements across agents and tool files, with a single plugin.

Logs:
    INVOKE_START — when an agent invocation begins (question preview)
    INVOKE_END   — when an agent invocation completes (result size, duration)
    TOOL_CALL    — before a tool executes (tool name, input preview)
    TOOL_DONE    — after a tool completes (tool name, result size, duration)
    TOOL_ERROR   — after a tool fails (tool name, exception, duration)

Registered on ALL 11 agents (supervisor + 10 sub-agents) via plugins=[LTTMLoggingPlugin()].

Does NOT replace:
    - Hook logging ([LTTM:SQLValidator], [LTTM:OutputIntegrity], etc.)
    - Memory logging ([LTTM:Memory])
    - Athena execution logging ([LTTM:Athena])
    - SSE emit_status() calls
    - Tool error logging
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
    """Centralized lifecycle logging plugin for all LTTM agents."""

    name = "lttm-logging"

    def __init__(self):
        super().__init__()
        self._tool_start_times: dict[str, float] = {}
        self._invocation_start: float = 0.0

    @hook
    def on_invoke_start(self, event: BeforeInvocationEvent) -> None:
        """Log invocation start with question preview."""
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
        """Log invocation end with duration."""
        duration_ms = int((time.monotonic() - self._invocation_start) * 1000)
        print(f"[LTTM:Log] INVOKE_END — {duration_ms}ms", flush=True)

    @hook
    def on_tool_start(self, event: BeforeToolCallEvent) -> None:
        """Log tool call start with name and input preview."""
        tool_name = event.tool_use.get("name", "unknown")
        tool_id = event.tool_use.get("toolUseId", tool_name)
        self._tool_start_times[tool_id] = time.monotonic()
        input_preview = str(event.tool_use.get("input", {}))[:200]
        print(f"[LTTM:Log] TOOL_CALL {tool_name} — {input_preview}", flush=True)

    @hook
    def on_tool_end(self, event: AfterToolCallEvent) -> None:
        """Log tool call end with name, result size, and duration."""
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
