# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sse_emitter.py — Server-Sent Events (SSE) status event emitter for LTTM real-time status.

Provides functions to emit Server-Sent Events (SSE) during agent execution,
giving users real-time visibility into what the agent is doing. Events are
pushed to a thread-safe queue.Queue as raw dicts. The supervisor's sync
generator entrypoint yields each dict, and AgentCore's _convert_to_sse()
wraps it as "data: {json}\n\n" automatically.

AgentCore streams responses when the entrypoint is a sync generator (def + yield).
It detects via inspect.isgenerator(result) and uses _sync_stream_with_error_handling.
Each yielded object is JSON-serialized and wrapped in SSE format by the framework.

Gated by LTTM_SSE_STATUS env var. When enabled (default), status events
are queued. Set LTTM_SSE_STATUS=false to disable and revert
to the legacy single-JSON-response behavior.

The event queue and the step counter belong to one question: they live in its
RequestContext (utils/request_context.py), which invoke() creates, not in
module globals that every question in the process would share.
"""

import os
import queue

import utils.request_context as request_context

_env_val = os.environ.get("LTTM_SSE_STATUS", "").lower()
if _env_val in ("true", "false"):
    _SSE_ENABLED = _env_val == "true"
else:
    _SSE_ENABLED = True


def _next_step() -> int:
    """The next step number of the current question's events.

    Args:
        None.

    Returns:
        The number, from 1 per question.
    """
    return next(request_context.current().steps)


def _push_event(event: dict) -> None:
    """Push a Server-Sent Events (SSE) event dict to the current question's queue.

    Args:
        event: Fully-formed SSE event dict (must already include `type`,
            `step`, `source`, and any type-specific payload fields). Pushed
            as-is onto the queue for the entrypoint generator to yield.

    Returns:
        None. Silently no-ops when the current question has no queue (not
        streaming, or outside a question) or when `put_nowait` raises —
        emission failures must never break core agent logic.
    """
    try:
        events = request_context.current().events
        if events is not None:
            events.put_nowait(event)
    except Exception:
        pass


def get_queue() -> queue.Queue | None:
    """Return the current question's event queue, or None outside a streaming question.

    Args:
        None.

    Returns:
        The queue of the current RequestContext; None when it has none.
    """
    return request_context.current().events


def emit_status(message: str, source: str = "supervisor") -> None:
    """Emit a status Server-Sent Events (SSE) event to the current invocation's queue.

    Args:
        message: Human-readable description of the current step.
        source: Component that generated the event (e.g. supervisor,
            cloudtrail_agent, cloudwatch_agent, athena).

    Returns:
        None. Silently no-ops when SSE is disabled (`LTTM_SSE_STATUS=false`)
        or when the event queue has not been created yet — safe to call
        unconditionally without checking `is_enabled()` first.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "status", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_result(result: str, source: str = "supervisor") -> None:
    """Emit the final result Server-Sent Events (SSE) event.

    Args:
        result: The complete plain-English answer.
        source: Component that generated the event.

    Returns:
        None. Silently no-ops when SSE is disabled — safe to call
        unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "result", "step": step, "source": source, "result": result}
    _push_event(event)


def emit_error(message: str, source: str = "supervisor") -> None:
    """Emit an error Server-Sent Events (SSE) event.

    Args:
        message: Description of the error.
        source: Component that generated the event.

    Returns:
        None. Silently no-ops when SSE is disabled — safe to call
        unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "error", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_guard(message: str, source: str = "supervisor") -> None:
    """Emit a guardrail/security Server-Sent Events (SSE) event.

    Rendered with 🛡️ prefix in alexandra.sh (distinct from ⏳ status and ❌ error).

    Args:
        message: Description of the guardrail action.
        source: Component that generated the event.

    Returns:
        None. Silently no-ops when SSE is disabled — safe to call
        unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "guard", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_tokens(message: str, source: str = "supervisor") -> None:
    """Emit a token cost attribution Server-Sent Events (SSE) event.

    Rendered with 💰 prefix in alexandra.sh (distinct from ⏳ status). One
    receipt per question.

    Args:
        message: Token usage summary string.
        source: Component that generated the event.

    Returns:
        None. Silently no-ops when SSE is disabled — safe to call
        unconditionally.
    """
    if not _SSE_ENABLED:
        return
    step = _next_step()
    event = {"type": "tokens", "step": step, "source": source, "message": message}
    _push_event(event)


def emit_done() -> None:
    """Push a sentinel None to signal the sync generator entrypoint to stop yielding.

    Args:
        None.

    Returns:
        None. Silently no-ops when the current question has no queue, or the push fails.
    """
    events = request_context.current().events
    if events is not None:
        try:
            events.put_nowait(None)
        except Exception:
            pass


def is_enabled() -> bool:
    """Check if Server-Sent Events (SSE) status emission is enabled.

    Args:
        None.

    Returns:
        False when LTTM_SSE_STATUS is "false"; True otherwise.
    """
    return _SSE_ENABLED
