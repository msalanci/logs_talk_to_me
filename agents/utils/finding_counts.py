# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
finding_counts.py — Count line for the API tools that return lists of findings.

Used by guardduty_tool.py, macie_tool.py, inspector_tool.py and
access_analyzer_tool.py as the first line of every findings list.

The supervisor model, counting long finding lists itself, sometimes got the
totals wrong ("22" for 25). So the tools state the count, computed by Python,
and the answer can quote it instead of counting. The Athena path already
starts with "Results: N rows returned."

The model also added up the per-region and per-account lists wrongly ("24" for
23), so `region_blocks` puts one total over a call's regions, and
`agent_wrapper.accounts_total` one over a question's accounts, both added up
here by `combined_count_line`.
"""

import re
from collections import Counter

SEVERITY_ORDER = ["critical", "high", "medium", "low"]
_COUNT_LINE = re.compile(r"Findings: (\d+)(?: shown of (\d+) in total)?(?: \(([^)]*)\))?")


def count_line(groups: list[str], order: list[str] = (), total: int | None = None) -> str:
    """Return a line such as "Findings: 18 (critical 1, high 7, medium 4, low 6)".

    Args:
        groups: One group name per finding shown — its severity, or its account label.
        order: Group names in the order to list them; groups not in it follow in
            first-seen order. Groups with no finding are left out.
        total: How many findings exist, when only some are shown; adds "shown of N in total".

    Returns:
        The count line, without a trailing newline.
    """
    counts = Counter(groups)
    names = [g for g in order if g in counts] + [g for g in dict.fromkeys(groups) if g not in order]
    breakdown = ", ".join(f"{str(name).lower()} {counts[name]}" for name in names)
    shown = len(groups)
    head = f"Findings: {shown} shown of {total} in total" if total and total > shown else f"Findings: {shown}"
    return f"{head} ({breakdown})" if breakdown else head


def joined(names: list[str]) -> str:
    """Join names in prose: ["a", "b", "c"] -> "a, b and c".

    Args:
        names: The names, in order.

    Returns:
        The joined names; "" for none.
    """
    return " and ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else "".join(names)


def combined_count_line(texts: list[str], label: str, empty: tuple[str, ...] = ()) -> str:
    """Add up several findings lists into one count line, e.g. all regions of an account, or all accounts.

    Each text counts by its first line only: the count line the tool put first, or a total line
    this module put first (so a list that carries its own total is never counted twice). A text
    that starts with one of `empty` ("No GuardDuty findings found", …) counts as 0. Any other
    text is an error, and then there is no total: a sum that silently leaves a region or account
    out would be wrong.

    Args:
        texts: The findings lists, as the tools returned them.
        label: What the sum covers, put in front, e.g. "Account 111111111111 (main), regions … together".
        empty: Beginnings of the tool's messages that mean "no findings here".

    Returns:
        "<label>: Findings: N (critical a, high b, …)", with "shown of M in total" when a list was
        cut off; "" when a text can't be counted.
    """
    shown = total = 0
    groups = Counter()
    for text in texts:
        match = _COUNT_LINE.search((text or "").split("\n", 1)[0])
        if not match:
            if empty and (text or "").startswith(tuple(empty)):
                continue
            return ""
        shown += int(match.group(1))
        total += int(match.group(2) or match.group(1))
        for part in (match.group(3) or "").split(","):
            name, _, n = part.strip().rpartition(" ")
            if name and n.isdigit():
                groups[name] += int(n)
    names = [g for g in SEVERITY_ORDER if g in groups] + [g for g in groups if g not in SEVERITY_ORDER]
    head = f"Findings: {shown} shown of {total} in total" if total > shown else f"Findings: {shown}"
    breakdown = ", ".join(f"{name} {groups[name]}" for name in names)
    return f"{label}: {head}" + (f" ({breakdown})" if breakdown else "")


def region_blocks(regions: list[str], results: list[str], label: str = "", empty: tuple[str, ...] = ()) -> str:
    """Join one tool call's per-region results into labelled blocks, under one total for all of them.

    Args:
        regions: The regions queried, in order.
        results: One findings list (or message) per region.
        label: Whose findings, e.g. "Account 111111111111 (main)"; empty when the call covers several accounts.
        empty: Beginnings of the tool's "no findings here" messages (see `combined_count_line`).

    Returns:
        The single result as is for one region; otherwise "=== Region r ===" blocks, after a line
        such as "Account 111111111111 (main), regions eu-central-1 and us-west-2 together: Findings: 18 (…)"
        when every region could be counted.
    """
    if len(results) == 1:
        return results[0]
    blocks = "\n\n".join(f"=== Region {r} ===\n{text}" for r, text in zip(regions, results))
    scope = f"regions {joined(regions)} together"
    total = combined_count_line(results, f"{label}, {scope}" if label else scope[0].upper() + scope[1:], empty)
    return f"{total}\n\n{blocks}" if total else blocks
