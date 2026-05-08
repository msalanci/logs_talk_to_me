# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
memory_hook.py — AgentCore Memory integration hook for the supervisor agent.

Registered in supervisor_agent.py as hook LTTMMemoryHook
Used only when LTTM_MEMORY_ARN env var is set and bedrock_agentcore.memory is importable.

Session ID is set per-request in invoke() via class variable _current_session_id.

The --clean flag in alexandra.sh sets _skip_retrieval=True to bypass LTM/episodic.
Saves into memory happen all the time
"""

from typing import Any
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from bedrock_agentcore.memory import MemoryClient


class LTTMMemoryHook(HookProvider):
    """
    AgentCore Memory integration — injects long-term context and persists messages.

    On the first user message of a session, retrieves two kinds of memories from AgentCore and appends them to the supervisor's system prompt:
      - LTM semantic facts in <user_context> tags (cross-session knowledge).
      - Episodic reflections in <agent_reflections> tags (session-specific lessons learned).

    On every message (user and assistant), saves the text (truncated to 5000 chars) back to AgentCore for future retrieval. 
    Saves happen even under --clean; only retrieval is skipped.

    The boto3 MemoryClient is lazily created on first use to avoid adding cold-start latency to containers where memory is not exercised.
    """

    _current_session_id = "default"
    _skip_retrieval = False  # Set by --clean flag in invoke()

    def __init__(self, memory_id, actor_id="lttm-admin"):
        """
        Initialize the memory hook with an AgentCore memory resource ID and actor ID.
        
        Args:
            memory_id: AgentCore memory resource ID (e.g. the value of the LTTM_MEMORY_ARN env var in the runtime). 
                       Must match a memory resource the execution role is permitted to read and write.
                       Wrong ID causes every retrieve/save call to fail silently with a logged warning, not an exception.
            actor_id: Actor identifier recorded on every saved event. 
                      Defaults to "lttm-admin"; override to tag events by a different user or automation source.
        """ 
      
        self.memory_id = memory_id
        self.actor_id = actor_id
        self._client = None
        self._context_loaded = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register callbacks for AgentInitializedEvent and MessageAddedEvent."""
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        registry.add_callback(MessageAddedEvent, self.on_message_added)

    def on_agent_initialized(self, event) -> None:
        """Log registration — real memory work is deferred to on_message_added."""
        print("[LTTM:Memory] Hook registered on agent", flush=True)

    def on_message_added(self, event) -> None:
        """Inject LTM context on first user message, then persist every message to AgentCore."""
        session_id = LTTMMemoryHook._current_session_id

        client = self._get_client()
        msg = event.agent.messages[-1]
        role = msg.get("role", "user")

        # On first user message: inject LTM context (unless --clean flag)
        if role == "user" and not getattr(self, '_context_loaded', False):
            self._context_loaded = True
            if LTTMMemoryHook._skip_retrieval:
                print("[LTTM:Memory] Skipping retrieval (--clean flag)", flush=True)
            else:
                try:
                    content = msg.get("content", [])
                    query = content[0].get("text", "general") if isinstance(content, list) and content else "general"
                    memories = client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace="default",
                        query=query,
                        top_k=5,
                    )
                    relevant = memories
                    if relevant:
                        facts = []
                        for m in relevant:
                            c = m.get("content", {})
                            text = c.get("text", "").strip() if isinstance(c, dict) else str(c).strip()
                            if text:
                                facts.append(text)
                        if facts:
                            facts_text = "\n".join(facts)
                            event.agent.system_prompt += (
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

                # Retrieve episodic reflections
                try:
                    episodic_memories = client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace=f"{session_id}",
                        query=query,
                        top_k=3,
                    )
                    if episodic_memories:
                        reflections = []
                        for m in episodic_memories:
                            c = m.get("content", {})
                            text = c.get("text", "").strip() if isinstance(c, dict) else str(c).strip()
                            if text:
                                reflections.append(text)
                        if reflections:
                            reflections_text = "\n".join(reflections)
                            event.agent.system_prompt += (
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
                            print("[LTTM:Memory] Episodic query returned results but no text content", flush=True)
                    else:
                        print("[LTTM:Memory] No episodic reflections found (expected — need 5-10+ sessions)", flush=True)
                except Exception as e:
                    print(f"[LTTM:Memory] Episodic retrieval failed (expected if no reflections yet): {e}", flush=True)

        # Save every message to memory
        try:
            content = msg.get("content", [])
            text = ""
            if isinstance(content, list) and content:
                text = content[0].get("text", "")
            elif isinstance(content, str):
                text = content
            if text:
                client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=session_id,
                    messages=[(text[:5000], role.upper())],
                )
        except Exception as e:
            print(f"[LTTM:Memory] Save failed: {e}", flush=True)
            
    def _get_client(self):
        """
        Lazily initialize MemoryClient on first use to defer boto3 cold-start cost.
        
        Returns: A shared MemoryClient instance pinned to us-west-2 (same region as the AgentCore runtime). 
                 Reused on subsequent calls.
        """

        if self._client is None:
            self._client = MemoryClient(region_name="us-west-2")
            print("[LTTM:Memory] MemoryClient initialized (deferred)", flush=True)
        return self._client
