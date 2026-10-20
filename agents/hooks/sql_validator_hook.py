# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
sql_validator_hook.py — Deterministic SQL validation hook for Athena sub-agents.

Registered in each Athena sub-agent (cloudtrail, cloudwatch, config, cur,
flowlogs, guardduty) as hook SQLValidatorHook — fires BEFORE SQLRewriteHook so
invalid SQL is rejected before LIMIT enforcement even runs.

Implements the "hard hook" pattern: deterministic checks (no LLM, <1ms, $0), framework-level enforcement that
the LLM cannot bypass, leaving `run_athena_query` a pure execute-only tool
while validation lives here as a guardrail layer.

Rules (table names, partition keys, blocked keywords) come from
agent_vars.py — single source of truth.

For lttm_logs.cloudtrail_logs it also checks the region filter:
unless the question names a region, the query must search every region in
CLOUDTRAIL_REGIONS (SIGNIN_REGIONS for sign-ins).
"""

import re
from typing import Any
from urllib.parse import unquote
from strands.hooks import (
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

import utils.agent_vars as vars
import utils.sql_text as sql_text


def validate_sql(sql: str) -> list[str]:
    """Validate SQL against LTTM table rules.

    The checks read the SQL with its string literals and comments blanked out
    (utils/sql_text.py): a word inside a literal such as
    `LIKE 'Delete%'` is data, not SQL. In order: one SELECT (or WITH) statement,
    no `awsdatacatalog.` prefix, no blocked keyword (DROP, DELETE, UPDATE,
    INSERT, ALTER, TRUNCATE), fully qualified `lttm_logs.<table>` names, every
    partition key of every table the query reads filtered with `=`, `IN` or
    `BETWEEN` after WHERE, and no SELECT *. Rules come from
    `utils/agent_vars.py` (`TABLES`, `BLOCKED_SQL_KEYWORDS`).

    The first check is needed beside the keyword list: UNLOAD writes with the
    agent role's credentials, and is no blocked keyword. A partition key counts
    only when it is filtered: `year > '2000'` does not, nor does `region` inside
    `awsregion`; every table of a JOIN or UNION is checked; and a "group",
    "order" or "limit" inside a literal does not end the WHERE text. A CloudWatch
    query must not read LTTM's own runtime logs, which hold every user's
    questions, the generated SQL and the internal names; that check reads the
    unmasked SQL, because the log group values are in its literals.

    Args:
        sql: The SQL statement to validate. Typically extracted from
            `event.tool_use["input"]["sql"]` inside a `BeforeToolCallEvent`
            handler, but the function is usable standalone for unit tests.

    Returns:
        List of error message strings, one per failed check. An empty list
        means all checks passed and the SQL is safe to execute. Error
        messages are user-facing text formatted for the LLM to read and
        fix on retry.
    """
    errors = []
    masked = sql_text.mask(sql)
    sql_lower = masked.lower().strip()
    sql_upper = masked.upper().strip()

    if not re.match(r"\(*\s*(select|with)\b", sql_lower) or ";" in sql_lower.rstrip("; \t\n"):
        errors.append("Only one SELECT (or WITH ... SELECT) statement is allowed")

    if "awsdatacatalog." in sql_lower:
        errors.append("Remove 'awsdatacatalog.' prefix — use 'lttm_logs.<table>' directly")

    for kw in vars.BLOCKED_SQL_KEYWORDS:
        if re.search(rf'\b{kw}\b', sql_upper):
            errors.append(f"Blocked keyword '{kw}' — only SELECT queries are allowed")

    tables = [table for table in vars.TABLES if re.search(rf"\b{re.escape(table)}\b", sql_lower)]
    if not tables:
        for table_name in vars.TABLES:
            bare_name = table_name.split(".")[1] if "." in table_name else table_name
            if re.search(rf"\b{bare_name}\b", sql_lower) and "lttm_logs." not in sql_lower:
                errors.append(f"Use fully qualified table name: '{table_name}' (not just '{bare_name}')")
                tables = [table_name]
                break

    if not tables:
        return errors

    errors.extend(partition_filter_errors(sql_lower))

    if re.search(r'\bselect\s+\*\s+from\b', sql_lower):
        errors.append("Use explicit column names instead of SELECT *")

    if "lttm_logs.cloudwatch_logs" in tables:
        own = sorted(group for group in log_groups_named(sql) if is_agent_log_group(group))
        if own:
            errors.append(f"LTTM's own logs and traces are not available to questions: {', '.join(own)}. "
                          f"Tell the user so; do not say they do not exist")

    return errors


_SUBQUERY = re.compile(r"\(\s*(?:select|with)\b")
_TABLE_REF = re.compile(r"\b(lttm_logs\.[a-z_]+)\b(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?")
_SET_OPERATOR = re.compile(r"\b(?:union|except|intersect)\b")
_NOT_ALIAS = {"where", "join", "on", "inner", "left", "right", "full", "cross", "outer", "group", "order", "limit",
              "union", "except", "intersect", "having", "using", "natural", "tablesample", "select", "window", "with"}


def _subquery_spans(text: str) -> list[tuple[int, int]]:
    """Find every parenthesised SELECT or WITH in the masked SQL.

    Args:
        text: The SQL, masked (sql_text.mask) and in lower case.

    Returns:
        (start, end) of each one, end exclusive, in the order they start; an empty list when there is none.
    """
    spans = []
    for match in _SUBQUERY.finditer(text):
        depth = 0
        for i in range(match.start(), len(text)):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            if depth == 0:
                spans.append((match.start(), i + 1))
                break
    return spans


def partition_filter_errors(masked_lower: str) -> list[str]:
    """List the errors for the table references whose partition keys their own WHERE does not filter.

    Each table reference is checked against the WHERE of its own SELECT: a subquery or CTE body is its own
    scope, with the subqueries nested in it blanked out, and each part of a UNION is its own SELECT. Two
    tables in one SELECT (a JOIN) that share a key name need that key filtered with their own alias, as
    Athena itself requires, so one table's filter never counts for the other. A keyword after a table name
    is no alias. A WHERE with an OR at its top level is an error too (TOP_LEVEL_OR).

    Args:
        masked_lower: The SQL, masked (sql_text.mask) and in lower case.

    Returns:
        The error messages, each once, in the order found; an empty list when every reference is filtered.
    """
    scopes = [(0, len(masked_lower))] + _subquery_spans(masked_lower)
    errors = []
    for start, end in scopes:
        own = list(masked_lower[start:end])
        for a, b in scopes:
            if start < a and b <= end:
                own[a - start:b - start] = " " * (b - a)
        own = "".join(own)
        bounds = [0] + [m.start() for m in _SET_OPERATOR.finditer(own)] + [len(own)]
        for seg_start, seg_end in zip(bounds, bounds[1:]):
            segment = own[seg_start:seg_end]
            refs = []
            for m in _TABLE_REF.finditer(segment):
                if m.group(1) in vars.TABLES:
                    alias = m.group(2) if m.group(2) and m.group(2) not in _NOT_ALIAS else None
                    refs.append((m.group(1), alias, m.end(2) if alias else m.end(1)))
            for n, (table, alias, after) in enumerate(refs):
                keys = vars.TABLES[table]
                where = re.search(r"\bwhere\b", segment[after:])
                if not where:
                    errors.append(f"No WHERE clause — required partition keys: {', '.join(keys)}")
                    continue
                filters = segment[after + where.start():]
                missing = []
                for key in keys:
                    shared = any(key in vars.TABLES[other] for m, (other, _, _) in enumerate(refs) if m != n)
                    name = alias or table.split(".", 1)[1]
                    pattern = rf"\b{re.escape(name)}\.{key}\s*(=|in\b|between\b)" if shared else rf"\b{key}\s*(=|in\b|between\b)"
                    if not re.search(pattern, filters):
                        missing.append(f"{name}.{key}" if shared else key)
                if missing:
                    errors.append(f"Missing required partition key filters in WHERE (use =, IN or BETWEEN): {', '.join(missing)}")
                if _top_level_or(filters):
                    errors.append(TOP_LEVEL_OR)
    return list(dict.fromkeys(errors))


TOP_LEVEL_OR = ("An OR at the top level of the WHERE clause lets rows bypass the partition filters. Put the "
                "alternatives in parentheses: AND ((month = '09' AND day IN (...)) OR (month = '10' AND day IN (...)))")
_CLAUSE_END = re.compile(r"\b(?:group\s+by|having|order\s+by|limit|offset|fetch|window)\b")


def _top_level_or(filters: str) -> bool:
    """Tell whether the WHERE clause at the start of `filters` has an OR outside every parenthesis.

    A time range across months is one query, `AND ((month = '09' AND day IN (...)) OR (month = '10' AND day
    IN (...)))`. Without the outer parentheses AND binds first, and the second branch has no account, region
    or year filter: it reads every account, with no error. The partition check alone cannot see it, because
    every key is still named. The clause ends at GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET, FETCH or WINDOW
    at its own depth, or at the parenthesis that closes its subquery.

    Args:
        filters: The masked SQL from the WHERE on, with nested subqueries blanked out.

    Returns:
        True when the clause has a top-level OR, False otherwise.
    """
    depth = 0
    for i, char in enumerate(filters):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and char in "oglhfw" and (i == 0 or not (filters[i - 1].isalnum() or filters[i - 1] == "_")):
            if filters.startswith("or", i) and (i + 2 == len(filters) or not (filters[i + 2].isalnum() or filters[i + 2] == "_")):
                return True
            if _CLAUSE_END.match(filters, i):
                return False
    return False


def log_groups_named(sql: str) -> set[str]:
    """Find the log groups in the `log_group` filters of the SQL (= and IN), as written.

    Args:
        sql: The query's SQL, not masked: the log groups are in its literals.

    Returns:
        The log group names; an empty set when the SQL filters none.
    """
    groups = set(re.findall(r"\blog_group\s*=\s*'([^']+)'", sql, re.IGNORECASE))
    for values in re.findall(r"\blog_group\s+in\s*\(([^)]*)\)", sql, re.IGNORECASE):
        groups.update(value.strip() for value in re.findall(r"'([^']+)'", values))
    return groups


def is_agent_log_group(name: str) -> bool:
    """Tell whether a log group is LTTM's own: an AgentCore runtime's log or the runtime's telemetry.

    Names may be URL-encoded, and a leading "/" does not matter, so "/aws/spans" counts as "aws/spans".

    Args:
        name: The log group name, as a query or a request wrote it.

    Returns:
        True when it is under vars.AGENT_LOG_GROUP_PREFIX or one of vars.AGENT_LOG_GROUPS, False otherwise.
    """
    bare = unquote(name).lower().strip().lstrip("/")
    return (bare.startswith(vars.AGENT_LOG_GROUP_PREFIX.lstrip("/"))
            or bare in {group.lstrip("/") for group in vars.AGENT_LOG_GROUPS})


REGION_CODE = re.compile(r"\b[a-z]{2}-[a-z]+-\d\b")
SIGNIN_MARKERS = ("signin.amazonaws.com", "consolelogin", "checkmfa", "switchrole",
                  "userauthentication", "credentialchallenge", "credentialverification")


def named_regions(question: str) -> set[str]:
    """Return the regions a question names, as codes ('us-west-2' or 'Oregon' -> 'us-west-2').

    Args:
        question: The question the sub-agent is answering.

    Returns:
        The region codes; an empty set when it names none.
    """
    text = question.lower()
    regions = set(REGION_CODE.findall(text))
    for name, code in vars.REGION_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            regions.add(code)
    return regions


def queried_regions(sql: str) -> set[str]:
    """Return the regions in the `region` partition filters of the SQL.

    Only the text from the first WHERE on is read, so a CASE in the SELECT list does not count.
    The `awsregion` column never matches, because `\\bregion` needs a word boundary.

    Args:
        sql: The query's SQL.

    Returns:
        The region codes; an empty set when the SQL has no WHERE or no region filter.
    """
    sql_lower = sql.lower()
    where = re.search(r"\bwhere\b", sql_lower)
    if not where:
        return set()
    filters = sql_lower[where.start():]
    regions = set(re.findall(r"\bregion\s*=\s*'([^']+)'", filters))
    for values in re.findall(r"\bregion\s+in\s*\(([^)]*)\)", filters):
        regions.update(value.strip() for value in re.findall(r"'([^']+)'", values))
    return regions


def check_cloudtrail_regions(sql: str, question: str) -> list[str]:
    """Check that a CloudTrail query searches every region the question needs.

    If the question names regions, the query must search at least those. If it names none,
    the query must search all of CLOUDTRAIL_REGIONS, or SIGNIN_REGIONS for sign-in events.
    Other tables are not checked.

    Args:
        sql: The SQL the LLM wants to run.
        question: The question the sub-agent is answering (its newest user message).

    Returns:
        List of error message strings for the LLM; empty when the regions are fine.
    """
    sql_lower = sql.lower()
    if "lttm_logs.cloudtrail_logs" not in sql_lower:
        return []
    searched = queried_regions(sql)
    named = named_regions(question)
    if named:
        missing = sorted(named - searched)
        if missing:
            return [f"The question names {', '.join(missing)}, but the query does not search it — "
                    f"add it to the region filter"]
        return []
    expected = vars.SIGNIN_REGIONS if any(m in sql_lower for m in SIGNIN_MARKERS) else vars.CLOUDTRAIL_REGIONS
    missing = [region for region in expected if region not in searched]
    if missing:
        return [f"The question names no region, so the query must search every used region; missing: "
                f"{', '.join(missing)}. Use region IN ({vars.sql_list(expected)})"]
    return []


def current_question(agent: Any) -> str:
    """Return the text of the newest user message that is not a tool result.

    The newest user text is the question being answered now.

    Args:
        agent: The sub-agent (anything with a `messages` list).

    Returns:
        The texts of that message joined with spaces; "" when there is none.
    """
    for message in reversed(getattr(agent, "messages", None) or []):
        if message.get("role") != "user":
            continue
        texts = [block["text"] for block in message.get("content", [])
                 if isinstance(block, dict) and "text" in block]
        if texts:
            return " ".join(texts)
    return ""


class SQLValidatorHook(HookProvider):
    """Deterministic guardrail that blocks bad SQL before Athena ever sees it.

    On every `run_athena_query` call, extracts the SQL from `event.tool_use`
    and runs it through `validate_sql`. If any rule fails — forbidden prefix,
    blocked keyword, unknown table, missing partition key, or SELECT * — the
    tool call is cancelled with `event.cancel_tool = <error message>` and the
    LLM receives the error text as the tool result so it can fix the SQL and
    retry. If all rules pass, the call goes through untouched.

    The checks themselves live in the module-level `validate_sql` function so
    they can be unit-tested independently of Strands.

    The CloudTrail region check (`check_cloudtrail_regions`) blocks
    at most once per question: if the retry still fails it, the query runs
    anyway, so a question the check misreads can never loop.
    """

    def __init__(self) -> None:
        """Start with the region check armed.

        Args:
            None.

        Returns:
            None.
        """
        self._region_check_blocked = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the SQL validation callbacks for BeforeInvocationEvent and BeforeToolCallEvent.

        Args:
            registry: The sub-agent's HookRegistry.
            **kwargs: Not used; accepted so a future Strands version can pass more.

        Returns:
            None.
        """
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)

    def on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Re-arm the region check at the start of each question.

        Args:
            event: The BeforeInvocationEvent of the sub-agent's question. Not read.

        Returns:
            None.
        """
        self._region_check_blocked = False

    def on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Validate the SQL passed to run_athena_query; cancel the tool call with an error if invalid.

        Args:
            event: The BeforeToolCallEvent of the sub-agent's tool call. Calls of tools other than
                run_athena_query, and calls without SQL, are left alone.

        Returns:
            None. On errors, sets event.cancel_tool to a message the model reads as the tool result. Logs
            an [LTTM:SQLValidator] line either way.
        """
        if event.tool_use.get("name") != "run_athena_query":
            return

        sql = event.tool_use.get("input", {}).get("sql", "")
        if not sql:
            return

        errors = validate_sql(sql)

        region_errors = check_cloudtrail_regions(sql, current_question(event.agent))
        if region_errors and self._region_check_blocked:
            print(f"[LTTM:SQLValidator] Region check failed again, allowed after one retry: {region_errors[0]}", flush=True)
        elif region_errors:
            self._region_check_blocked = True
            errors += region_errors

        if errors:
            msg = f"SQL validation failed: {'; '.join(errors)}. Fix and retry."
            print(f"[LTTM:SQLValidator] BLOCKED — {msg}", flush=True)
            event.cancel_tool = msg
        else:
            print(f"[LTTM:SQLValidator] PASSED — {sql[:100]}...", flush=True)
