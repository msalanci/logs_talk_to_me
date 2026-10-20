# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
prompt_guard.py — Checks each user question against the Bedrock managed guardrail.

The guardrail (lttm-prompt-guard: prompt-attack filter + off-topic denial) looks at the
question once, when it arrives, through the ApplyGuardrail API, not on the supervisor's
BedrockModel. On the model it would run on every model call over the whole conversation
text. That would also show it the steering judge's feedback, which Strands adds as a user
message, and a judge GUIDE was blocked as a prompt attack: the user got "GUARDRAIL
VIOLATION" instead of the answer. Converse guardrails never evaluate tool results, so log
content would not be screened that way either.
"""

from typing import Any

import boto3

import utils.agent_vars as vars

BLOCKED_MESSAGE = "GUARDRAIL VIOLATION: I can only help with AWS infrastructure and log analysis questions."


def question_allowed(question: str, client: Any = None) -> bool:
    """Return True when the guardrail lets the question through.

    Without LTTM_GUARDRAIL_ID/LTTM_GUARDRAIL_VERSION every question is allowed.
    An API error is raised to the caller, so a broken guardrail fails the question
    instead of letting it through unchecked.

    Args:
        question: The user's question, as received by the supervisor.
        client: A bedrock-runtime client; tests pass a fake one.

    Returns:
        False when the guardrail intervenes (GUARDRAIL_INTERVENED), True otherwise.
    """
    if not (vars.GUARDRAIL_ID and vars.GUARDRAIL_VERSION):
        return True
    client = client or boto3.client("bedrock-runtime", region_name=vars.GUARDRAIL_REGION)
    response = client.apply_guardrail(
        guardrailIdentifier=vars.GUARDRAIL_ID,
        guardrailVersion=vars.GUARDRAIL_VERSION,
        source="INPUT",
        content=[{"text": {"text": question}}],
    )
    return response.get("action") != "GUARDRAIL_INTERVENED"
