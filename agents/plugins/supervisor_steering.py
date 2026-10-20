# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
supervisor_steering.py — LLM-as-judge steering plugin for supervisor anti-hallucination.

Registered in supervisor_agent.py as plugin SupervisorSteeringHandler.

Layer B of the supervisor's checks: corrective feedback (`Guide`) instead of a
blind retry. Two check points in the supervisor lifecycle catch routing mistakes
before a tool runs and data-integrity violations after the model responds. Context
comes from the `LedgerProvider`, which holds the tool calls of the current question
only: `set_user_question()` clears it, so the integrity judge never compares an
answer with an earlier question's tool results.

What acts is code: a routing table (ROUTING_RULES), a money check (every dollar
amount backed by a cost result) and an answer check (no claim that a line written by
code rules out), each with one precise Guide. The two Claude Haiku judges run in
shadow mode: their verdicts are logged, never acted on. Every correction after the
model's answer (the money check and the answer check) starts with CHECK_PREFIX:
Strands adds it as a user message, and the prefix tells the supervisor
that it comes from the checker, so it does not address the user about it (and the
memory hook does not save it as the user's words).

Cost: ~$0.001/query (Claude Haiku), ~1-2s per check. Nova Lite was tested but
produced weaker results.

Sources:
    - Strands Steering docs: https://strandsagents.com/docs/user-guide/concepts/plugins/steering/
    - Strands blog "100% accuracy": steering with corrective feedback vs 82.5% for prompts
    - The article "5 Techniques to Stop AI Agent Hallucinations in Production"
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from strands import Agent
from strands.vended_plugins.steering import (
    Guide,
    LedgerProvider,
    ModelSteeringAction,
    Proceed,
    SteeringHandler,
    ToolSteeringAction,
)
from strands.types.content import Message
from strands.types.streaming import StopReason
from strands.types.tools import ToolUse

import utils.agent_vars as vars

from utils.answer_check import ruled_out
from utils.money import unbacked_amounts
from utils.sse_emitter import emit_status

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    """Return the current UTC date and time for the routing judge, read at call time.

    Args:
        None.

    Returns:
        The date and time as "YYYY-MM-DD HH:MM".
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


MAX_CHECKED_RESULT_CHARS = 4000
MAX_CHECKED_ANSWER_CHARS = 16000
CHECK_PREFIX = "[Automatic integrity check]"


def _guide_feedback(reply: str, default: str) -> str | None:
    """Return the feedback of a GUIDE verdict, or None when the judge's reply means VALID.

    Judges sometimes change their mind in one reply ("GUIDE: … Actually, reconsidering: VALID").
    A reply that says VALID anywhere is undecided, and both judge prompts say an undecided judge
    answers VALID, so only a reply with GUIDE and no VALID guides. Only the
    uppercase words count, so "valid" or "INVALID" in the feedback changes nothing.

    Args:
        reply: The judge's reply.
        default: The feedback to use when the reply says GUIDE with nothing after it.

    Returns:
        The text after GUIDE (or `default`); None when the reply means VALID.
    """
    guide = re.search(r"\bGUIDE\b", reply)
    if not guide or re.search(r"\bVALID\b", reply):
        return None
    return reply[guide.end():].lstrip(" :*").strip() or default


ROUTING_JUDGE_PROMPT = f"""You validate AI agent tool routing decisions. The supervisor agent is about to call a sub-agent tool. Check if the routing is correct.

CRITICAL RULE — DEFAULT TO VALID: When in doubt, ALWAYS respond VALID. False positives (blocking a valid tool) are FAR WORSE than false negatives (allowing a questionable tool). The supervisor is intelligent and makes deliberate routing choices.

IMPORTANT — MULTI-AGENT QUERIES: The supervisor often calls MULTIPLE tools for a single question. For example:
- "Who created the most expensive resources?" requires BOTH query_cur (find expensive resources) AND query_cloudtrail (find who created them)
- "Who changed the security group and what changed?" requires BOTH query_cloudtrail AND query_config
- "Is there an outage causing errors?" requires BOTH query_health AND query_cloudwatch

Each tool handles a DIFFERENT PART of the question. If the tool is reasonable for ANY part of the user's question, respond VALID. Do NOT block a tool just because another tool seems more primary.

IMPORTANT — SPLIT CALLS: The supervisor may split one question into several calls of the SAME tool, one per account or region, sent together (e.g. "all three accounts" → one call per account). You see only one of those calls. Judge it only on whether ITS account, region and time range belong to the question. NEVER object that a single call does not cover every account or region the user asked about.

Compare the user's question against the tool being called:
1. ROUTING: Is this tool relevant to ANY part of the question?
   - Cost/billing/spend/expensive → query_cur
   - API activity/who did what/who created/who changed → query_cloudtrail
   - Configuration changes/what changed/current state → query_config
   - Application logs/Lambda output/errors in logs → query_cloudwatch
     (Route 53 DNS query logs are CloudWatch logs too. query_cloudwatch finds log group names itself, so a question that names no log group is VALID.)
   - External access/publicly accessible → query_access_analyzer
   - Sensitive data/PII/credentials in S3/data classification → query_macie
   - Vulnerabilities/CVEs/unpatched packages/Lambda/EC2/ECR vulnerabilities → query_inspector
   - Service health/outages → query_health
   - Org structure/SCPs/accounts → query_organizations
   - Service limits/quotas → query_quotas
   - Network traffic/rejected packets/flow logs → query_flowlogs
   - Threats/suspicious activity → query_guardduty
   - Control Tower/landing zone/Control Tower controls (guardrails), baselines, drift, what Control Tower did and who started it → query_controltower

2. ACCOUNT: Is the correct AWS account being referenced?
   - {vars.ACC1_ID} = {vars.ACC1_LABEL} account
   - {vars.ACC2_ID} = {vars.ACC2_LABEL} account
   - {vars.ACC3_ID} = {vars.ACC3_LABEL} account

3. TIME RANGE: Is the correct time period being referenced? Judge it against the current UTC date and time given with the call. A relative range such as "last 24 hours" or "last 7 days" can span several calendar days, including yesterday. For "last N", "latest" or "most recent" questions without a time range, any month up to 6 months back is VALID — the search walks back until it finds N results.

Rules:
- Respond with exactly VALID or GUIDE: <specific corrective feedback>
- When uncertain, respond VALID — false positives are worse than false negatives
- If the tool is not a query_* tool, respond VALID
- If the question spans multiple domains (e.g., "who created expensive resources"), ALL relevant tools are VALID
- NEVER block query_cloudtrail for questions containing "who" — CloudTrail is always valid for identity questions
- Be concise — one line verdict only"""

INTEGRITY_JUDGE_PROMPT = f"""You validate that a supervisor agent faithfully forwarded tool results to the user.

Compare the tool result against the supervisor's response:
1. DATA COMPLETENESS (H2): Did the supervisor drop, summarize, or compress data rows?
   - If the tool returned 17 rows but the supervisor only shows 9, that's a violation.
   - The supervisor MUST include ALL data rows from the tool result.
   - Minor formatting differences (whitespace, headers) are OK.

2. DATA FABRICATION (H3): Did the supervisor invent data rows that weren't in the tool result?
   - If the supervisor mentions specific events, findings, or records with details not in the tool result, that's fabrication.
   - Adding a one-line header like "CloudTrail results:" is OK.
   - Paraphrasing error messages is OK.

CRITICAL — SUMMARIES AND ANALYSIS ARE ALLOWED:
   - The supervisor is EXPECTED to add a plain English summary, analysis, or interpretation AFTER the raw data rows.
   - Summaries that reference data FROM the tool result are NOT fabrication — they are analysis.
   - Observations like "duplicate timestamps may indicate a retry" are valid interpretations, NOT fabrication.
   - Only flag fabrication if the supervisor invents data rows or specific values NOT present in the tool result.
   - A "Summary" or "Note" section after the raw data is VALID behavior.

Rules:
- Respond with exactly VALID or GUIDE: <specific corrective feedback>
- The corrective feedback should tell the agent WHAT to fix (e.g., "Your response dropped 8 of 17 data rows. Forward the tool result VERBATIM.")
- GUIDE only when you can quote the specific row or value that is missing or invented. A count that seems off is not enough on its own — counting long lists is error-prone, so check row by row before you GUIDE.
- When a tool result holds both a list of discovered names (for example available log groups) and query rows, the list is context for the query, not data rows the response must repeat.
- Each tool result is headed by the question it answered. Accounts, dates and names from that question are not invented when the response uses them to label that result. Nor are the account labels: {vars.ACC1_ID} = {vars.ACC1_LABEL}, {vars.ACC2_ID} = {vars.ACC2_LABEL}, {vars.ACC3_ID} = {vars.ACC3_LABEL}.
- When a result counts or lists per account, an account named in the question but missing from the rows had none: showing it as 0 or "none" is not invented.
- Lines such as "Findings: N (…)", "… together: Findings: N (…)" and "Results: N rows returned." are counted by code and are correct. Check the response's totals against these lines; never GUIDE because your own count of a list differs from them.
- Money in cost results is written by code: an amount such as "$242.78 (exact: 242.7750241034005)" and a line "Total (computed by code) of … over the N rows: $…" are correct. A response that shows the dollar value without the exact one, or states that total, has neither changed nor invented data.
- When uncertain, respond VALID — false positives are worse than false negatives
- If the tool result is an error or "no results found", respond VALID
- If the tool result is short (<200 chars), respond VALID — not enough to compress
- Sometime multiple subagents are being called (multi-agent setup) and supervisor module combines responses from more subagents. If multiple tools were called, the response may combine data from all of them. Only flag fabrication if data appears that wasn't in ANY tool result.
- Be concise — one line verdict only"""


ROUTING_RULES = (
    (re.compile(r"\b(dns|nxdomain|route\s*53|resolver quer\w*|domain names?|queried domains?)\b", re.I),
     re.compile(r"\b(flow[ -]?logs?|ports?(?: 53)?|traffic|ips?|ip address(?:es)?|udp|tcp|packets?|bytes|"
                r"connections?|rejected|accepted|enis?|network interfaces?)\b", re.I),
     "query_flowlogs",
     "DNS questions (domain names, NXDOMAIN, Route 53 Resolver queries) are answered from the Route 53 Resolver "
     "query logs in CloudWatch: use query_cloudwatch. VPC flow logs record IP traffic and have no domain names."),
)


def routing_rule(question: str, tool: str) -> str | None:
    """Find the corrective feedback when a routing rule says this tool cannot answer the question.

    Each rule of ROUTING_RULES holds what the question is about, what makes the rule not apply, the tool that
    cannot answer it, and the corrective feedback. The rules are the judges' one demonstrated catch, kept as
    code: the routing judge rightly stopped query_flowlogs for "the most queried domain names". A question
    about network traffic ("which IPs sent traffic to our DNS resolver", "was DNS traffic rejected") is
    answered only by flow logs, so the DNS rule does not apply to it. Only rules with such evidence belong
    here: a broad keyword rule such as "login → CloudTrail" would misroute "login errors in our Lambda logs".

    Args:
        question: The user's question.
        tool: The tool the supervisor is about to call.

    Returns:
        The feedback of the first rule that applies; None when none does.
    """
    for topic, unless, wrong_tool, feedback in ROUTING_RULES:
        if tool == wrong_tool and topic.search(question) and not unless.search(question):
            return feedback
    return None


def coverage_notes(results: list[str], answer: str) -> list[str]:
    """Find the rows of the tool results that the answer leaves out, for the log only.

    The integrity judge's job, as code; it logs until shadow data shows it can be trusted to act. A row is
    named by its first value; a row whose first value is money or a short count cannot be told apart from
    other numbers and is skipped (a 12-digit account ID is kept: it is the commonest row name). An account
    may be named in the answer by its label ("main") instead of its ID.

    Args:
        results: The texts of the question's successful query results, with "Row N:" blocks.
        answer: The supervisor's answer.

    Returns:
        The first values of the rows the answer does not name, each once, in order; an empty list when the
        answer names them all.
    """
    missing = []
    for text in results:
        for block in re.split(r"\n\s*\nRow \d+:\n", "\n\n" + text)[1:]:
            first = re.match(r"[ \t]+[^:\n]+:[ \t]*(.*)", block)
            key = first.group(1).strip() if first else ""
            if not key or re.fullmatch(r"[<>-]?\s?-?\$.*|-?\d{1,6}(?:\.\d+)?", key):
                continue
            label = vars.ACCOUNTS.get(key, {}).get("label") if isinstance(vars.ACCOUNTS.get(key), dict) else None
            if key not in answer and not (label and re.search(rf"\b{re.escape(label)}\b", answer, re.I)):
                missing.append(key)
    return list(dict.fromkeys(missing))


def _session_cost_results(agent) -> list[str]:
    """Collect this session's cost results, from the agent's tool results.

    Failed calls are left out. No user text is read: Strands writes a conversation summary as a user message,
    and its amounts must not back an answer; the session's questions come from the handler.

    Args:
        agent: The supervisor agent (anything with a `messages` list).

    Returns:
        The texts of every successful query_cur result in the session, in order; an empty list when there
        is none.
    """
    results, names = [], {}
    for message in getattr(agent, "messages", None) or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if message.get("role") == "assistant" and isinstance(block.get("toolUse"), dict):
                names[block["toolUse"].get("toolUseId")] = block["toolUse"].get("name", "")
            elif message.get("role") == "user" and isinstance(block.get("toolResult"), dict):
                result = block["toolResult"]
                if names.get(result.get("toolUseId")) == "query_cur" and result.get("status") != "error":
                    results += [b["text"] for b in result.get("content", []) if isinstance(b, dict) and b.get("text")]
    return results


class SupervisorSteeringHandler(SteeringHandler):
    """Two-stage LLM judge that catches wrong routing and result manipulation.

    Before each query_* tool call, runs a Claude Haiku judge against the
    user's question to validate correct sub-agent routing, correct AWS
    account, and correct time range (catches H4 and H5). On mismatch,
    returns `Guide` with specific corrective feedback instead of blocking
    the call outright.

    After the supervisor generates its final response, runs a second judge
    that compares the response against all successful tool results from the
    ledger. Flags data compression (H2 — tool returned 17 rows, response
    shows 9) and fabrication (H3 — response invents rows not in any tool
    result).

    Both judges run in shadow mode: their verdicts are logged, never acted on.
    What acts is code: the routing table, the money check and the answer check.

    Each session's supervisor has its own handler, so the per-question
    flags (`_tool_called_this_turn`, `_after_model_guide_count`) are instance state,
    reset per question by `set_user_question()`. They must not be class-level, which
    every session in the process would share: Strands registers the given plugin
    instance and never re-creates it, also during retries.

    Usage:
        handler = SupervisorSteeringHandler()
        handler.set_user_question(question)
        supervisor_agent = Agent(tools=[...], plugins=[handler])
    """

    def __init__(self):
        """Initialize the handler with a LedgerProvider for tool-call history context.

        Args:
            None.

        Returns:
            None. Starts with no question, no tools guided (each tool is guided at most once per question,
            against loops), no query tool run and no GUIDE given for this question, and no session
            questions (the newest is kept last).
        """
        super().__init__(context_providers=[LedgerProvider()])
        self._user_question: str = ""
        self._guided_tools: set = set()
        self._tool_called_this_turn = False
        self._after_model_guide_count = 0
        self._session_questions: list[str] = []

    def set_user_question(self, question: str) -> None:
        """Set the current user question and reset all per-invocation state.

        Args:
            question: The user's original question text, used by the routing
                judge as context for validating each tool call. Calling this
                method also clears the tool-call ledger, the guided-tools
                set, and the two flags that track
                whether a tool was called this turn and how many GUIDE
                verdicts the integrity judge has issued this invocation.
                Must be called once per user question, before
                `supervisor_agent(question)`.

        Returns:
            None. Also adds the question to the session's last 50. The ledger is emptied, or it would
            keep every tool call of the session and the integrity judge would compare a follow-up answer
            with an earlier question's rows; an empty ledger is rebuilt on the next tool call.
        """
        self._user_question = question
        self._session_questions = (self._session_questions + [question])[-50:]
        self.steering_context.data.set("ledger", {})
        self._guided_tools = set()
        self._tool_called_this_turn = False
        self._after_model_guide_count = 0

    async def steer_before_tool(
        self, *, agent: "Agent", tool_use: ToolUse, **kwargs: Any
    ) -> ToolSteeringAction:
        """Run the routing judge before each query_* tool call; return Guide on mismatch.

        Catches H4 (wrong routing) and H5 (wrong account or time range).
        Example: user asks "how much did EC2 cost?" and the supervisor is
        about to call `query_cloudtrail` — the judge returns
        `GUIDE: use query_cur instead`.

        Non-query tools and tools already guided once this invocation fall
        through to `Proceed` to prevent infinite loops. Uses Claude Haiku
        via the ROUTING_JUDGE_PROMPT (Nova Lite was tested but produced
        weaker results), built without the CountTokens call.

        Routing is corrected by code: when the routing table says the tool cannot answer the question,
        the call gets one Guide, once per tool and question (the supervisor may insist). The judge runs in
        shadow mode: most of its objections in saved runs were wrong (one turned 7 failed WorkMail
        sign-ins into "zero"), so its verdict is logged and never acted on. Its token usage goes on the
        question's receipt. A failure of the judge never blocks the call.

        Args:
            agent: The supervisor agent. Not read.
            tool_use: The tool call about to run: its name and input.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            Guide with the routing table's feedback; Proceed otherwise.
        """
        tool_name = tool_use.get("name", "")

        if tool_name not in vars.QUERY_TOOLS:
            return Proceed(reason=f"Non-query tool: {tool_name}")

        if tool_name in self._guided_tools:
            print(f"[LTTM:Steering] SKIP — {tool_name} already guided once, allowing", flush=True)
            return Proceed(reason=f"Already guided {tool_name} — allowing to prevent loop")

        if not self._user_question:
            return Proceed(reason="No user question set")

        self._tool_called_this_turn = True

        feedback = routing_rule(self._user_question, tool_name)
        if feedback:
            print(f"[LTTM:Steering] GUIDE before_tool — {tool_name}: [routing table] {feedback}", flush=True)
            self._guided_tools.add(tool_name)
            return Guide(reason=feedback)

        tool_input = tool_use.get("input", {})
        tool_question = tool_input.get("question", str(tool_input))

        try:
            judge = Agent(
                model=vars.build_bedrock_model(vars.JUDGE_MODEL),
                system_prompt=ROUTING_JUDGE_PROMPT,
            )
            judged = judge(
                f"Current date and time (UTC): {_utc_now()}\n\n"
                f"User's original question: {self._user_question}\n\n"
                f"Tool being called: {tool_name}\n\n"
                f"Question passed to tool: {tool_question}"
            )
            vars.extract_token_usage(judged, "routing_judge")
            verdict = str(judged).strip()

            reason = _guide_feedback(verdict, "Routing issue detected")
            if reason:
                print(f"[LTTM:Steering] SHADOW GUIDE before_tool — {tool_name}: {reason}", flush=True)
            else:
                print(f"[LTTM:Steering] VALID before_tool — {tool_name}", flush=True)
            return Proceed(reason=f"Routing judge in shadow mode for {tool_name}")

        except Exception as e:
            print(f"[LTTM:Steering] ERROR before_tool — {tool_name}: {e}", flush=True)
            return Proceed(reason=f"Steering error: {e}")

    async def steer_after_model(
        self, *, agent: "Agent", message: Message, stop_reason: StopReason, **kwargs: Any
    ) -> ModelSteeringAction:
        """Run the integrity judge after the supervisor's final response; return Guide on violations.

        Catches H2 (data compression — e.g., tool returned 17 rows but the
        response shows 9) and H3 (data fabrication — response invents rows
        that weren't in any tool result). Uses Claude Haiku on the whole
        tool result and the whole answer, never a preview: rows past a preview look invented, and correct
        rows were then removed from answers.

        Skipped when: no tool was called this turn (conversational response; this must be the first
        check), the tool result is too short to meaningfully check (<200 chars), the
        tool result or the answer is too large (>4000 / >16000 chars), or the
        judge has already guided once this invocation (loop prevention).

        Before the judge, in this order:
        - At most one GUIDE per question: other hooks (OutputIntegrityHook's retry) can make the model
          answer again, and a check that kept guiding would loop. After a GUIDE, the corrected answer is checked
          for money and for ruled-out claims again, for the log only.
        - Money is computed by code: a dollar amount that no cost result backs was computed by the model
          (one sum for dev and prod together), and gets one precise GUIDE.
        - A claim that a line written by code rules out ("No drift has been detected" for controls whose
          drift is NOT_CHECKING; a reason of the answer's own for an empty Athena result) gets one precise
          GUIDE, made by code (utils/answer_check.py). Only on the final answer (stop_reason "end_turn"):
          Strands calls this after every model turn, and a GUIDE on a turn that calls a tool would cancel
          that call.
        - Rows the answer leaves out are logged (coverage_notes), not acted on; a failure there never
          stops the steps after.
        The judge runs in shadow mode: its objections to correct answers (inferred zeros, rounding) cost a
        rewrite each and once removed correct data, so its verdict is logged and never acted on. A failure
        of the judge never blocks the answer.

        Args:
            agent: The supervisor agent, whose session's cost results back amounts too.
            message: The model's response.
            stop_reason: Why the model stopped; "end_turn" for a final answer.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            Guide with a correction that starts with CHECK_PREFIX (money or answer check); Proceed otherwise.
        """
        if not self._tool_called_this_turn:
            print("[LTTM:Steering] SKIP after_model — no tool called this turn, skipping integrity check", flush=True)
            return Proceed(reason="No tool called this turn — conversational response")

        output_text = ""
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    output_text += block["text"] + " "
        elif isinstance(content, str):
            output_text = content

        if self._after_model_guide_count >= 1:
            if output_text.strip():
                still = self._unbacked_money(agent, output_text)
                if still:
                    print(f"[LTTM:Steering] after a GUIDE the answer states money no cost result holds: "
                          f"{', '.join(still)} — let through (one GUIDE per question)", flush=True)
                again = self._ruled_out(output_text) if stop_reason == "end_turn" else None
                if again:
                    print(f"[LTTM:Steering] after a GUIDE the answer still says what a line written by code rules out "
                          f"({again[0]}): {again[1][:200]!r} — let through (one GUIDE per question)", flush=True)
            print("[LTTM:Steering] SKIP after_model — already guided once this invocation, allowing to prevent loop", flush=True)
            return Proceed(reason="Max after_model GUIDEs reached — allowing to prevent loop")

        if not output_text.strip():
            return Proceed(reason="Empty model output")

        unbacked = self._unbacked_money(agent, output_text)
        if unbacked:
            print(f"[LTTM:Steering] GUIDE after_model — money not in the cost result: {', '.join(unbacked)}", flush=True)
            self._after_model_guide_count += 1
            return Guide(reason=f"{CHECK_PREFIX} Your answer states {', '.join(unbacked)}, which no cost result holds. "
                                "Money is computed by code: do not add up, subtract, average or round amounts yourself. "
                                "Remove that amount, or use the dollar values and the \"Total (computed by code)\" line "
                                "exactly as the cost result gives them. Keep every other row and amount unchanged.")

        claim = self._ruled_out(output_text) if stop_reason == "end_turn" else None
        if claim:
            kind, sentence, correction = claim
            print(f"[LTTM:Steering] GUIDE after_model — [answer check] {kind}: {sentence[:200]!r}", flush=True)
            self._after_model_guide_count += 1
            return Guide(reason=f"{CHECK_PREFIX} {correction}")

        try:
            dropped = coverage_notes(self._results_of_all_queries(), output_text)
            if dropped:
                print(f"[LTTM:Steering] SHADOW coverage — rows not in the answer: {', '.join(dropped[:10])}", flush=True)
        except Exception as e:
            print(f"[LTTM:Steering] ERROR coverage check — {type(e).__name__}: {e}", flush=True)

        tool_result = self._get_last_tool_result()
        if not tool_result or len(tool_result) < 200:
            return Proceed(reason="Tool result too short for integrity check")

        if len(tool_result) > MAX_CHECKED_RESULT_CHARS or len(output_text) > MAX_CHECKED_ANSWER_CHARS:
            print(f"[LTTM:Steering] SKIP after_model — tool result ({len(tool_result)} chars) or answer "
                  f"({len(output_text)} chars) too large for reliable integrity check", flush=True)
            return Proceed(reason="Tool result or answer too large — skipping integrity check")

        try:
            judge = Agent(
                model=vars.build_bedrock_model(vars.JUDGE_MODEL),
                system_prompt=INTEGRITY_JUDGE_PROMPT,
            )
            judged = judge(
                f"Tool result (complete):\n{tool_result}\n\n"
                f"Supervisor response (complete):\n{output_text}"
            )
            vars.extract_token_usage(judged, "integrity_judge")
            verdict = str(judged).strip()

            reason = _guide_feedback(verdict, "Data integrity issue")
            if reason:
                print(f"[LTTM:Steering] SHADOW GUIDE after_model — {reason}", flush=True)
            else:
                print("[LTTM:Steering] VALID after_model — output integrity confirmed", flush=True)
                emit_status("LLM-as-judge confirmed response is valid, sending to user", source="supervisor_steering")
            return Proceed(reason="Integrity judge in shadow mode")

        except Exception as e:
            print(f"[LTTM:Steering] ERROR after_model — {e}", flush=True)
            return Proceed(reason=f"Steering error: {e}")

    def _unbacked_money(self, agent, output_text: str) -> list[str]:
        """Find the dollar amounts in an answer that no cost result and no question of this session backs.

        Checked only when this question has a cost result. This session's earlier cost results and questions back
        amounts a follow-up restates ("following up on the $250.17 total", "within my $300 budget"); a session
        restored from memory after a restart has no earlier results, so such a follow-up gets one GUIDE. A failure
        of the check lets the answer through to the judge instead of skipping both.

        Args:
            agent: The supervisor agent, whose session's cost results back amounts.
            output_text: The supervisor's answer.

        Returns:
            The unbacked amounts as written; an empty list when every amount is backed, when this question has
            no cost result, or when the check failed (logged).
        """
        results = self._results_of("query_cur")
        if not results:
            return []
        try:
            return unbacked_amounts(output_text, results + _session_cost_results(agent), self._session_questions)
        except Exception as e:
            print(f"[LTTM:Steering] ERROR money check — {type(e).__name__}: {e}; answer goes on to the judge", flush=True)
            return []

    def _ruled_out(self, output_text: str) -> tuple[str, str, str] | None:
        """Find the answer's first claim that a line written by code in this question's results rules out.

        A failure of the check lets the answer through to the steps after.

        Args:
            output_text: The supervisor's answer.

        Returns:
            (kind, sentence, correction) for that claim; None when there is none, or when the check failed
            (logged).
        """
        try:
            return ruled_out(self._results_of_all_queries(), output_text, self._user_question)
        except Exception as e:
            print(f"[LTTM:Steering] ERROR answer check — {type(e).__name__}: {e}; answer goes on", flush=True)
            return None

    def _results_of_all_queries(self) -> list[str]:
        """Collect the texts of this question's successful query_* calls.

        Args:
            None.

        Returns:
            The texts, tool by tool in name order; an empty list when there is none.
        """
        return [text for tool in sorted(vars.QUERY_TOOLS) for text in self._results_of(tool)]

    def _results_of(self, tool: str) -> list[str]:
        """Collect the texts of this question's successful calls of one tool, from the ledger.

        Args:
            tool: The tool's name, such as "query_cur".

        Returns:
            The result texts, in call order; an empty list when the ledger holds none.
        """
        ledger = self.steering_context.data.get("ledger")
        if not ledger or not isinstance(ledger, dict):
            return []
        texts = []
        for call in ledger.get("tool_calls", []):
            if call.get("tool_name") != tool or call.get("status") != "success":
                continue
            result = call.get("result", "")
            if isinstance(result, list):
                texts += [block["text"] for block in result if isinstance(block, dict) and "text" in block]
            else:
                texts.append(str(result))
        return texts

    def _get_last_tool_result(self) -> str:
        """Concatenate all successful query_* tool results from the ledger.

        For multi-agent queries (e.g. query_cur + query_cloudtrail), the
        supervisor combines data from multiple tools. The integrity judge
        needs to see ALL results to avoid false positives — flagging CUR
        data as "fabrication" just because it wasn't in the CloudTrail
        result. Each result is headed by the question it answered, so the
        accounts, dates and names the supervisor put into that question are
        not taken for invented.

        Args:
            None.

        Returns:
            Newline-separated concatenation of every successful query_*
            result in the current invocation, each prefixed by a
            `[tool_name("<question asked>") result]:` header (`[tool_name result]:`
            when the call had no question). Empty string if the ledger has
            no successful query_* calls.
        """
        ledger = self.steering_context.data.get("ledger")
        if not ledger or not isinstance(ledger, dict):
            return ""

        tool_calls = ledger.get("tool_calls", [])
        all_results = []
        
        for call in tool_calls:
            tool_name = call.get("tool_name", "")
            if tool_name in vars.QUERY_TOOLS and call.get("status") == "success":
                result = call.get("result", "")
                result_text = ""
                if isinstance(result, list):
                    for block in result:
                        if isinstance(block, dict) and "text" in block:
                            result_text = block["text"]
                            break
                elif isinstance(result, str):
                    result_text = result
                else:
                    result_text = str(result)
                
                if result_text:
                    args = call.get("tool_args")
                    asked = args.get("question", "") if isinstance(args, dict) else ""
                    header = f'[{tool_name}("{asked}") result]' if asked else f"[{tool_name} result]"
                    all_results.append(f"{header}:\n{result_text}")

        return "\n\n".join(all_results) if all_results else ""
