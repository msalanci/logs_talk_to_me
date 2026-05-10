# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
supervisor_steering.py — Strands SteeringHandler for supervisor anti-hallucination (LLM as judge)

Unified LLM-based steering plugin that, using Strands SteeringHandler with two check points:

    steer_before_tool():
        Runs before each query_* tool call on the supervisor.
        LLM as judge validates: correct agent routing, correct account, correct time range.
        Returns a Guide with correct feedback when failure detected (e.g., "User asked about costs,
        use query_cur not query_cloudtrail").

    steer_after_model():
        Runs after the supervisor generates its response.
        LLM as judge validates: data rows not dropped (H2), no fabricated data (H3).
        Returns a Guide with correct feedback when failure detected

Registered on the supervisor agent via plugins=[SupervisorSteeringHandler(...)].
"""

import logging
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
from utils.sse_emitter import emit_status

logger = logging.getLogger(__name__)

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
    "query_macie",
    "query_inspector",
}

# Prompt to check if supervisor is calling right agent (steer_before_tool)
ROUTING_JUDGE_PROMPT = f"""You validate AI agent tool routing decisions. The supervisor agent is about to call a sub-agent tool. Check if the routing is correct.

CRITICAL RULE — DEFAULT TO VALID: When in doubt, ALWAYS respond VALID. False positives (blocking a valid tool) are FAR WORSE than false negatives (allowing a questionable tool). The supervisor is intelligent and makes deliberate routing choices.

IMPORTANT — MULTI-AGENT QUERIES: The supervisor often calls MULTIPLE tools for a single question. For example:
- "Who created the most expensive resources?" requires BOTH query_cur (find expensive resources) AND query_cloudtrail (find who created them)
- "Who changed the security group and what changed?" requires BOTH query_cloudtrail AND query_config
- "Is there an outage causing errors?" requires BOTH query_health AND query_cloudwatch

Each tool handles a DIFFERENT PART of the question. If the tool is reasonable for ANY part of the user's question, respond VALID. Do NOT block a tool just because another tool seems more primary.

Compare the user's question against the tool being called:
1. ROUTING: Is this tool relevant to ANY part of the question?
   - Cost/billing/spend/expensive → query_cur
   - API activity/who did what/who created/who changed → query_cloudtrail
   - Configuration changes/what changed/current state → query_config
   - Application logs/Lambda output/errors in logs → query_cloudwatch
   - External access/publicly accessible → query_access_analyzer
   - Service health/outages → query_health
   - Org structure/SCPs/accounts → query_organizations
   - Service limits/quotas → query_quotas
   - Network traffic/rejected packets/flow logs → query_flowlogs
   - Threats/suspicious activity → query_guardduty

2. ACCOUNT: Is the correct AWS account being referenced?
   - {vars.ACC1_ID} = {vars.ACC1_LABEL} account
   - {vars.ACC2_ID} = {vars.ACC2_LABEL} account
   - {vars.ACC3_ID} = {vars.ACC3_LABEL} account

3. TIME RANGE: Is the correct time period being referenced?

Rules:
- Respond with exactly VALID or GUIDE: <specific corrective feedback>
- When uncertain, respond VALID — false positives are worse than false negatives
- If the tool is not a query_* tool, respond VALID
- If the question spans multiple domains (e.g., "who created expensive resources"), ALL relevant tools are VALID
- NEVER block query_cloudtrail for questions containing "who" — CloudTrail is always valid for identity questions
- Be concise — one line verdict only"""

# Prompt to check if supervisor provided the right response according to data raceived (steer_after_model)
INTEGRITY_JUDGE_PROMPT = """You validate that a supervisor agent faithfully forwarded tool results to the user.

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
- When uncertain, respond VALID — false positives are worse than false negatives
- If the tool result is an error or "no results found", respond VALID
- If the tool result is short (<200 chars), respond VALID — not enough to compress
- Sometime multiple subagents are being called (multi-agent setup) and supervisor module combines responses from more subagents. If multiple tools were called, the response may combine data from all of them. Only flag fabrication if data appears that wasn't in ANY tool result.
- Be concise — one line verdict only"""


class SupervisorSteeringHandler(SteeringHandler):
    """ 
    Two-stage LLM judge that catches wrong routing and result manipulation.

    Before each query_* tool call, runs a Claude Haiku judge against the user's question, to validate correct sub-agent routing,
    correct AWS account, and correct time range. 
    On mismatch, returns `Guide` with specific corrective feedback instead of blockingthe call outright.

    After the supervisor generates its final response, runs a second judge that compares the response against all successful tool results from the ledger. 
    Flags data compression and fabrication 
        
    Usage:        
        handler = SupervisorSteeringHandler()
        handler.set_user_question(question)
        supervisor_agent = Agent(tools=[...], plugins=[handler])
    """

    _cls_tool_called_this_turn: bool = False
    _cls_after_model_guide_count: int = 0

    def __init__(self):
        super().__init__(context_providers=[LedgerProvider()])
        self._user_question: str = ""
        self._last_tool_result: str = ""
        self._last_tool_name: str = ""
        self._guided_tools: set = set()  # Track tools already guided — prevent infinite loops

    def set_user_question(self, question: str) -> None:
        """Set the current user question. Call before each supervisor invocation."""
        self._user_question = question
        self._last_tool_result = ""
        self._last_tool_name = ""
        self._guided_tools = set()
        SupervisorSteeringHandler._cls_tool_called_this_turn = False
        SupervisorSteeringHandler._cls_after_model_guide_count = 0

    async def steer_before_tool(
        self, *, agent: "Agent", tool_use: ToolUse, **kwargs: Any
    ) -> ToolSteeringAction:
        """ 
        ROUTING JUDGE:
        Validate tool routing before execution.
        Hits before a sub-agent runs.
        Uses Claude Haiku to check if the supervisor is calling the right sub-agent with the right parameters for the user's question.
        """
        tool_name = tool_use.get("name", "")

        if tool_name not in QUERY_TOOLS:
            return Proceed(reason=f"Non-query tool: {tool_name}")

        if tool_name in self._guided_tools:
            print(f"[LTTM:Steering] SKIP — {tool_name} already guided once, allowing", flush=True)
            return Proceed(reason=f"Already guided {tool_name} — allowing to prevent loop")

        if not self._user_question:
            return Proceed(reason="No user question set")

        SupervisorSteeringHandler._cls_tool_called_this_turn = True
        tool_input = tool_use.get("input", {})
        tool_question = tool_input.get("question", str(tool_input))

        try:
            judge = Agent(
                # model=vars.US_NOVA_LITE,
                model=vars.US_HAIKU,
                system_prompt=ROUTING_JUDGE_PROMPT,
            )
            verdict = str(judge(
                f"User's original question: {self._user_question}\n\n"
                f"Tool being called: {tool_name}\n\n"
                f"Question passed to tool: {tool_question}"
            )).strip()

            if verdict.upper().startswith("GUIDE"):
                reason = verdict.split(":", 1)[1].strip() if ":" in verdict else "Routing issue detected"
                print(f"[LTTM:Steering] GUIDE before_tool — {tool_name}: {reason}", flush=True)
                self._guided_tools.add(tool_name)  # Track — won't block this tool again
                return Guide(reason=reason)
            else:
                print(f"[LTTM:Steering] VALID before_tool — {tool_name}", flush=True)
                return Proceed(reason=f"Routing validated for {tool_name}")

        except Exception as e:
            print(f"[LTTM:Steering] ERROR before_tool — {tool_name}: {e}", flush=True)
            return Proceed(reason=f"Steering error: {e}")

    async def steer_after_model(
        self, *, agent: "Agent", message: Message, stop_reason: StopReason, **kwargs: Any
    ) -> ModelSteeringAction:
        """
        INTEGRITY JUDGE:
        Validate model output for data integrity (H2 compression, H3 fabrication).
        Hits after the supervisor generates its final response.
        Uses Claude Haiku to compare the supervisor's response against the last tool result.
        What it checks: "Did the supervisor faithfully forward ALL the data without dropping rows or inventing fake data?"
        Example: Tool returned 17 rows but supervisor only shows 9 → judge says "GUIDE: you dropped 8 rows"
        """
        
        if not SupervisorSteeringHandler._cls_tool_called_this_turn:
            print("[LTTM:Steering] SKIP after_model — no tool called this turn, skipping integrity check", flush=True)
            return Proceed(reason="No tool called this turn — conversational response")

        if SupervisorSteeringHandler._cls_after_model_guide_count >= 1:
            print("[LTTM:Steering] SKIP after_model — already guided once this invocation, allowing to prevent loop", flush=True)
            return Proceed(reason="Max after_model GUIDEs reached — allowing to prevent loop")

        output_text = ""
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    output_text += block["text"] + " "
        elif isinstance(content, str):
            output_text = content

        if not output_text.strip():
            return Proceed(reason="Empty model output")

        tool_result = self._get_last_tool_result()
        if not tool_result or len(tool_result) < 200:
            return Proceed(reason="Tool result too short for integrity check")

        if len(tool_result) > 4000:
            # prevents the judge from looping on large results.
            print(f"[LTTM:Steering] SKIP after_model — tool result too large ({len(tool_result)} chars) for reliable integrity check", flush=True)
            return Proceed(reason=f"Tool result too large ({len(tool_result)} chars) — skipping integrity check")

        try:
            judge = Agent(
                # model=vars.US_NOVA_LITE,
                model=vars.US_HAIKU,
                system_prompt=INTEGRITY_JUDGE_PROMPT,
            )
            # Truncate for judge context window
            result_preview = tool_result[:3000]
            output_preview = output_text[:3000]

            verdict = str(judge(
                f"Tool result (first 3000 chars):\n{result_preview}\n\n"
                f"Supervisor response (first 3000 chars):\n{output_preview}"
            )).strip()

            if verdict.upper().startswith("GUIDE"):
                reason = verdict.split(":", 1)[1].strip() if ":" in verdict else "Data integrity issue"
                print(f"[LTTM:Steering] GUIDE after_model — {reason}", flush=True)
                SupervisorSteeringHandler._cls_after_model_guide_count += 1
                return Guide(reason=reason)
            else:
                print("[LTTM:Steering] VALID after_model — output integrity confirmed", flush=True)
                emit_status("LLM-as-judge confirmed response is valid, sending to user", source="supervisor_steering")
                return Proceed(reason="Output integrity validated")

        except Exception as e:
            print(f"[LTTM:Steering] ERROR after_model — {e}", flush=True)
            return Proceed(reason=f"Steering error: {e}")

    def _get_last_tool_result(self) -> str:
        """Extract ALL query_* tool results from the ledger context.
        
        For multi-agent queries (query_cur + query_cloudtrail), the supervisor combines data from multiple tools. 
        The integrity judge needs to see ALL results to avoid false positives (flagging CUR data as "fabrication" when only comparing against CloudTrail result).

        Returns:
            Newline-separated concatenation of every successful query_* result in the current invocation, 
            each prefixed by a`[tool_name result]:` header. Empty string if the ledger has no successful query_* calls.
        """

        ledger = self.steering_context.data.get("ledger")
        if not ledger or not isinstance(ledger, dict):
            return ""

        tool_calls = ledger.get("tool_calls", [])
        all_results = []
        
        for call in tool_calls:
            tool_name = call.get("tool_name", "")
            if tool_name in QUERY_TOOLS and call.get("status") == "success":
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
                    all_results.append(f"[{tool_name} result]:\n{result_text}")

        return "\n\n".join(all_results) if all_results else ""
