# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
answer_check.py — Claims in the supervisor's answer that a line written by code rules out.

The supervisor explains only from lines written by code, and its prompt says so, but its answers still broke that:
- Control Tower's line said the drift of two controls is not checked (NOT_CHECKING), so it is not known, and the
  answer opened with "No drift has been detected";
- an empty CloudWatch result came with its lines, and the answer added "This means the Lambda function was not
  invoked", which the prompt forbids.
So code reads the answer, and the supervisor gets one precise correction through the steering handler's existing
path, as the money check does: a Guide, at most one per question. Only phrasings are caught, not meaning: a claim
worded otherwise passes.
"""

import re

NOTE = "Note (written by code):"

DRIFT_NOTE = re.compile(r"^Note \(written by code\): Drift (?:NOT_CHECKING|UNKNOWN) \(.*$", re.M)
NO_DRIFT = [re.compile(pattern, re.I) for pattern in (
    r"\bno\s+(?:\w+\s+){0,2}drift\s+(?:was\s+|is\s+|has\s+been\s+)?(?:detected|found|present)\b",
    r"\bnot\s+drifted\b",
    r"\bno\s+indication\s+of\s+(?:\w+\s+){0,2}drift\b",
    r"\b(?:neither|none)\b[^.\n]{0,60}?\b(?:is|are|has|have|was|were)\s+(?:been\s+|reported\s+as\s+|shown\s+as\s+)?drifted\b",
    r"\b(?:there\s+is|there's|shows?|has|have|with)\s+no\s+(?:\w+\s+)?drift\b(?!\s+(?:detection|check|information|status|data))",
)]
CHECKED_DRIFT = re.compile(r"\b(?:landing\s+zone|baselines?)\b", re.I)
CONTROLS = re.compile(r"\b(?:controls?|guardrails?)\b(?!\s+tower)", re.I)

EMPTY = "no results found."
OWN_CAUSES = [re.compile(pattern, re.I) for pattern in (
    r"\b(?:could|may|might|would|can)\s+(?:also\s+)?(?:mean|indicate|suggest|imply|explain|reflect)\b",
    r"\b(?:could|may|might)\s+(?:also\s+)?(?:be|have\s+been)\s+(?:because|due|caused)\b",
    r"\b(?:could|may|might)\s+not\s+(?:exist|have\s+been|be\s+(?:enabled|configured|running))\b",
    r"\b(?:likely|probably|possibly|perhaps|presumably)\b",
    r"\bit(?:'s|\s+is)\s+possible\s+that\b",
    r"\b(?:was|were)(?:n't|\s+not)\s+(?:invoked|triggered|called|running|active|used|enabled|configured|deployed)\b",
    r"\b(?:did|does)(?:n't|\s+not)\s+(?:run|execute|produce|write|emit|generate|occur|happen|receive)\b",
    r"\b(?:does|do|did)(?:n't|\s+not)\s+exist\b(?!\s+in\s+the\s+(?:data\s+)?lake)",
    r"\bno\s+longer\s+exists?\b",
    r"\bno\s+(?:resources?|changes?|activity|traffic|invocations?)\s+(?:exist(?:s|ed)?|occurred|happened|took\s+place|were\s+made)\b",
)]


EVENT_WORDS = re.compile(r"\b(?:reset\w*|lifecycle|ResetEnabledControl|EnableGuardrail|UpdateLandingZone)\b", re.I)
EVENT_CAUSES = re.compile(r"\b(?:because|caused|causes|cause\s+of|triggered\s+by|side[- ]effects?|as\s+a\s+result|"
                          r"result\s+of|as\s+part\s+of|led\s+to|leads?\s+to|root\s+cause|in\s+response\s+to|due\s+to|"
                          r"downstream|consequences?|(?:which|that|this)\s+(?:then\s+)?(?:triggered|caused|set\s+off))\b",
                          re.I)
TRIGGER_QUESTION = re.compile(r"\b(?:did|was|were|is|has|have)\b[^?]{0,80}?\b(?:trigger\w*|caus\w*|start\w*|initiat\w*|"
                              r"set\s+off|responsible)\b", re.I)
YES = re.compile(r"^\W*(?:yes|indirectly|in\s+a\s+way)\b", re.I)
NOT_RECORDED = re.compile(r"\b(?:not|never|no)\b[^.]{0,40}?\b(?:record\w*|show\w*|known|say\w*|tell\w*)\b|\bunknown\b|"
                          r"\bnot\s+among\b", re.I)


def _why_line(results: list[str]) -> str:
    """Find the events tool's line that CloudTrail does not record why, when a result holds it.

    WHY is imported here, not at the top: tools import utils, and a top-level import would be circular.

    Args:
        results: This question's tool results.

    Returns:
        The line, as the events tool writes it; "" when no result holds it.
    """
    from tools.controltower_events_tool import WHY
    line = f"{NOTE} {WHY}"
    return line if any(line in text for text in results) else ""


def _units(answer: str, keep_code: bool = False) -> list[str]:
    """The answer's own sentences and lines: without quoted data (code, tables, quotes) and emphasis.

    With keep_code, inline code keeps its text, without the backticks: an answer can write every event name in
    backticks, and without them no sentence would name an event. _prose is imported here, not at the top: hooks
    import utils, and a top-level import would be circular.

    Args:
        answer: The supervisor's answer.
        keep_code: True to keep the text of inline code.

    Returns:
        The sentences and lines, each with its whitespace made single spaces; [] for an empty answer.
    """
    from hooks.output_integrity_hook import _prose
    text = _prose(re.sub(r"`([^`\n]*)`", r"\1", answer) if keep_code else answer).replace("*", "")
    return [" ".join(unit.split()) for unit in re.split(r"(?<=[.!?])\s+|\n+", text) if unit.strip()]


def _cut(text: str, limit: int = 240) -> str:
    """Shorten a sentence for a correction.

    Args:
        text: The sentence.
        limit: The most characters kept.

    Returns:
        The text as it is when it fits; otherwise cut, ending with "…".
    """
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _empty(result: str) -> bool:
    """An Athena result without rows: it starts "No results found." and has no "Results:" line.

    format_athena_rows starts a result without rows so, and so do the CloudWatch wrapper's lines without a query; a
    result with rows has a "Results:" line. The API tools (GuardDuty, Health, Macie, Inspector, …) word their empty
    results otherwise and are not checked.

    Args:
        result: One tool result's text.

    Returns:
        True for an empty Athena result, False otherwise.
    """
    text = result.strip()
    return text.lower().startswith(EMPTY) and not re.search(r"^\s*Results:", text, re.M)


def ruled_out(results: list[str], answer: str, question: str = "") -> tuple[str, str, str] | None:
    """Find the first claim of the answer that a line written by code rules out.

    Three kinds, checked in this order:
    - "drift": when a result holds Control Tower's drift line (DRIFT_NOTE, written by
      controltower_tool._drift_note and quoted in the correction), a sentence that claims there is no drift
      (NO_DRIFT). A sentence about the landing zone or a baseline alone is left out: their drift is checked
      (IN_SYNC or DRIFTED), so "no drift" can be true there; "Control Tower" is not a control.
    - "event cause": when a result holds the events tool's WHY line, an opener that answers yes to a question
      whether someone or something triggered or caused the events (TRIGGER_QUESTION, YES), whatever else it names;
      the opener is the first unit that is not a heading. Or a sentence about one of Control Tower's resets or
      lifecycle events (EVENT_WORDS) that gives a cause (EVENT_CAUSES) and does not say the cause is not recorded
      (NOT_RECORDED). "triggered" alone is no cause ("Control Tower (lifecycle events) triggered 7 automatically"
      is true); "triggered by" is. "triggered by Control Tower itself" is caught too, though it can be true: a true
      one costs one extra correction, a missed story a wrong answer. With AWS Organizations' account moves in the
      results, "…, which triggered the resets" is the story to expect; a sequence ("16 seconds after …") is no
      cause. The correction says that a lifecycle event names no user: a correction that only forbade causes of
      the answer's own kept a yes to whether a person triggered the resets.
    - "cause": when every result is an empty Athena result (_empty), a sentence that explains it in words of its
      own (OWN_CAUSES: words of inference, and the causes seen in answers). A phrase the results themselves hold is
      not the answer's own. "This means …" and "not enabled" are not caught on their own: correct answers use them
      to restate a line written by code, and the guesses after them are caught by the other phrases. The
      correction asks for the reason only in the lines' words, not to remove the sentence: a sentence can also
      hold true facts from a line.

    Args:
        results: This question's tool results.
        answer: The supervisor's answer.
        question: The user's question; "" when not known.

    Returns:
        (kind, the sentence, the correction for the supervisor); None when no claim is ruled out.
    """
    notes = [match.group(0) for text in results for match in DRIFT_NOTE.finditer(text)]
    if notes:
        for unit in _units(answer):
            if CHECKED_DRIFT.search(unit) and not CONTROLS.search(unit):
                continue
            if any(pattern.search(unit) for pattern in NO_DRIFT):
                return ("drift", unit,
                        f"Your answer says \"{_cut(unit)}\", but the line written by code says: "
                        f"\"{notes[0][len(NOTE):].strip()}\" Say that the drift of these controls is not known, as "
                        f"that line says, and never that there is no drift. Keep every row and every other statement "
                        f"unchanged.")
    why = _why_line(results)
    if why:
        units = _units(answer, keep_code=True)
        opener = next((unit for unit in units if not unit.startswith("#")), "")
        if TRIGGER_QUESTION.search(question or "") and YES.search(opener):
            unit = opener
            return ("event cause", unit,
                    f"The question asks whether someone or something triggered or caused Control Tower's events, and "
                    f"your answer opens with yes: \"{_cut(unit)}\". The line written by code says: "
                    f"\"{why[len(NOTE):].strip()}\" A lifecycle event names no user: it is Control Tower's own, and the "
                    f"events do not record what set it off. Answer from the events: who started each API call, which "
                    f"events are Control Tower's own, and what happened just before them, with times; say that the "
                    f"events do not record why. Keep every row and every other statement unchanged.")
        for unit in units:
            if EVENT_WORDS.search(unit) and EVENT_CAUSES.search(unit) and not NOT_RECORDED.search(unit):
                return ("event cause", unit,
                        f"Your answer gives a cause for Control Tower's events that no event holds: \"{_cut(unit)}\". "
                        f"The line written by code says: \"{why[len(NOTE):].strip()}\" Give the events, their times "
                        f"and who started each API call. A lifecycle event names no user: it is Control Tower's own, "
                        f"and the events do not record what set it off, so never say that a person or another event "
                        f"triggered or caused it. Say that the events do not record why. Keep every row and every "
                        f"other statement unchanged.")
    if results and all(_empty(text) for text in results):
        seen = " ".join(" ".join(results).lower().split())
        for unit in _units(answer):
            for pattern in OWN_CAUSES:
                match = pattern.search(unit)
                if match and " ".join(match.group(0).lower().split()) not in seen:
                    return ("cause", unit,
                            f"The results had no data, and your answer explains it in words of its own: "
                            f"\"{_cut(unit)}\". Give the reason only as the \"{NOTE}\" lines give it, in their words; "
                            f"when there is no such line, say only that nothing matched the question. Keep everything "
                            f"else unchanged.")
    return None
