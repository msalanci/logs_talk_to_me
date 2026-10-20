# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sql_text.py — SQL text for the SQL guards, with string literals and comments blanked out.

The guards search SQL with regular expressions. Searched raw, a word inside a string literal counts
too: `eventname LIKE 'Delete%'` would look like a DELETE statement, and `LIKE '%security group%'` would
end the WHERE clause at "group". mask() keeps every character's position, so a match found in the masked
text sits at the same place in the original SQL.
"""

import re

OUTER_LIMIT = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)


def mask(sql: str) -> str:
    """The SQL with the inside of every string literal, and every comment, replaced by spaces.

    The quotes of a literal stay, so `region = 'eu-central-1'` reads `region = '            '`.
    The result has the same length as the input. Inside a literal, '' is a quote, not its end.

    Args:
        sql: The SQL text.

    Returns:
        The masked SQL.
    """
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        if sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                elif sql[j] == "'":
                    break
                else:
                    j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                out[k] = " "
            i = end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                out[k] = " "
            i = end
        else:
            i += 1
    return "".join(out)


def outer_limit(sql: str) -> re.Match | None:
    """The statement's own LIMIT (the last one outside parentheses); not a subquery's or a CTE's.

    Args:
        sql: The SQL text.

    Returns:
        The LIMIT's match in the masked SQL, its number in group 1; None when the statement has no LIMIT of its own.
    """
    masked = mask(sql)
    found = None
    for match in OUTER_LIMIT.finditer(masked):
        before = masked[:match.start()]
        if before.count("(") == before.count(")"):
            found = match
    return found
