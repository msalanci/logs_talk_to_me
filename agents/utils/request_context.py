# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
request_context.py — The state of one question, kept per request instead of per process.

invoke() makes a RequestContext for each question and starts it in the thread that runs the agent.
Strands copies Python's context into its event-loop thread (strands/_async.py) and into the thread
of each tool (asyncio.to_thread), so every hook, tool and sub-agent working on that question reads
the same context with current(). Two questions running at the same time (two sessions in one
process) each see their own. Outside a request, current() returns a context whose events go nowhere.
"""

import contextvars
import itertools
import queue
import threading
from dataclasses import dataclass, field

TOKEN_KEYS = ("inputTokens", "outputTokens", "totalTokens", "cacheReadInputTokens", "cacheWriteInputTokens")


@dataclass
class Receipt:
    """What one question used: the tokens of every LLM call and the data Athena scanned.

    The supervisor, each sub-agent and both judges add their own call; parallel sub-agents add to
    the same receipt, so a lock keeps each addition whole. Not counted: the managed guardrail and
    AgentCore Memory (billed per text unit and per event, not per token), and the summary Strands
    writes when the supervisor's history overflows its context window (that model call bypasses
    the agent's metrics; rare).

    `tokens` maps each caller to {"calls", and each of TOKEN_KEYS}. With prompt caching, Bedrock reports the
    cached part of the input apart from inputTokens, so TOKEN_KEYS holds the cache reads and writes too. `cache`
    maps each caller to {"writes": [...], "reads": [...]}, the tokens of each cache write and read.
    """

    tokens: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    athena_queries: int = 0
    athena_bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_tokens(self, caller: str, usage: dict) -> None:
        """Add one LLM call's tokens to a caller's count.

        Args:
            caller: Who made the call, such as "supervisor" or a sub-agent's name.
            usage: The call's usage, as Strands reports it; missing keys count as 0.

        Returns:
            None.
        """
        with self._lock:
            entry = self.tokens.setdefault(caller, {"calls": 0, **dict.fromkeys(TOKEN_KEYS, 0)})
            entry["calls"] += 1
            for key in TOKEN_KEYS:
                entry[key] += int(usage.get(key) or 0)

    def add_cache(self, caller: str, writes: list, reads: list) -> None:
        """Add one agent invocation's cache writes and reads, call by call (tokens each).

        Args:
            caller: Who made the calls.
            writes: The tokens written to the cache, per call.
            reads: The tokens read from the cache, per call.

        Returns:
            None. Nothing is added when both lists are empty.
        """
        if not writes and not reads:
            return
        with self._lock:
            entry = self.cache.setdefault(caller, {"writes": [], "reads": []})
            entry["writes"] += [int(w) for w in writes]
            entry["reads"] += [int(r) for r in reads]

    def cache_lines(self, minutes: int, names: dict) -> list:
        """The lines the user sees under the 💰 line, the supervisor first.

        One line per cache write ("saved to cache for the next N minutes") and one per caller for its reads. A read
        costs 0.1 x the input price, whatever the lifetime: 90% cheaper than reading the same tokens uncached.

        Args:
            minutes: How long a cache write lasts.
            names: The callers' names as the user sees them; a caller without one is "<caller> agent".

        Returns:
            The lines; [] when nothing was cached.
        """
        with self._lock:
            cache = {caller: {kind: list(sizes) for kind, sizes in entry.items()} for caller, entry in self.cache.items()}
        lines = []
        for caller in sorted(cache, key=lambda name: name != "supervisor"):
            name = names.get(caller, f"{caller.replace('_', ' ')} agent")
            for size in cache[caller]["writes"]:
                lines.append(f"📝 {name} instructions saved to cache for the next {minutes} minutes ({size:,} tokens)")
            reads = cache[caller]["reads"]
            if reads:
                times = {1: "", 2: " twice"}.get(len(reads), f" {len(reads)} times")
                size = (f"{reads[0]:,} tokens{' each' if len(reads) > 1 else ''}" if len(set(reads)) == 1
                        else f"{sum(reads):,} tokens in all")
                lines.append(f"♻️ {name} instructions read from cache{times} ({size}, 90% cheaper)")
        return lines

    def add_athena(self, bytes_scanned: int) -> None:
        """Add one Athena query and the data it scanned.

        Args:
            bytes_scanned: The bytes the query scanned; None counts as 0.

        Returns:
            None.
        """
        with self._lock:
            self.athena_queries += 1
            self.athena_bytes += int(bytes_scanned or 0)

    def as_dict(self) -> dict:
        """The receipt for the [LTTM:Receipt] log line.

        Args:
            None.

        Returns:
            {"total_tokens", "tokens" per caller, "athena_queries", "athena_bytes_scanned"}.
        """
        with self._lock:
            return {"total_tokens": sum(entry["totalTokens"] for entry in self.tokens.values()),
                    "tokens": {caller: dict(entry) for caller, entry in self.tokens.items()},
                    "athena_queries": self.athena_queries, "athena_bytes_scanned": self.athena_bytes}

    def line(self) -> str:
        """The 💰 line the user sees: the question's tokens per caller, the supervisor first, then what Athena scanned.

        A caller's cache reads and writes are shown when there are any.

        Args:
            None.

        Returns:
            "This question: N tokens — …", with an Athena part when a query ran.
        """
        receipt = self.as_dict()
        callers = sorted(receipt["tokens"].items(), key=lambda item: item[0] != "supervisor")
        parts = [f"{caller.replace('_', ' ')}{f' ×{entry['calls']}' if entry['calls'] > 1 else ''} "
                 f"{entry['totalTokens']:,} (in {entry['inputTokens']:,}, out {entry['outputTokens']:,}"
                 f"{f', cache read {entry.get('cacheReadInputTokens', 0):,}' if entry.get('cacheReadInputTokens') else ''}"
                 f"{f', cache write {entry.get('cacheWriteInputTokens', 0):,}' if entry.get('cacheWriteInputTokens') else ''})"
                 for caller, entry in callers]
        line = f"This question: {receipt['total_tokens']:,} tokens — {'; '.join(parts) or 'no LLM call'}."
        if receipt["athena_queries"]:
            queries = receipt["athena_queries"]
            line += f" Athena: {queries} quer{'y' if queries == 1 else 'ies'}, {receipt['athena_bytes_scanned'] / 1e6:,.1f} MB scanned."
        return line


@dataclass
class RequestContext:
    """One question: its session, its words, its --clean flag, its stream of SSE events and its receipt.

    `session_id` is "" outside a question, not "default": AgentCore Memory refuses an empty ID loudly, while
    "default" is the namespace of the long-term facts. `question` is the user's question, word for word: every
    sub-agent gets it. With `clean` (--clean), memory adds nothing to this question; with `test_run` (a test
    question, LTTM_TEST_RUN), memory keeps nothing of it. `events` are the question's SSE events, None when not
    streaming; `steps` gives the SSE step numbers, thread-safe.
    """

    session_id: str = ""
    question: str = ""
    clean: bool = False
    test_run: bool = False
    events: queue.Queue | None = None
    steps: itertools.count = field(default_factory=lambda: itertools.count(1))
    receipt: Receipt = field(default_factory=Receipt)


_current: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("lttm_request", default=None)
_OUTSIDE = RequestContext()


def current() -> RequestContext:
    """The context of the question being answered in this thread; an empty one outside a request.

    Args:
        None.

    Returns:
        The current RequestContext; outside a request, a shared one whose events go nowhere.
    """
    return _current.get() or _OUTSIDE


def start(context: RequestContext) -> None:
    """Make `context` the current one in this thread and in everything Strands starts from it.

    Args:
        context: The question's RequestContext.

    Returns:
        None.
    """
    _current.set(context)
