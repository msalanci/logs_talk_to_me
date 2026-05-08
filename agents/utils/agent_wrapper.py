# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
agent_wrapper.py — Shared wrapper for all LTTM sub-agent @tool functions.

Every sub-agent has a @tool-decorated function (e.g., query_cloudtrail,query_cur, query_guardduty) that the supervisor calls. 
The body of eachfunction is identical: 
    emit SSE status,
    run the sub-agent,
    apply workarounds,
    handle errors, 
    return the answer

Extracts the raw tool result (what Athena returns), bypassing the sub-agent's summary.
This gives the user nicely formatted output when the LLM cooperates,and falls back to raw JSON 
only when the LLM fails (empty, summarized).
"""

import utils.agent_vars as vars


def _extract_raw_result(agent) -> str:
    """
    Extract the raw tool result from an agent's message history, bypassing the LLM.

    Checks every message role="user" toolResult block in `agent.messages` and returns 
    the longest text content by character count. 

    Args:
        agent: An `Agent` instance whose `.messages` attribute will be scanned. 
               The function tolerates a missing `messages` attribute by returning an empty string — no exception raised.

    Returns:
        Text of the largest toolResult text block found, or an empty string if `agent` has no messages, 
        no toolResult blocks, or all blocks have empty text. Never returns None.
    """
    messages = getattr(agent, "messages", None)
    if not messages:
        return ""

    best_text = ""
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or "toolResult" not in item:
                continue
            tr_content = item["toolResult"].get("content", [])
            for block in tr_content:
                if isinstance(block, dict) and "text" in block:
                    text = block["text"]
                    if text and len(text) > len(best_text):
                        best_text = text

    return best_text
