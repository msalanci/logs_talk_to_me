# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
memory_hook.py — AgentCore Memory integration hook for the supervisor agent.

Registered in supervisor_agent.py as hook LTTMMemoryHook — only when the runtime has a memory ID
(LTTM_MEMORY_ARN or BEDROCK_AGENTCORE_MEMORY_ID).

Talks to AgentCore Memory directly with the SDK's MemoryClient, through a Strands HookProvider,
instead of a session manager. The session ID, the --clean flag and the test-run marker of the
question being answered come from its RequestContext (utils/request_context.py).

Each strategy's namespace is read from the strategy's own configuration (terraform/agents.tf), not
repeated here. The API matches namespaces by prefix, and a session's namespace holds both its summary
and its episodes, so summaries and reflections are asked for by strategy and record type. Reflections
live per actor, so they carry across sessions.
"""

import os
from typing import Any

from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

from bedrock_agentcore.memory import MemoryClient

import utils.agent_vars as vars
import utils.request_context as request_context
from plugins.supervisor_steering import CHECK_PREFIX

HISTORY_MESSAGES = 10
SUMMARY_TOPICS = 3
RECORD_TYPE_KEY = "x-amz-agentcore-memory-recordType"


def _text_of(content) -> str:
    """Join all text blocks of a Strands message's content.

    Args:
        content: A message's content: a plain string, or a list of content blocks.

    Returns:
        The text blocks joined with newlines; "" when there is no text (a tool call or a tool result).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content if isinstance(block, dict) and block.get("text"))
    return ""


def _alternating(messages: list[tuple[str, str]]) -> list[dict]:
    """Turn saved messages into a conversation the model accepts.

    Roles must alternate user/assistant, start with a question and end with an answer. Of two saved
    messages in a row with the same role, the later one is kept: an assistant's final answer after its
    "let me check", or the question that followed one left without an answer.

    Args:
        messages: Saved (role, text) pairs, oldest first. Roles are USER or ASSISTANT in any case;
            pairs with another role or no text are skipped.

    Returns:
        Strands messages ({"role": …, "content": [{"text": …}]}) that alternate, start with "user" and
        end with "assistant"; an empty list when no such conversation is left.
    """
    conversation: list[dict] = []
    for role, text in messages:
        role = {"USER": "user", "ASSISTANT": "assistant"}.get((role or "").upper())
        if not role or not text:
            continue
        message = {"role": role, "content": [{"text": text}]}
        if conversation and conversation[-1]["role"] == role:
            conversation[-1] = message
        else:
            conversation.append(message)
    while conversation and conversation[0]["role"] != "user":
        conversation.pop(0)
    while conversation and conversation[-1]["role"] != "assistant":
        conversation.pop()
    return conversation


