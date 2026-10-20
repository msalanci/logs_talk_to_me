# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
agent_wrapper.py — Shared extraction helpers for LTTM sub-agent tool results.

Sub-agent LLMs occasionally return an empty or heavily summarized
`str(result)` even when the underlying Athena query returned real data. This
module pulls the tool results directly from `agent.messages` — bypassing the
LLM entirely — so the `query_*` wrappers hand the supervisor raw data.

Only the tool results of the agent's newest question count, and all of them
are kept, in order: keeping only the largest would drop every account but one
in a multi-account answer, and could return an earlier question's rows. Each
`query_*` wrapper also builds a fresh sub-agent per question, so no history
carries over and parallel calls to the same sub-agent don't collide.

`accounts_total` adds up the finding lists of a question that asked one API
tool about several accounts, so the supervisor quotes one total instead of
adding the lists up itself.
"""

import inspect
import json

import utils.agent_vars as vars
from tools.athena_tool import format_athena_rows
from utils.finding_counts import combined_count_line, joined


def _newest_question(agent) -> list[dict]:
    """Find the agent's messages from its newest question on.

    The newest question is the last user message with a text block: tool results arrive as user messages too.

    Args:
        agent: A Strands Agent, or anything with a `messages` list.

    Returns:
        The messages from that question on; [] when `messages` is missing or empty.
    """
    messages = getattr(agent, "messages", None) or []

    start = 0
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list) and any(
                isinstance(block, dict) and "text" in block for block in content):
            start = i
    return messages[start:]


def _tool_calls(agent) -> list[dict]:
    """List every tool call of the agent's newest question, in order.

    Args:
        agent: A Strands Agent, or anything with a `messages` list.

    Returns:
        One {name, input, status, text} per tool result, matched to its call by toolUseId; [] when there are none.
    """
    uses, calls = {}, []
    for msg in _newest_question(agent):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if msg.get("role") == "assistant" and "toolUse" in item:
                uses[item["toolUse"].get("toolUseId")] = item["toolUse"]
            result = item.get("toolResult") if msg.get("role") == "user" else None
            if result:
                use = uses.get(result.get("toolUseId"), {})
                calls.append({"name": use.get("name", ""), "input": dict(use.get("input") or {}),
                              "status": result.get("status", ""),
                              "text": "\n".join(block["text"] for block in result.get("content", [])
                                                if isinstance(block, dict) and block.get("text"))})
    return calls


def accounts_total(agent, tool, empty: tuple[str, ...] = ()) -> str:
    """One total over the accounts a question listed findings for, e.g. GuardDuty in all three accounts.

    The sub-agent lists each account's findings in a separate call of `tool`, and the supervisor
    model, adding the lists up itself, got the sum wrong. The total is given only when
    it is right: every list call succeeded, each was for a different account, and all asked the
    same otherwise (same filters and regions, defaults filled in; `max_results` aside).

    Args:
        agent: The sub-agent, after it answered the question.
        tool: The Strands tool whose calls to add up (e.g. `query_guardduty_findings`); its
            `account_id` and `action` parameters tell the calls apart.
        empty: Beginnings of the tool's "no findings here" messages (see `combined_count_line`).

    Returns:
        A line such as "Accounts 111111111111 (main), 222222222222 (dev) and 333333333333 (prod)
        together: Findings: 23 (…)"; "" when there is no right total (logged, with why).
    """
    defaults = {name: p.default for name, p in inspect.signature(tool).parameters.items()
                if p.default is not inspect.Parameter.empty}
    calls = [(c, {**defaults, **c["input"]}) for c in _tool_calls(agent) if c["name"] == tool.tool_name]
    lists = [(c, ask) for c, ask in calls if ask.get("action", "list_findings") == "list_findings"]
    if len(lists) < 2:
        return ""
    accounts = [str(ask.get("account_id", "")) for _, ask in lists]
    scopes = [{k: v for k, v in ask.items() if k not in ("account_id", "max_results")} for _, ask in lists]
    if any(c["status"] == "error" for c, _ in lists):
        why = "a call failed"
    elif len(set(accounts)) < len(accounts) or any(scope != scopes[0] for scope in scopes):
        why = "the calls asked different things"
    else:
        names = [f"{a} ({vars.ACCOUNTS[a]['label']})" if a in vars.ACCOUNTS else a for a in accounts]
        line = combined_count_line([c["text"] for c, _ in lists], f"Accounts {joined(names)} together", empty)
        if line:
            print(f"[LTTM:Totals] {tool.tool_name}: {line}", flush=True)
            return line
        why = "a list could not be counted"
    print(f"[LTTM:Totals] {tool.tool_name}: no total over {len(lists)} accounts — {why}", flush=True)
    return ""


def _with_more_rows(text: str, kept: int) -> str:
    """Mark a row list that SQLRewriteHook cut, with a last element {vars.MORE_ROWS_KEY: kept}.

    format_athena_rows turns the mark into its "more than N rows" line.

    Args:
        text: A tool result's text: a JSON row list, or any other text.
        kept: How many rows SQLRewriteHook kept.

    Returns:
        The row list with the mark added, as JSON; a text that is not a row list, as it is.
    """
    try:
        rows = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return json.dumps(rows + [{vars.MORE_ROWS_KEY: kept}]) if isinstance(rows, list) else text


def _extract_raw_results(agent, discovery_tools: tuple[str, ...] = ()) -> list[str]:
    """Return the text of every tool result of the agent's newest question, in order.

    Only messages after the newest user question count, so an earlier
    question's rows can never come back. Every result is kept, so a question
    that queried several accounts returns all of them. Failed results (status
    "error": a `SQLValidatorHook` cancel or a tool exception) are dropped when
    at least one result succeeded, and returned when none did, so the caller
    still sees why.

    Args:
        agent: A Strands `Agent` (anything with a `messages` list). A missing
            or empty `messages` attribute gives an empty list.
        discovery_tools: Names of lookup tools (e.g. `list_log_groups`) whose
            results were a step towards the answer, not the answer: they are
            dropped when another tool ran, and kept when they are all there is.

    Returns:
        The texts of the successful tool results, in order; if none succeeded,
        the texts of the failed ones. When the real query failed and only a
        lookup succeeded, the failed texts: they say why, the lookup does not.
        Never None. A row list that SQLRewriteHook cut (its result carries
        `vars.MORE_ROWS_KEY`) ends with the mark of `_with_more_rows`.
    """
    names = {}
    succeeded, failed = [], []
    for msg in _newest_question(agent):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if msg.get("role") == "assistant" and "toolUse" in item:
                names[item["toolUse"].get("toolUseId")] = item["toolUse"].get("name", "")
            result = item.get("toolResult") if msg.get("role") == "user" else None
            if not result:
                continue
            text = "\n".join(block["text"] for block in result.get("content", [])
                             if isinstance(block, dict) and block.get("text"))
            if text and result.get(vars.MORE_ROWS_KEY):
                text = _with_more_rows(text, result[vars.MORE_ROWS_KEY])
            if text:
                entry = (names.get(result.get("toolUseId"), ""), text)
                (failed if result.get("status") == "error" else succeeded).append(entry)

    answers = [text for name, text in succeeded if name not in discovery_tools]
    if answers:
        return answers
    if any(name not in discovery_tools for name, _ in failed) and discovery_tools:
        return [text for _, text in failed]
    return [text for _, text in succeeded] or [text for _, text in failed]


def _extract_raw_result(agent) -> str:
    """Return every tool result of the agent's newest question as one string, separated by blank lines.

    Args:
        agent: A Strands Agent, or anything with a `messages` list.

    Returns:
        The texts of _extract_raw_results, joined; "" when there are none.
    """
    return "\n\n".join(_extract_raw_results(agent))


def format_raw_results(results: list[str]) -> str:
    """Format each Athena row list with `format_athena_rows`, keep other text as is, and join them.

    Args:
        results: Tool result texts, as _extract_raw_results returns them.

    Returns:
        The parts, separated by blank lines; "" for an empty list.
    """
    parts = []
    for text in results:
        try:
            rows = json.loads(text)
            if isinstance(rows, list):
                parts.append(format_athena_rows(rows))
                continue
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        parts.append(text)
    return "\n\n".join(parts)
