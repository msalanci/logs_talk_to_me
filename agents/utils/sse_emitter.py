# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sse_emitter.py — Server-Sent Events (SSE) status event emitter for LTTM real-time status.

Provides functions to emit Server-Sent Events (SSE) during agent execution, giving users real-time visibility 
into what the agent is doing.
Events are pushed to a thread-safe queue, which is araw dicts. 
The supervisor's sync generator entrypoint yields each dict, and AgentCore's _convert_to_sse()
wraps it as "data: {json}\n\n" automatically.

AgentCore streams responses when the entrypoint is a sync generator (def + yield).
It detects via `inspect.isgenerator(result)` and uses `_sync_stream_with_error_handling`.
Each yielded object is JSON-serialized and wrapped in SSE format by the framework.

Gated by LTTM_SSE_STATUS env var, when enabled (default), status events are queued. 
Set LTTM_SSE_STATUS=false to disable and revert to the legacy single-JSON-response behavior.
"""

import os
import queue

_env_val = os.environ.get("LTTM_SSE_STATUS", "").lower()
if _env_val in ("true", "false"):
    _SSE_ENABLED = _env_val == "true"
else:
    _SSE_ENABLED = True  # Default: SSE is ON

_step_counter = 0
_event_queue: queue.Queue | None = None


def _reset():
    """Reset step counter and create a fresh event queue. Called at the start of each invocation."""
    global _step_counter, _event_queue
    _step_counter = 0
    _event_queue = queue.Queue()


def _next_step() -> int:
    """Increment and return the next step number."""
    global _step_counter
    _step_counter += 1
    return _step_counter


def _push_event(event: dict) -> None:
    """
    Push a Server-Sent Events (SSE) event dict to the thread-safe queue
    
    Args:
        event: Fully-formed SSE event dict (must already include `type`,`step`, `source` and any type-specific payload fields). 
               Pushed as-is onto the queue for the entrypoint generator to yield.

    Returns:
        None. Silently no-ops when the queue has not been created yet (`_reset()` has not been 
        called for the current invocation) or when `put_nowait` raises — emission failures must 
        never break core agent logic.
    """

    try:
        if _event_queue is not None:
            _event_queue.put_nowait(event)
    except Exception:
        pass


def get_queue() -> queue.Queue | None:
    """
    Return the current event queue. Used by the supervisor's sync generator entrypoint
    
    Returns:
        The active `queue.Queue` when `_reset()` has run for the current invocation, or `None` when SSE 
        has not been initialized yet (e.g. during module import or outside of an agent invocation).
        Callers must handle `None`.    
    """
    return _event_queue


def emit_status(message: str, source: str = "supervisor") -> None:
    """
    Emit a status Server-Sent Events (SSE) event. No-op if SSE is disabled.

    Args:
        message: Human-readable description of the current step
        source: Component that generated the event (e.g. supervisor, cloudtrail_agent, cloudwatch_agent, athena)
            
    Returns:
        None. Silently no-ops when SSE is disabled (`LTTM_SSE_STATUS=false`), or when the event queue has not been 
        created yet — safe to call unconditionally without checking `is_enabled()` first.                 
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "status", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_result(result: str, source: str = "supervisor") -> None:
    """
    Emit the final result Server-Sent Events (SSE) event. No-op if SSE is disabled.

    Args:
        result: The complete plain-English answer
        source: Component that generated the event

    Returns:
        None. Silently no-ops when SSE is disabled, safe to call unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "result", "step": step, "source": source, "result": result}
    _push_event(event)


def emit_error(message: str, source: str = "supervisor") -> None:
    """
    Emit an error Server-Sent Events (SSE) event. No-op if SSE is disabled.

    Args:
        message: Description of the error 
        source: Component that generated the event

    Returns:
        None. Silently no-ops when SSE is disabled, safe to call unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "error", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_guard(message: str, source: str = "supervisor") -> None:
    """
    Emit a guardrail/security Server-Sent Events (SSE) event. No-op if SSE is disabled.
    Rendered with 🛡️ prefix in alexandra.sh

    Args:
        message: Description of the guardrail action
        source: Component that generated the event

    Returns:
        None. Silently no-ops when SSE is disabled, safe to call unconditionally.
    """

    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "guard", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_tokens(message: str, source: str = "supervisor") -> None:
    """
    Emit a token cost attribution Server-Sent Events (SSE) event. No-op if SSE is disabled.
    Rendered with 💲 prefix in alexandra.sh

    Args:
        message: Token usage summary string
        source: Component that generated the event

    Returns:
        None. Silently no-ops when SSE is disabled, safe to call unconditionally.
    """

    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "tokens", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_ping(source: str = "supervisor") -> None:
    """
    Emit a heartbeat Server-Sent Events (SSE) event to keep the stream alive.

    Why: During the supervisor's final-answer compose phase (after all sub-agents
    return but before emit_result fires), no SSE events are produced for up to
    ~90 seconds while the LLM generates the response. Prolonged silence on the
    HTTP body causes API Gateway / Lambda response streaming / client to drop
    the connection mid-flight (observed as a ~127s cutoff client-side while the
    server continues to INVOKE_END ~203s later).

    This emitter yields a minimal event — `{"type": "ping", ...}` — purely so
    bytes keep flowing through the SSE pipe. alexandra.sh's parser has no
    `ping` case in its `case "$TYPE" in` block, so unknown types are silently
    dropped — the user sees nothing, the connection stays alive.

    Called by the supervisor's yield loop on queue.Queue.get() timeout,
    not by individual hooks or sub-agents.

    Args:
        source: Component that generated the event (default "supervisor").

    Returns:
        None. Silently no-ops when SSE is disabled, safe to call unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "ping", "step": step, "source": source}
    _push_event(event)


def emit_done() -> None:
    """Push a sentinel None to signal the sync generator entrypoint to stop yielding."""
    if _event_queue is not None:
        try:
            _event_queue.put_nowait(None)
        except Exception:
            pass


def is_enabled() -> bool:
    """Check if Server-Sent Events (SSE) status emission is enabled."""
    return _SSE_ENABLED