class LTTMMemoryHook(HookProvider):
    """AgentCore Memory integration: adds long-term context to the prompt, and saves every message.

    On the first user message a process sees, retrieves from AgentCore and keeps:
      - LTM semantic facts (top 5) in <user_context> tags (cross-session knowledge);
      - episodic reflections (top 3) in <agent_reflections> tags (lessons learned across this
        actor's sessions).
    On every user message, puts back any kept block the system prompt is missing: the supervisor
    rebuilds its prompt with today's date before each question.

    A process that starts on an existing session (AgentCore stops one after 15 minutes idle) gets the
    session's latest messages back, plus its summary (load_session_history).

    On every message (user and assistant), saves its text (cut to 5000 characters) to AgentCore.
    Saves happen even under --clean; only retrieval is skipped. Never saved: anything of
    a test run, and a code check's correction.

    The boto3 MemoryClient is created on first use, so a process that never touches memory pays no
    start-up cost for it.
    """

    def __init__(self, memory_id: str, actor_id: str = "lttm-admin"):
        """Set up the memory hook for one AgentCore Memory resource.

        Args:
            memory_id: AgentCore memory resource ID (in the runtime, the value of LTTM_MEMORY_ARN or
                BEDROCK_AGENTCORE_MEMORY_ID). Must match a memory resource the execution role may read
                and write: a wrong ID makes every retrieve and save fail with a logged warning, not an
                exception.
            actor_id: Actor identifier recorded on every saved event. Defaults to "lttm-admin";
                override it to tag events by a different user or automation source.

        Returns:
            None.
        """
        self.memory_id = memory_id
        self.actor_id = actor_id
        self._client = None
        self._context_loaded = False
        self._context_blocks: list[str] = []
        self._strategies: dict[str, dict] | None = None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the callbacks for AgentInitializedEvent and MessageAddedEvent.

        Args:
            registry: The supervisor's HookRegistry.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            None.
        """
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        registry.add_callback(MessageAddedEvent, self.on_message_added)

    def on_agent_initialized(self, event) -> None:
        """Log that the hook is registered; the memory work waits for on_message_added.

        Args:
            event: The AgentInitializedEvent of the supervisor. Not read.

        Returns:
            None. Prints one [LTTM:Memory] line.
        """
        print("[LTTM:Memory] Hook registered on agent", flush=True)

    def _strategy(self, strategy_type: str) -> dict:
        """Find this memory's strategy of a type, as GetMemory returns it.

        The strategies are looked up once per process. A failed lookup is not kept, so the next
        question tries again.

        Args:
            strategy_type: The strategy type, such as "EPISODIC" or "SUMMARIZATION".

        Returns:
            The strategy's description from GetMemory; {} when the memory has none of that type, or
            when the lookup failed (logged).
        """
        if self._strategies is None:
            try:
                strategies = self._get_client().get_memory_strategies(self.memory_id)
            except Exception as e:
                print(f"[LTTM:Memory] Strategy lookup failed: {e}", flush=True)
                return {}
            self._strategies = {(s.get("type") or s.get("memoryStrategyType") or ""): s for s in strategies}
        return self._strategies.get(strategy_type, {})

    def _strategy_id(self, strategy_type: str) -> str:
        """Find the ID of this memory's strategy of a type.

        Args:
            strategy_type: The strategy type, such as "EPISODIC" or "SUMMARIZATION".

        Returns:
            The strategy's ID; "" when the memory has no strategy of that type.
        """
        strategy = self._strategy(strategy_type)
        return strategy.get("strategyId") or strategy.get("memoryStrategyId") or ""

    def _namespace(self, strategy_type: str, session_id: str, reflections: bool = False) -> str:
        """Build the namespace where a strategy keeps its records for this actor and session.

        The template comes from the strategy's own configuration. The summaries' {sessionId} is one
        session; the reflections' /strategy/{memoryStrategyId}/actor/{actorId}/ spans all of this
        actor's sessions.

        Args:
            strategy_type: The strategy type, such as "EPISODIC" or "SUMMARIZATION".
            session_id: The session whose namespace is wanted.
            reflections: True for the EPISODIC strategy's reflections instead of its episodes.

        Returns:
            The namespace with its placeholders filled in; "" when the strategy, or its reflection
            configuration, has no namespace template.
        """
        config = self._strategy(strategy_type)
        if reflections:
            config = ((config.get("configuration") or {}).get("reflection") or {}).get("episodicReflectionConfiguration") or {}
        templates = config.get("namespaceTemplates") or config.get("namespaces") or []
        if not templates:
            return ""
        return (templates[0].replace("{memoryStrategyId}", self._strategy_id(strategy_type))
                .replace("{actorId}", self.actor_id).replace("{sessionId}", session_id))

    def _retrieve_texts(self, namespace: str, query: str, top_k: int, strategy_type: str = "",
                        record_type: str = "") -> list[str]:
        """Find the texts of the memory records most relevant to a query.

        The API matches the namespace as a prefix, and one namespace holds several strategies'
        records (a session's summary next to its episodes and reflections). With a strategy type, and
        a record type, AgentCore returns only those records; without one, every record under the
        namespace (the facts in "default").

        Args:
            namespace: The namespace prefix to search under.
            query: The text the records should be relevant to.
            top_k: The most records to return.
            strategy_type: Only records of this strategy type ("" for every record under the namespace).
            record_type: With a strategy type, only records of this type: "BASE" or "REFLECTION".

        Returns:
            The records' texts in the order AgentCore returns them, blanks left out; an empty list when
            the memory has no strategy of the asked type.

        Raises:
            Exception: Any error of the AgentCore calls. It is not caught here: every caller catches
                and logs it.
        """
        client = self._get_client()
        if not strategy_type:
            records = client.retrieve_memories(memory_id=self.memory_id, namespace=namespace, query=query, top_k=top_k)
        else:
            strategy_id = self._strategy_id(strategy_type)
            if not strategy_id:
                return []
            criteria = {"searchQuery": query, "topK": top_k, "memoryStrategyId": strategy_id}
            if record_type:
                criteria["metadataFilters"] = [{"left": {"metadataKey": RECORD_TYPE_KEY}, "operator": "EQUALS_TO",
                                                "right": {"metadataValue": {"stringValue": record_type}}}]
            records = client.retrieve_memory_records(memoryId=self.memory_id, namespace=namespace,
                                                     searchCriteria=criteria).get("memoryRecordSummaries", [])
        texts = []
        for record in records or []:
            content = record.get("content", {})
            text = content.get("text", "").strip() if isinstance(content, dict) else str(content).strip()
            if text:
                texts.append(text)
        return texts

    def _apply_context(self, agent) -> None:
        """Put back every block this hook added that the system prompt is missing.

        A prompt in content blocks with a cache point keeps its blocks, and the memory block goes after
        them, so memory is never cached and does not break the prompt cache. A plain text prompt is
        extended with the block. Under --clean nothing is added.

        Args:
            agent: The supervisor agent, whose system prompt is extended.

        Returns:
            None. Changes agent.system_prompt, and logs how many blocks it added.
        """
        if request_context.current().clean:
            return
        missing = [block for block in self._context_blocks if block not in (agent.system_prompt or "")]
        for block in missing:
            content = getattr(agent, "system_prompt_content", None)
            if isinstance(content, list) and any("cachePoint" in item for item in content):
                agent.system_prompt = content + [{"text": block}]
                continue
            agent.system_prompt = (agent.system_prompt or "") + block
        if missing:
            print(f"[LTTM:Memory] Memory context added to this question's prompt: {len(missing)} block(s)", flush=True)

    def load_session_history(self, agent, session_id: str, question: str = "") -> int:
        """Give a process that starts on an existing session that session's latest messages back.

        AgentCore stops a session's process after 15 minutes idle, and the next question of the session
        starts a new process whose agent has no messages. The saved events are put in time order (by
        their timestamps, whatever order the API returns), and the latest HISTORY_MESSAGES become the
        agent's conversation: a saved message holds at most 5000 characters, so 10 of them stay under
        about 50,000. The session's summary, if AgentCore has made one, is kept as a prompt
        block. Under --clean the messages still come back (--clean keeps the session's conversation and
        leaves out what memory adds), but the summary does not.

        Args:
            agent: The supervisor agent. Skipped when it already holds a conversation.
            session_id: The session whose saved events are read.
            question: The new question, used to pick the most relevant summary topics.

        Returns:
            The number of messages loaded; 0 when nothing was loaded (the agent had a conversation, the
            session has no saved messages, or reading them failed, which is logged).
        """
        if getattr(agent, "messages", None):
            return 0
        try:
            events = self._get_client().list_events(memory_id=self.memory_id, actor_id=self.actor_id,
                                                    session_id=session_id, max_results=100, include_payload=True)
        except Exception as e:
            print(f"[LTTM:Memory] Session history not loaded: {e}", flush=True)
            return 0
        saved = []
        for event in sorted(events or [], key=lambda e: str(e.get("eventTimestamp", ""))):
            for item in event.get("payload", []):
                message = item.get("conversational") if isinstance(item, dict) else None
                if message:
                    saved.append((message.get("role"), _text_of([message.get("content", {})])))
        conversation = _alternating(saved[-HISTORY_MESSAGES:])
        if not conversation:
            return 0
        agent.messages = conversation
        print(f"[LTTM:Memory] Session history loaded: {len(conversation)} messages of session {session_id[:8]}", flush=True)
        if request_context.current().clean:
            return len(conversation)
        try:
            namespace = self._namespace("SUMMARIZATION", session_id)
            topics = self._retrieve_texts(namespace, question or "conversation so far", SUMMARY_TOPICS,
                                          "SUMMARIZATION") if namespace else []
            if topics:
                topics_text = "\n".join(topics)
                self._context_blocks.append(
                    "\n\n<session_summary>\n"
                    "Summary of this session's earlier conversation (from AgentCore Memory). Use it only to "
                    "understand follow-up questions; it is not data to present.\n"
                    f"{topics_text}\n"
                    "</session_summary>")
                print(f"[LTTM:Memory] Session summary added: {len(topics)} topic(s)", flush=True)
        except Exception as e:
            print(f"[LTTM:Memory] Session summary not loaded: {e}", flush=True)
        return len(conversation)

    def on_message_added(self, event) -> None:
        """Load and keep memory context for user questions, then save the message to AgentCore Memory.

        On the first question a process sees without --clean, retrieves the facts and the episodic
        reflections and keeps them as prompt blocks. --clean holds for its own question
        only, so a later question without it still gets them. On every question, the kept blocks go
        back into the prompt. The message is then saved, except under a test run, and except a code
        check's correction: Strands adds that as a user message, and AgentCore would make long-term
        facts of it, as the user's own words.

        Args:
            event: The MessageAddedEvent of the message just added to the supervisor's conversation.
                Its agent holds the messages and the system prompt.

        Returns:
            None. May change the agent's system prompt, and writes an event to AgentCore Memory.
            Failed retrievals and saves are logged, never raised.
        """
        session_id = request_context.current().session_id

        client = self._get_client()
        msg = event.agent.messages[-1]
        role = msg.get("role", "user")
        is_question = role == "user" and bool(_text_of(msg.get("content", [])))

        if is_question and not self._context_loaded:
            if request_context.current().clean:
                print("[LTTM:Memory] Skipping retrieval (--clean flag)", flush=True)
            else:
                self._context_loaded = True
                query = _text_of(msg.get("content", [])) or "general"
                try:
                    facts = self._retrieve_texts("default", query, 5)
                    if facts:
                        facts_text = "\n".join(facts)
                        self._context_blocks.append(
                            "\n\n<user_context>\n"
                            "The following facts are from the user's previous sessions. "
                            "Use them ONLY to answer questions about previous sessions or user preferences. "
                            "Do NOT use these facts to modify SQL queries, add filters, or change how you "
                            "route questions to sub-agents. They are background context only.\n"
                            f"{facts_text}\n"
                            "</user_context>"
                        )
                        print(f"[LTTM:Memory] Retrieved {len(facts)} LTM facts", flush=True)
                    else:
                        print("[LTTM:Memory] No relevant LTM facts found", flush=True)
                except Exception as e:
                    print(f"[LTTM:Memory] LTM retrieval failed: {e}", flush=True)

                try:
                    namespace = self._namespace("EPISODIC", session_id, reflections=True)
                    reflections = self._retrieve_texts(namespace, query, 3, "EPISODIC", "REFLECTION") if namespace else []
                    if reflections:
                        reflections_text = "\n".join(reflections)
                        self._context_blocks.append(
                            "\n\n<agent_reflections>\n"
                            "The following are lessons learned from past query experiences. "
                            "Use them to avoid repeating past mistakes (e.g., wrong account ID, "
                            "missing partition keys). Do NOT share these with the user. "
                            "Do NOT use these reflections to add SQL filters, modify queries, "
                            "or change how you route questions to sub-agents unless the user "
                            "explicitly asks for it.\n"
                            f"{reflections_text}\n"
                            "</agent_reflections>"
                        )
                        print(f"[LTTM:Memory] Retrieved {len(reflections)} episodic reflections", flush=True)
                    else:
                        print(f"[LTTM:Memory] No episodic reflections in {namespace or '(no reflection namespace)'}", flush=True)
                except Exception as e:
                    print(f"[LTTM:Memory] Episodic retrieval failed: {e}", flush=True)

        if is_question:
            self._apply_context(event.agent)

        if request_context.current().test_run:
            return
        try:
            text = _text_of(msg.get("content", []))
            if role == "user" and text.startswith(CHECK_PREFIX):
                print("[LTTM:Memory] Not saved: an automatic check's correction, not the user's words", flush=True)
                return
            if text:
                client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=session_id,
                    messages=[(text[:5000], role.upper())],
                )
        except Exception as e:
            print(f"[LTTM:Memory] Save failed: {e}", flush=True)

    def _get_client(self) -> MemoryClient:
        """Create the MemoryClient on first use, so the boto3 start-up cost is paid only when memory is used.

        Args:
            None.

        Returns:
            The hook's MemoryClient, in the runtime's own region (AWS_REGION, which AgentCore sets;
            vars.AGENTCORE_REGION otherwise). The same client on every later call.
        """
        if self._client is None:
            self._client = MemoryClient(region_name=os.environ.get("AWS_REGION") or vars.AGENTCORE_REGION)
            print("[LTTM:Memory] MemoryClient initialized (deferred)", flush=True)
        return self._client
