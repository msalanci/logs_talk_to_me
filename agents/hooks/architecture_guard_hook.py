# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
architecture_guard_hook.py — Deterministic architecture leakage prevention.

Registered in supervisor_agent.py as hook ArchitectureGuardHook, on each session's supervisor.

Keeps the supervisor from revealing LTTM's internals (tool, hook and plugin names, implementation
details), in two layers:
  - Layer A, `is_probing()`: invoke() checks each question before the agent runs, and answers a
    question about the agent's own internals with PROBE_ANSWER. The agent does not run, so nothing
    is saved to memory.
  - Layer B, ArchitectureGuardHook: internal names in the model's answer are replaced with
    "[internal]" where they stand, instead of the model writing the answer again.

A question counts as probing only when it is about the agent itself ("your tools", "what tools do
you have", "system prompt"): "functions", "agents" and "tools" are also AWS nouns. INTERNAL_NAMES
holds no plain words that appear in real log data, such as ".py" or DEFAULT_REGION.

Cost: $0, under 1 ms — regular expressions only, no LLM call.
"""

import re
from typing import Any

from strands.hooks import (
    AfterModelCallEvent,
    HookProvider,
    HookRegistry,
)

import utils.agent_vars as vars
from utils.sse_emitter import emit_guard

_PARTS = r"(tools|sub-?agents|agents|functions|hooks|plugins|components|prompts?|instructions|internals|architecture|implementation|source\s+code|code)"

PROBING_PATTERNS = [
    rf"\byour\s+(own\s+|internal\s+)?{_PARTS}\b",
    r"\bwhat\s+(tools|sub-?agents|agents|functions|hooks|plugins)\s+(do|can|did)\s+you\b",
    r"\b(tools|sub-?agents|agents|hooks|plugins)\s+(do|can|did)\s+you\s+(have|use|call)\b",
    r"\bsystem\s+prompt\b",
    r"\bhow\s+(do|are)\s+you\s+(work|built|implemented|structured)\s+internally\b",
    r"\bhow\s+are\s+you\s+(built|implemented|structured)\b",
]

PROBE_ANSWER = ("I can't share details about how I'm built. I can help you analyze AWS infrastructure and logs — "
                "what would you like to investigate?")

REDACTED = "[internal]"
# Longest first, so a name wins over a shorter name inside it (query_guardduty_history over query_guardduty).
_INTERNAL = re.compile("|".join(re.escape(name) for name in sorted(vars.INTERNAL_NAMES, key=len, reverse=True)),
                       re.IGNORECASE)


def is_probing(question: str) -> bool:
    """Layer A: tell whether a question asks about the agent's own internals, not about AWS resources.

    Args:
        question: The user's question as it reached invoke(). None or "" counts as not probing.

    Returns:
        True when any of PROBING_PATTERNS matches the lower-cased question, False otherwise.
    """
    text = (question or "").lower()
    return any(re.search(pattern, text) for pattern in PROBING_PATTERNS)


class ArchitectureGuardHook(HookProvider):
    """Layer B: replace internal names in the supervisor's answers with "[internal]".

    Deterministic and free: the answer is redacted where it stands, instead of being written
    again by the model. Questions about the agent itself never get this far (Layer A).
    """

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the callback for AfterModelCallEvent.

        Args:
            registry: The agent's HookRegistry.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            None.
        """
        registry.add_callback(AfterModelCallEvent, self.on_after_model_call)

    def on_after_model_call(self, event: AfterModelCallEvent) -> None:
        """Layer B: redact the internal names in the model's answer, in place.

        Args:
            event: The AfterModelCallEvent of a supervisor model call. Its stop_response holds the
                answer's message; a call without one (None) is left alone.

        Returns:
            None. Changes the answer's text blocks inside the event. When it redacted a name, it also
            logs an [LTTM:ArchGuard] line and emits a guard status event to the CLI.
        """
        if event.stop_response is None:
            return
        content = event.stop_response.message.get("content", [])
        if not isinstance(content, list):
            return
        found = set()
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                found.update(m.group(0) for m in _INTERNAL.finditer(block["text"]))
                block["text"] = _INTERNAL.sub(REDACTED, block["text"])
        if found:
            print(f"[LTTM:ArchGuard] OUTPUT LEAK — redacted {sorted(found)}", flush=True)
            emit_guard("Removed internal names from the answer", source="supervisor")
