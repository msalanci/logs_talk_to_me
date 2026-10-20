# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
money.py — Money in CUR results, made by code: cents and totals.

The supervisor model, rounding the CUR rows' raw doubles and adding them up itself, was sometimes a cent off
("$250.16" for 250.1654291434005). As finding counts are, the CUR wrapper states every amount in cents and, when
adding the rows up is valid, their total, so the answer quotes them instead of computing.

Which result columns are money comes from the SQL, never from the values (usage amounts are doubles too), and
only known shapes count, so a share, a percentage, a count, a rank or a scaled amount is never shown in dollars:
- money whose rows add up: a cost column of `lttm_logs.cur_data` (COST_COLUMNS, as cur_agent.py's table schema
  names them) or an alias of one, a SUM of money, a CASE, IF or COALESCE whose values are money (or 0), money plus
  or minus money;
- money whose rows must not be added up: an average, one row's amount (MIN, MAX, LAG, …), a window function,
  money rounded or cast in SQL (a sum of rounded rows can differ from the total), an absolute amount. The reasons
  are the constants AVERAGE to REPEATED.

Rows are added up only for a single SELECT, the shape the CUR sub-agent writes. A subquery or a CTE can repeat a
grand total on every row or hide subtotal rows and a top N behind a wrapper, so such a result gets cents but no
total.
"""

import json
import re
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation, localcontext

from tools.athena_tool import format_athena_rows
from utils.sql_text import mask
from utils.untrusted_text import one_line

COST_COLUMNS = ("line_item_unblended_cost", "line_item_blended_cost")
_CENT = Decimal("0.01")
_ALIAS = r'"[^"]+"|[a-z_][a-z0-9_]*'
_REFERENCE = re.compile(r"(?:[a-z_][a-z0-9_]*\s*\.\s*)*([a-z_][a-z0-9_]*)")
_ZERO_OR_NULL = re.compile(r"0+(?:\.0*)?|null")
_KEYWORDS = {"end", "and", "or", "not", "null", "is", "in", "like", "between", "when", "then", "else", "case",
             "distinct", "asc", "desc", "true", "false", "as"}
_BEFORE_A_SIGN = _KEYWORDS - {"end", "null", "true", "false"}

AVERAGE = "it is an average, not a sum"
ONE_ROW = "it is one row's amount, not a sum"
WINDOW = "it comes from a window function, which repeats or accumulates amounts across rows"
ROUNDED = "each row is rounded in SQL, so the rows' sum can differ from the total"
ABSOLUTE = "it is an amount without its sign, not a sum"
NESTED = "the query reads a subquery or a CTE, and code adds up the rows of a single SELECT only"
REPEATED = "the query reads more than one table or can repeat rows (JOIN, UNNEST, VALUES, a sample)"


def exact_amount(value) -> Decimal | None:
    """An amount from its text exactly as Athena returned it.

    Args:
        value: A row's value: text, a number, or None.

    Returns:
        The amount as a Decimal; None when it is no finite number (NULL, "").
    """
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def usd(amount: Decimal) -> str:
    """An amount in dollars, rounded half-up to cents: "$1,234.57", "-$4.32".

    An amount that is not zero but rounds to $0.00 reads "< $0.01" (or "> -$0.01"), so a small cost is
    never shown as none. The precision grows with the amount: the default context stops at 28 digits.

    Args:
        amount: The exact amount.

    Returns:
        The amount as the answer may state it.
    """
    digits = Context(prec=max(28, amount.adjusted() + 5))
    rounded = amount.quantize(_CENT, rounding=ROUND_HALF_UP, context=digits)
    if rounded == 0 and amount != 0:
        return "< $0.01" if amount > 0 else "> -$0.01"
    return f"{'-' if rounded < 0 else ''}${abs(rounded):,.2f}"


def _cents(amount: Decimal) -> Decimal | None:
    """An amount rounded half-up to cents, at any size.

    Args:
        amount: The exact amount.

    Returns:
        The amount in cents; None for a number past Decimal's range ("1e1000000").
    """
    try:
        return amount.quantize(_CENT, rounding=ROUND_HALF_UP, context=Context(prec=max(28, amount.adjusted() + 5)))
    except (InvalidOperation, ValueError, OverflowError):
        return None


_STATED = re.compile(r"(?:(?<![\w$.,])(-))?\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\d,]*\.?\d)")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")
_FIELD = re.compile(r"^[ \t]+[^:\n]+:[ \t]*(.*)$", re.M)


def stated_amounts(text: str) -> list[tuple[str, Decimal]]:
    """The dollar amounts a text states, each as written and as a number: [("-$4.32", Decimal("-4.32")), …].

    An amount is written as an answer states it: "$242.78", "$1,234.57", "-$4.32", "$250". A minus counts only when
    it touches the "$": "- $242.78" is a markdown bullet or a dash between words.

    Args:
        text: An answer, a result or a question.

    Returns:
        The amounts, in order; [] when there are none.
    """
    found = []
    for m in _STATED.finditer(text):
        value = Decimal(m.group(2).replace(",", "") + (f".{m.group(3)}" if m.group(3) else ""))
        found.append((m.group(0).strip(), -value if m.group(1) else value))
    return found


def _backed_by_result(text: str) -> set[Decimal]:
    """The cents a cost result backs: its "$…" amounts and totals, its "(exact: …)" values, and the numbers in its
    rows' values. Not its layout ("Results: 3 rows", "Row 3:", "over the 3 rows"), so "about $3" is not backed.

    A row's value is read from its "  column: value" line, as format_athena_rows writes it.

    Args:
        text: One cost result, as the supervisor got it.

    Returns:
        The amounts in cents.
    """
    numbers = [value for _, value in stated_amounts(text)]
    numbers += [n for n in map(exact_amount, re.findall(r"\(exact: ([^)\s]+)\)", text)) if n is not None]
    numbers += [n for field in _FIELD.findall(text) for n in map(exact_amount, _NUMBER.findall(field)) if n is not None]
    return {c for c in map(_cents, numbers) if c is not None}


def _backed_by_question(text: str) -> set[Decimal]:
    """The cents a user's question backs: any amount or number in it ("over $100", "more than 100 dollars").

    Args:
        text: A user's question.

    Returns:
        The amounts in cents.
    """
    numbers = [value for _, value in stated_amounts(text)]
    numbers += [n for n in map(exact_amount, _NUMBER.findall(text)) if n is not None]
    return {c for c in map(_cents, numbers) if c is not None}


def unbacked_amounts(answer: str, results: list[str], questions: list[str] | str = ()) -> list[str]:
    """The dollar amounts an answer states that neither a cost result nor a user's question holds.

    A cost result backs every number of its rows' values and every amount it writes (`_backed_by_result`): the
    answer may state any of them in dollars and cents, including the correct cents of a raw value the wrapper did
    not take for money. A question backs the amounts and numbers in it. Zero is always backed (an account with no
    cost). Anything else, such as "$7.39" for dev and prod together, "$250" for $250.17 or a wrong rounding, is
    money the model computed itself.

    Args:
        answer: The supervisor's answer.
        results: The cost results of the question.
        questions: The user's question, or the questions of the conversation.

    Returns:
        The unbacked amounts as the answer writes them, each once, in order; [] when every amount is backed.
    """
    backed = {Decimal("0.00")}
    for text in results:
        backed |= _backed_by_result(text)
    for text in ([questions] if isinstance(questions, str) else questions):
        backed |= _backed_by_question(text)
    return list(dict.fromkeys(shown for shown, value in stated_amounts(answer) if _cents(value) not in backed))


def _exact_sum(amounts: list[Decimal]) -> Decimal:
    """The exact sum of the amounts (a default context rounds a sum to 28 digits).

    Args:
        amounts: The amounts to add up.

    Returns:
        Their sum; 0 for none.
    """
    if not amounts:
        return Decimal(0)
    top = max(a.adjusted() for a in amounts)
    bottom = min(a.as_tuple().exponent for a in amounts)
    with localcontext(Context(prec=max(28, top - bottom + len(str(len(amounts))) + 2))):
        return sum(amounts, Decimal(0))


def _depths(text: str) -> list[int]:
    """The parenthesis depth at each character; parentheses inside a double-quoted name do not count.

    Args:
        text: The masked SQL, or a part of it.

    Returns:
        One depth per character of `text`, the depth before it.
    """
    depths, depth, quoted = [], 0, False
    for ch in text:
        depths.append(depth)
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            depth += (ch == "(") - (ch == ")")
    return depths


def _in_quotes(text: str, index: int) -> bool:
    """Whether the character at `index` is inside a double-quoted name.

    Args:
        text: The masked SQL, or a part of it.
        index: A character's position.

    Returns:
        True inside a quoted name, False outside.
    """
    return text.count('"', 0, index) % 2 == 1


def _closing(text: str, start: int) -> int | None:
    """The index of the parenthesis that closes the one at `start`.

    Args:
        text: The masked SQL, or a part of it.
        start: The position of an opening parenthesis.

    Returns:
        The closing parenthesis' position; None when it is not closed.
    """
    depths = _depths(text)
    return next((i for i in range(start + 1, len(text)) if text[i] == ")" and depths[i] == depths[start] + 1
                 and not _in_quotes(text, i)), None)


def _split(text: str, separators: str) -> list[tuple[str, str]]:
    """Split at the separators outside parentheses and quoted names: [(separator before the part, part), …].

    A + or - splits only between two operands: a sign ("-x", "THEN -1", "1e-5") does not. After the words of
    _BEFORE_A_SIGN a + or - is a sign; END, NULL, TRUE and FALSE end an operand, so they are not among them.

    Args:
        text: An expression or a select list.
        separators: The characters to split at, such as "," or "+-".

    Returns:
        The parts, each with the separator before it ("" for the first).
    """
    parts, start, op = [], 0, ""
    depths = _depths(text)
    for i, ch in enumerate(text):
        if ch not in separators or depths[i] or _in_quotes(text, i):
            continue
        if ch in "+-":
            before = text[:i].rstrip()
            word = re.search(r"([a-z_][a-z0-9_]*)$", before)
            if (not re.search(r"[\w)\"]$", before) or (word and word.group(1) in _BEFORE_A_SIGN)
                    or re.search(r"(?<![\w.])\d+(?:\.\d*)?e$", before)):
                continue
        parts.append((op, text[start:i].strip()))
        op, start = ch, i + 1
    return parts + [(op, text[start:].strip())]


def _case_values(e: str) -> list[str] | None:
    """The THEN and ELSE values of a CASE expression that is the whole text.

    Args:
        e: An expression, without its outer parentheses.

    Returns:
        The values, in order; None when the text is not one CASE expression.
    """
    depths = _depths(e)
    tokens = [(m.group(1), m.start(), m.end()) for m in re.finditer(r"\b(case|when|then|else|end)\b", e)
              if depths[m.start()] == 0 and not _in_quotes(e, m.start())]
    if not tokens or tokens[0][:2] != ("case", 0):
        return None
    level, values, start = 0, [], None
    for word, at, end in tokens:
        if word == "case":
            level += 1
        elif word == "end":
            level -= 1
            if level == 0:
                if start is not None:
                    values.append(e[start:at])
                return values if not e[end:].strip() else None
        elif level == 1:
            if start is not None:
                values.append(e[start:at])
                start = None
            if word in ("then", "else"):
                start = end
    return None


def _all_money(values: list[str], sources: dict) -> tuple[bool, str | None]:
    """Money when every value is money or 0 (or NULL) and one at least is money.

    Args:
        values: The values of a CASE, IF, COALESCE, GREATEST or LEAST.
        sources: The names that hold money (see _money).

    Returns:
        (True, the first reason a value gives why its rows must not be added up, or None) when they are money;
        (False, None) otherwise.
    """
    results = [(v, _money(v, sources)) for v in values]
    if any(ok for _, (ok, _) in results) and all(ok or _ZERO_OR_NULL.fullmatch(v.strip())
                                                 for v, (ok, _) in results):
        return True, next((why for _, (ok, why) in results if ok and why), None)
    return False, None


def _function(name: str, args: str, rest: str, sources: dict) -> tuple[bool, str | None]:
    """Whether a function call's value is money, and why its rows must not be added up (None when they may).

    FILTER (WHERE …) picks rows; it does not change what is added up. IF(condition, value[, value]): the values
    decide, and a missing one is NULL. Any other function's first argument must be money: a count, a rank or a sum
    of hours is no money. GREATEST and LEAST need every value to be money (or 0): GREATEST(cost, 0) is money,
    GREATEST(cost, hours) not. LAG(value, offset, default) needs its default to be money too.

    Args:
        name: The function's name, in lower case.
        args: The text between its parentheses.
        rest: What follows the call: "", FILTER (…) or OVER (…).
        sources: The names that hold money (see _money).

    Returns:
        (is money, the reason its rows must not be added up or None).
    """
    window = False
    if rest:
        filtered = re.match(r"filter\s*\(", rest)
        if filtered:
            close = _closing(rest, filtered.end() - 1)
            if close is None:
                return False, None
            rest = rest[close + 1:].strip()
        window = bool(re.fullmatch(r"over\s*(\(.*\)|[a-z_][a-z0-9_]*)", rest, re.S))
        if rest and not window:
            return False, None
    if name in ("cast", "try_cast"):
        typed = re.fullmatch(r"(.*)\s+as\s+([a-z].*)", args.strip(), re.S)
        inner = _money(typed.group(1), sources) if typed else (False, None)
        if not inner[0] or window:
            return False, None
        return (True, inner[1]) if re.match(r"(double|real)\b", typed.group(2)) else (True, ROUNDED)
    parts = [part for _, part in _split(args, ",")]
    if name == "if":
        return _all_money(parts[1:], sources) if len(parts) >= 2 and not window else (False, None)
    if name == "coalesce":
        return (False, None) if window else _all_money(parts, sources)
    first = _money(parts[0], sources) if parts and parts[0] else (False, None)
    if not first[0]:
        return False, None
    if name == "sum":
        return True, WINDOW if window else first[1]
    if name in ("avg", "approx_percentile", "geometric_mean"):
        return True, AVERAGE
    if name in ("greatest", "least"):
        return (True, ONE_ROW) if _all_money(parts, sources)[0] else (False, None)
    if name in ("lag", "lead"):
        default = parts[2] if len(parts) > 2 else "null"
        return (True, ONE_ROW) if _money(default, sources)[0] or _ZERO_OR_NULL.fullmatch(default.strip()) \
            else (False, None)
    if name in ("min", "max", "arbitrary", "any_value", "first_value", "last_value", "nth_value"):
        return True, ONE_ROW
    if window:
        return False, None
    if name in ("round", "truncate", "floor", "ceil", "ceiling"):
        return True, ROUNDED
    if name == "abs":
        return True, ABSOLUTE
    if name in ("try", "nullif"):
        return True, first[1]
    return False, None


def _money(expression: str, sources: dict) -> tuple[bool, str | None]:
    """Whether an expression's value is money, and why its rows must not be added up (None when they may).

    `sources` maps the names that hold money (the cost columns, then the aliases of inner selects) to the
    reason their rows must not be added up, or None. Money plus or minus money is money. A product or a ratio
    never is: ×100 gives cents, ÷1000 thousands, money ÷ money a share.

    Args:
        expression: A select item's expression.
        sources: The names that hold money, as above.

    Returns:
        (is money, the reason its rows must not be added up or None).
    """
    e = re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", expression.strip())
    while e.startswith("(") and _closing(e, 0) == len(e) - 1:
        e = e[1:-1].strip()
    if not e:
        return False, None
    values = _case_values(e)
    if values is not None:
        return _all_money(values, sources)
    terms = _split(e, "+-")
    if len(terms) > 1:
        results = [_money(term, sources) for _, term in terms if term]
        if results and all(ok for ok, _ in results):
            return True, next((why for _, why in results if why), None)
        return False, None
    if e[0] in "+-":
        return _money(e[1:], sources)
    if len(_split(e, "*/")) > 1:
        return False, None
    call = re.match(r"([a-z_][a-z0-9_]*)\s*\(", e)
    if call:
        close = _closing(e, call.end() - 1)
        if close is not None:
            return _function(call.group(1), e[call.end():close], e[close + 1:].strip(), sources)
    reference = _REFERENCE.fullmatch(e)
    if reference and reference.group(1) in sources:
        return True, sources[reference.group(1)]
    return False, None


def _select_lists(masked: str) -> list[tuple[int, list[str]]]:
    """Every select list in the (masked, lower-case) SQL, in order of appearance: (depth, its items).

    A SELECT without a FROM at its depth runs to its closing parenthesis, or to the end.

    Args:
        masked: The masked SQL, in lower case.

    Returns:
        (depth, items) per SELECT; [] when there is none.
    """
    depths = _depths(masked)
    keywords = [(m.group(1), m.start(), m.end()) for m in re.finditer(r"\b(select|from)\b", masked)
                if not _in_quotes(masked, m.start())]
    lists = []
    for i, (word, start, end) in enumerate(keywords):
        if word != "select":
            continue
        level = depths[start]
        stop = next((s for w, s, _ in keywords[i + 1:] if w == "from" and depths[s] == level), None)
        if stop is None:
            stop = next((j for j in range(end, len(masked)) if depths[j] < level), len(masked))
        text = re.sub(r"^\s*(distinct|all)\b", "", masked[end:stop])
        lists.append((level, [item for _, item in _split(text, ",") if item]))
    return lists


def _item(item: str, position: int, outermost: bool) -> tuple[str | None, str]:
    """A select item's result column name and its expression.

    Athena names an expression without an alias "_col<position>" in the query's result. An inner select's
    unnamed expression cannot be read by name, so it gets no name (None). The words of _KEYWORDS end an
    expression rather than name it ("CASE … END", "x IS NOT NULL"), so none of them is an alias.

    Args:
        item: One select item.
        position: Its position in the select list, from 0.
        outermost: True for an item of the outermost select.

    Returns:
        (the column name, the expression).
    """
    match = re.fullmatch(rf"(?P<expr>.*?)(?:(?P<as>\s*\bas\s+)|\s+)(?P<alias>{_ALIAS})", item, re.S)
    if match:
        expr, alias = match.group("expr").strip(), match.group("alias").strip('"')
        last = re.split(r"\s+", expr)[-1] if expr else ""
        if expr and alias not in _KEYWORDS and (match.group("as") or (
                re.search(r"[\w)\"']$", expr) and last not in _BEFORE_A_SIGN)):
            return alias, expr
    reference = _REFERENCE.fullmatch(re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", item))
    if reference:
        return reference.group(1), item
    return (f"_col{position}" if outermost else None), item


def money_columns(sql: str) -> dict[str, str | None]:
    """The money columns a CUR query returns, by result column name, each with whether its rows add up.

    Only the outermost select's columns count; an inner select's aliases are what they read. The outermost select
    is the first one at the lowest depth: "(SELECT … LIMIT 5) LIMIT 20" has none at depth 0. The inner aliases are
    read four times over, as an alias can read another inner alias, which can read another one. A name that one
    inner select defines as money and another as no money (two CTEs, each with its "total") is unclear, and is not
    read as money. A name the outermost select gives twice holds only its last value in the result rows, and is
    left out. A `*` (or `t.*`) passes the inner columns (or the table's cost columns) through.

    Args:
        sql: The query the CUR sub-agent ran.

    Returns:
        {column name in lower case: None when its rows may be added up into a total, otherwise the reason
        they must not be}.
    """
    lists = _select_lists(mask(sql).lower())
    if not lists:
        return {}
    outermost = min(range(len(lists)), key=lambda i: (lists[i][0], i))
    inner = [_item(item, position, False) for i, (_, list_items) in enumerate(lists) if i != outermost
             for position, item in enumerate(list_items) if not item.endswith("*")]
    sources = dict.fromkeys(COST_COLUMNS)
    inner_money = {}
    for _ in range(4):
        for name, expression in inner:
            is_money, why = _money(expression, sources) if name else (False, None)
            if not is_money:
                continue
            inner_money[name] = (inner_money[name] or why) if name in inner_money else why
            if name not in COST_COLUMNS:
                sources[name] = inner_money[name]
    for name, expression in inner:
        if name and name not in COST_COLUMNS and not _money(expression, sources)[0]:
            sources.pop(name, None)
            inner_money.pop(name, None)
    names = [_item(item, position, True)[0] for position, item in enumerate(lists[outermost][1])
             if not item.endswith("*")]
    twice = {name for name in names if names.count(name) > 1}
    money = {}
    for position, item in enumerate(lists[outermost][1]):
        if item.endswith("*"):
            passed = inner_money if len(lists) > 1 else dict.fromkeys(COST_COLUMNS)
            money.update({name: why for name, why in passed.items() if name not in money})
            continue
        name, expression = _item(item, position, True)
        is_money, why = _money(expression, sources)
        if is_money and name not in twice:
            money[name] = (money[name] or why) if name in money else why
    return money


def _no_total(sql: str, rows: int, limit: int) -> str | None:
    """Why the rows' sum is not the total of the whole result.

    One SELECT can still count a cost in several rows: a cost by tag (UNNEST of resource_tags), a self-join of two
    months, a comma join, VALUES rows.

    Args:
        sql: The query.
        rows: How many rows it returned.
        limit: The most rows the SQL rewrite hook lets a CUR query return.

    Returns:
        The reason, as the "No total" line gives it; None when the rows' sum is the total.
    """
    masked = mask(sql).lower()
    if len([m for m in re.finditer(r"\bselect\b", masked) if not _in_quotes(masked, m.start())]) > 1:
        return NESTED
    own = "".join(ch if depth == 0 else " " for ch, depth in zip(masked, _depths(masked)))
    source = re.search(r"\bfrom\b(.*?)(?=\b(?:where|group|order|limit|having|offset|fetch|window)\b|$)", own, re.S)
    if re.search(r"\b(join|unnest|tablesample|lateral|union|intersect|except|values)\b", masked) or (
            source and "," in source.group(1)):
        return REPEATED
    if re.search(r"\b(rollup|cube|grouping\s+sets)\b", masked):
        return "the query adds subtotal rows (ROLLUP, CUBE or GROUPING SETS)"
    if re.search(r"\bselect\s+distinct\b", masked):
        return "the query removes duplicate rows (DISTINCT)"
    if re.search(r"\boffset\b", masked):
        return "the query skips rows (OFFSET)"
    caps = [int(a or b) for a, b in re.findall(r"\blimit\s+(\d+)|\bfetch\s+(?:first|next)\s+(\d+)", masked)]
    cap = min(caps + [limit])
    if rows >= cap:
        return f"the rows may be cut off at LIMIT {cap}, so their sum may not be the total"
    return None


def format_cost_rows(text: str, sql: str, limit: int) -> str:
    """One CUR tool result as the supervisor gets it: the rows formatted by Python, with money in cents.

    Every amount of a money column reads "$242.78 (exact: 242.7750241034005)". With two or more rows, each
    money column also gets one line after the row count: "Total (computed by code) of <column> over the N
    rows: $250.17" when adding the rows up is valid, otherwise "No total (computed by code) of <column>:
    <why>." A result that is not a list of rows is returned as it is; one without a money column is formatted
    by `format_athena_rows` alone.

    Args:
        text: The tool result's text: the JSON row list that `run_athena_query` returned.
        sql: The query that produced it.
        limit: The most rows the SQL rewrite hook lets a CUR query return (its `default_limit`).

    Returns:
        The formatted result. Prints one [LTTM:Totals] line per total line.
    """
    try:
        rows = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return text
    keys = {key.lower(): key for key in (rows[0] if rows else {})}
    columns = {keys[name]: why for name, why in money_columns(sql).items() if name in keys}
    if not rows or not columns:
        return format_athena_rows(rows)
    shown = []
    for row in rows:
        amounts = {col: exact_amount(row.get(col)) for col in columns}
        shown.append({key: f"{usd(amounts[key])} (exact: {value})" if amounts.get(key) is not None else value
                      for key, value in row.items()})
    lines = []
    if len(rows) >= 2:
        for col, why in columns.items():
            amounts = [exact_amount(row.get(col)) for row in rows]
            why = why or _no_total(sql, len(rows), limit) or ("a row has no amount" if None in amounts else None)
            line = (f"No total (computed by code) of {one_line(col)}: {why}." if why else
                    f"Total (computed by code) of {one_line(col)} over the {len(rows)} rows: {usd(_exact_sum(amounts))}")
            print(f"[LTTM:Totals] query_cur: {line}", flush=True)
            lines.append(line)
    head, _, rest = format_athena_rows(shown).partition("\n")
    return "\n".join([head, *lines]) + (f"\n{rest}" if rest else "")
