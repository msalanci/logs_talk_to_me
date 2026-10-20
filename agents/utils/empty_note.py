# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
empty_note.py — Lines written by code that say what is true when an answer comes back empty.

The supervisor is the only one who explains, and it sees only what code writes, never a sub-agent's own text.
Given "No results found." alone it guessed a cause ("may not be enabled", "Config was not tracking those
resources"), and a region list in its own prompt made it answer without asking. So each service's wrapper adds one
line to an empty result, written here from the agent_vars constants: what the data source covers, and that these
queries found nothing. The lines follow four rules:
- the line appears only after a real query, in the result, never in the supervisor's prompt;
- it reads one way only (verbs, never "<Service> records in …", which was read as the noun);
- it says what an empty result means, so there is no gap left to explain;
- it names per account where accounts differ (agent_vars *_REGIONS_BY_ACCOUNT).
Config's line lives in config_agent.region_note (its history can hold regions without a recorder).
CloudWatch's table has no region column: a region it does not collect is read from the question itself
(cloudwatch_region_note), and the sub-agent's own text is not passed on for it.
"""

import json
import re

import utils.agent_vars as vars
from utils.agent_wrapper import _tool_calls
from utils.sql_text import mask

NOTE = "Note (written by code):"
RUNTIME_LOGS = "LTTM's own AgentCore runtime logs and traces are not collected."


def athena_calls(agent) -> list[dict]:
    """List every run_athena_query call of the agent's newest question, in order.

    Args:
        agent: The sub-agent, after it answered.

    Returns:
        One {sql, status, text} per call; [] when there were none.
    """
    return [{"sql": str(c["input"].get("sql", "")), "status": c["status"], "text": c["text"]}
            for c in _tool_calls(agent) if c["name"] == "run_athena_query"]


def all_empty(calls: list[dict]) -> bool:
    """Tell whether every query succeeded and returned no rows.

    Args:
        calls: The calls from athena_calls.

    Returns:
        True when there is at least one call and every one returned "[]"; False otherwise.
    """
    return bool(calls) and all(c["status"] != "error" and c["text"].strip() == "[]" for c in calls)


def filtered_values(sql: str, column: str) -> set[str]:
    """The values named by the query's `column = '…'` and `column IN ('…', …)` filters (a table alias allowed).

    Found in the masked SQL, so a filter inside a string literal or a comment does not count; NOT IN does not either.

    Args:
        sql: The query.
        column: The column, such as "account_id".

    Returns:
        The values; an empty set when the query does not filter on the column.
    """
    found = set()
    for match in re.finditer(rf"\b{re.escape(column)}\s*(=|\bIN\b)\s*", mask(sql), re.IGNORECASE):
        rest = sql[match.end():]
        if match.group(1) == "=":
            literal = re.match(r"'([^']*)'", rest)
            if literal:
                found.add(literal.group(1))
        elif rest.startswith("(") and ")" in rest:
            found |= set(re.findall(r"'([^']*)'", rest[1:rest.index(")")]))
    return found


def _accounts(ids: set) -> list[str]:
    """The known account IDs among `ids`, in agent_vars order; all three when none is named.

    Args:
        ids: Account IDs, as the queries name them.

    Returns:
        The LTTM account IDs among them; every LTTM account when there is none.
    """
    known = [a for a in vars.ACCOUNTS if a in ids]
    return known or list(vars.ACCOUNTS)


def _labels(accounts: list[str]) -> str:
    """Name accounts by their labels.

    Args:
        accounts: LTTM account IDs.

    Returns:
        "the three accounts" when all are there; their labels, separated by commas, otherwise.
    """
    names = [vars.ACCOUNTS[a]["label"] for a in accounts]
    return "the three accounts" if len(names) == len(vars.ACCOUNTS) else ", ".join(names)


def _union(calls: list[dict], column: str) -> set[str]:
    """Collect the values the queries name for a column.

    Args:
        calls: The calls from athena_calls.
        column: The column, such as "account_id".

    Returns:
        The values filtered_values finds in any query; an empty set when there are no calls.
    """
    return set().union(*(filtered_values(c["sql"], column) for c in calls)) if calls else set()


def cloudtrail_note(calls: list[dict]) -> str:
    """CloudTrail: an empty answer says which accounts and regions were searched, what the trail logs and since when.

    Args:
        calls: The calls from athena_calls.

    Returns:
        The note; "" unless every query succeeded and returned no rows.
    """
    if not all_empty(calls):
        return ""
    accounts = _accounts(_union(calls, "account_id"))
    regions = sorted(_union(calls, "region"))
    where = (f"in {', '.join(regions)}" if regions and set(regions) != set(vars.LAKE_REGIONS)
             else "in every region the lake holds")
    starts = {}
    for account in accounts:
        starts.setdefault(vars.CLOUDTRAIL_LAKE_START[account], []).append(vars.ACCOUNTS[account]["label"])
    since = " and ".join(f"{day} ({', '.join(labels)})" for day, labels in sorted(starts.items()))
    return (f"{NOTE} these queries found no CloudTrail events for {_labels(accounts)} {where}. The organization "
            f"trail logs every management event, read and write, of the three accounts in every region, but no data "
            f"events (for example S3 object reads and writes, or Lambda invocations); the lake holds CloudTrail "
            f"from {since}.")


def _grouped_by(sql: str, column: str) -> bool:
    """Tell whether a query groups its rows by a column (its name, or a position in the SELECT list).

    Args:
        sql: The query.
        column: The column, such as "aws_account_id".

    Returns:
        True when its GROUP BY names the column, or a position while the SELECT list names the column; False
        otherwise.
    """
    masked = mask(sql).lower()
    group = re.search(r"\bgroup\s+by\b(.*?)(?:\border\s+by\b|\blimit\b|\bhaving\b|$)", masked, re.S)
    if not group:
        return False
    if re.search(rf"\b{re.escape(column)}\b", group.group(1)):
        return True
    selected = re.search(r"\bselect\b(.*?)\bfrom\b", masked, re.S)
    return bool(re.search(r"\b\d+\b", group.group(1)) and selected
                and re.search(rf"\b{re.escape(column)}\b", selected.group(1)))


def missing_accounts(calls: list[dict], column: str) -> list[str]:
    """Find the accounts that grouped queries asked about (`column = …` / IN) and returned no row for.

    Only a grouped answer, one row per account at least, counts: a plain list of events stops at 20 rows, so an
    account missing there may still have rows. A grouped answer cut at its row limit does not count, and neither
    does one whose rows do not carry the column (renamed in the SELECT): those rows can't say whose they are, so
    every account would look empty.

    Args:
        calls: The calls from athena_calls.
        column: The account column, such as "aws_account_id".

    Returns:
        The account IDs, in agent_vars order; [] when no grouped answer can tell.
    """
    asked, seen, grouped = set(), set(), False
    for c in calls:
        if c["status"] == "error" or not _grouped_by(c["sql"], column):
            continue
        try:
            rows = json.loads(c["text"])
        except (TypeError, ValueError):
            return []
        if not isinstance(rows, list) or len(rows) > vars.GROUPED_ROW_LIMIT:
            return []
        if not all(isinstance(row, dict) and column in row for row in rows):
            return []
        grouped = True
        asked |= filtered_values(c["sql"], column)
        seen |= {str(row.get(column)) for row in rows if isinstance(row, dict) and row.get(column)}
    return [a for a in vars.ACCOUNTS if a in asked and a not in seen] if grouped else []


def flowlogs_note(calls: list[dict], question: str = "") -> str:
    """Flow Logs: a region an account does not collect is named when the question asks about that region; an empty
    answer says what is collected.

    Only a region the question names: with none named, the sub-agent queries every collected region for every
    account, and a line such as "not collected in us-west-2 (dev, prod)" next to an answer about all regions was
    offered as a possible cause of dev's and prod's zero. When other accounts have rows, the accounts asked about
    without a row get a line too, with where each is collected (the regions the queries read, when they name any):
    their flow logs cover every VPC, so no row means no traffic, which the supervisor otherwise guessed at. Account
    names are joined with "and" ("dev and prod"). named_regions is imported here, not at the top: hooks import
    utils, and a top-level import would be circular.

    Args:
        calls: The calls from athena_calls.
        question: The user's question, for the regions it names.

    Returns:
        The note lines; "" when no query succeeded, or there is nothing to say.
    """
    from hooks.sql_validator_hook import named_regions
    succeeded = [c for c in calls if c["status"] != "error"]
    if not succeeded:
        return ""
    by_account = vars.FLOWLOGS_REGIONS_BY_ACCOUNT
    accounts = _accounts(_union(succeeded, "aws_account_id"))
    regions = sorted(_union(succeeded, "aws_region") & named_regions(question))
    where = vars.coverage(by_account)
    missing = {}
    for region in regions:
        lacking = [a for a in accounts if region not in by_account[a]]
        if lacking:
            missing[region] = _labels(lacking)
    empty = [] if all_empty(calls) else missing_accounts(succeeded, "aws_account_id")
    empty_line = ""
    if empty:
        asked = set(regions) or set(vars.FLOWLOGS_REGIONS)
        places = sorted({r for a in empty for r in by_account[a] if r in asked})
        names = [vars.ACCOUNTS[a]["label"] for a in empty]
        named_empty = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        empty_line = (f"{NOTE} these queries found no flow records for {named_empty}: "
                      f"{'their' if len(empty) > 1 else 'its'} VPC flow logs are collected for every VPC in "
                      f"{', '.join(places) or where}, and a VPC without traffic writes no records.")
    if missing:
        named = ", ".join(f"{region} ({labels})" for region, labels in missing.items())
        found = " These queries found no flow records." if all_empty(calls) else ""
        return "\n".join(line for line in (f"{NOTE} VPC flow logs are not collected in {named}: they are collected in "
                                            f"{where}.{found}", empty_line) if line)
    if all_empty(calls):
        return (f"{NOTE} VPC flow logs are collected in {where}, for every VPC there, and these queries found no "
                f"flow records that match them.")
    return empty_line


def cloudwatch_note(calls: list[dict], lake_groups: dict | None = None) -> str:
    """CloudWatch: an empty answer says where logs reach the lake from (the table has no region column).

    It also says what the empty result means ("the lake holds no log event that matches them", the wording that
    stopped the guesses for Config), and, from `lake_groups`, whether each log group the queries named is in the
    lake at all: without that, the supervisor added "or its log group does not exist" to an empty answer about a
    log group that is in the lake. RUNTIME_LOGS says what of LTTM's own is not collected: its AgentCore runtime's
    logs and traces (terraform/cloudwatch.tf stops sending them); LTTM's own Lambda logs are in the lake.

    Args:
        calls: The calls from athena_calls.
        lake_groups: {log group: [the asked accounts whose logs of it are in the lake]}; None for none.

    Returns:
        The note lines; "" unless every query succeeded and returned no rows.
    """
    if not all_empty(calls):
        return ""
    lines = [f"{NOTE} CloudWatch logs reach the lake from {vars.coverage(vars.CLOUDWATCH_REGIONS_BY_ACCOUNT)}, and these "
             f"queries found no log events: the lake holds no log event that matches them. {RUNTIME_LOGS}"]
    asked = _accounts(_union(calls, "account_id"))
    for group, holders in sorted((lake_groups or {}).items()):
        if holders:
            lines.append(f"{NOTE} the log group {group} is in the lake ({_labels(holders)}), and none of its log "
                         f"events matches these queries.")
        else:
            lines.append(f"{NOTE} no log of the log group {group} was ever collected into the lake ({_labels(asked)}).")
    return "\n".join(lines)


def asked_log_groups(calls: list[dict]) -> tuple[list[str], list[str]]:
    """The log groups the queries named (at most 10) and the accounts they asked about.

    Args:
        calls: The calls from athena_calls.

    Returns:
        (the log group names, sorted; the account IDs, every LTTM account when the queries name none).
    """
    return sorted(_union(calls, "log_group"))[:10], _accounts(_union(calls, "account_id"))


LOG_GROUP_NAME = re.compile(r"(?<![\w/.-])/[A-Za-z0-9_.#-]+(?:/[A-Za-z0-9_.#$\[\]-]+)+")


def named_log_groups(text: str) -> list[str]:
    """The log groups a request names, in order, at most 10.

    A log group is written with a leading "/" and two parts at least (/aws/lambda/<name>, /aws/route53/<zone>,
    /API-Gateway-Execution-Logs_<id>/<stage>); "dev/prod" or a date has no leading "/". LTTM's own runtime log
    groups are left to the sub-agent, which answers that they are not available. is_agent_log_group is imported
    here, not at the top: hooks import utils, and a top-level import would be circular.

    Args:
        text: The request or the question.

    Returns:
        The names, each once; [] when it names none.
    """
    from hooks.sql_validator_hook import is_agent_log_group
    names = [match.group(0).rstrip(".,;:") for match in LOG_GROUP_NAME.finditer(text)]
    return [name for name in dict.fromkeys(names) if not is_agent_log_group(name)][:10]


def listed_groups_note(question: str, listing: dict) -> tuple[str, bool]:
    """When no query ran and the log-group list ({account: [names]}) is what the sub-agent found, write a line for
    each log group the request names: in the lake (for which accounts), or never collected.

    Without it, the supervisor read the list as the account's log groups ("There are 42 log groups in the main
    account") and said that a log group "does not exist in the main account". cloudwatch_note's "never collected"
    line needs a query, and the sub-agent does not query a log group the list lacks.

    Args:
        question: The user's question, for the log groups it names.
        listing: The log groups in the lake, per account ID.

    Returns:
        (the lines, "" for none; True when none of the named log groups is in the lake: then nothing matched the
        question).
    """
    names = named_log_groups(question)
    lines = []
    for group in names:
        holders = [account for account in vars.ACCOUNTS if group in listing.get(account, [])]
        lines.append(f"{NOTE} the log group {group} is in the lake ({_labels(holders)})." if holders else
                     f"{NOTE} no log of the log group {group} was ever collected into the lake "
                     f"({_labels(list(vars.ACCOUNTS))}).")
    in_lake = {name for names_in_account in listing.values() for name in names_in_account}
    return "\n".join(lines), bool(names) and not in_lake.intersection(names)


def named_accounts(text: str) -> set[str]:
    """The accounts a request names: an account ID, or a label as a word.

    The first account's label ("main", the default account) counts only before "account", so "the main errors" is
    not one.

    Args:
        text: The request or the question.

    Returns:
        The account IDs named; an empty set for none.
    """
    found = {account for account in vars.ACCOUNTS if account in text}
    for account, info in vars.ACCOUNTS.items():
        label = re.escape(info["label"])
        pattern = rf"\b{label}\s+accounts?\b" if account == vars.ACC1_ID else rf"\b{label}\b"
        if re.search(pattern, text, re.IGNORECASE):
            found.add(account)
    return found


def _cloudwatch_asked(question: str, calls: list[dict]) -> tuple[set[str], list[str]]:
    """The regions the question names, and the accounts it or the queries name.

    named_regions is imported here, not at the top: hooks import utils, and a top-level import would be circular.

    Args:
        question: The user's question.
        calls: The calls from athena_calls.

    Returns:
        (the regions named, the account IDs in agent_vars order).
    """
    from hooks.sql_validator_hook import named_regions
    accounts = named_accounts(question) | (_union(calls, "account_id") & set(vars.ACCOUNTS))
    return named_regions(question), [account for account in vars.ACCOUNTS if account in accounts]


def uncollected_accounts(question: str, calls: list[dict] = ()) -> list[str]:
    """The accounts asked about that collect none of the regions the question names.

    Their rows can only be logs of their other regions, so they answer nothing that was asked: the table has no
    region column, and a query about dev in us-west-2 would read dev's eu-central-1 logs instead.

    Args:
        question: The user's question.
        calls: The calls from athena_calls.

    Returns:
        The account IDs; [] when the question names no region.
    """
    regions, accounts = _cloudwatch_asked(question, calls)
    return [account for account in accounts
            if regions and not regions & set(vars.CLOUDWATCH_REGIONS_BY_ACCOUNT[account])]


def without_accounts(results: list[str], accounts: list[str]) -> list[str]:
    """Athena row lists without the rows of some accounts; other results unchanged.

    A row without an account_id is kept: nothing tells whose it is.

    Args:
        results: The tool results' texts.
        accounts: The account IDs whose rows go.

    Returns:
        The results, in order, each row list as JSON again; the results as they are when `accounts` is empty.
    """
    if not accounts:
        return results
    kept = []
    for text in results:
        try:
            rows = json.loads(text)
        except (TypeError, ValueError):
            kept.append(text)
            continue
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            kept.append(json.dumps([row for row in rows if str(row.get("account_id", "")) not in accounts]))
        else:
            kept.append(text)
    return kept


def cloudwatch_region_note(question: str, calls: list[dict] = ()) -> str:
    """CloudWatch: the regions a question names whose logs no account sends to the lake.

    The table has no region column, so no query can look there, and the sub-agent would answer such a question with
    its own text alone, which the supervisor must not explain from. Also a region inside the union that an account
    the question or its queries name does not collect (dev and prod: eu-central-1 only): the union holds that
    region, so without this it gets no line. Account names are joined with "and" ("dev and prod", not "dev, prod").

    Args:
        question: The user's question.
        calls: The calls from athena_calls.

    Returns:
        The note lines, the line on regions no account collects first; "" when there is nothing to say.
    """
    asked, accounts = _cloudwatch_asked(question, calls)
    regions = sorted(asked - set(vars.CLOUDWATCH_REGIONS))
    lines = []
    for region in sorted(asked & set(vars.CLOUDWATCH_REGIONS)):
        lacking = [account for account in accounts if region not in vars.CLOUDWATCH_REGIONS_BY_ACCOUNT[account]]
        if lacking:
            names = [vars.ACCOUNTS[account]["label"] for account in lacking]
            named = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            lines.append(f"{NOTE} CloudWatch logs of {named} are not collected in {region}: they reach the lake from "
                         f"{vars.coverage(vars.CLOUDWATCH_REGIONS_BY_ACCOUNT)}.")
    if not regions:
        return "\n".join(lines)
    return "\n".join([f"{NOTE} CloudWatch logs are not collected in {', '.join(regions)}: they reach the lake from "
                      f"{vars.coverage(vars.CLOUDWATCH_REGIONS_BY_ACCOUNT)}. {RUNTIME_LOGS}", *lines])


def own_log_groups_named(text: str) -> list[str]:
    """LTTM's own runtime log and trace groups a request names, in order.

    Their line comes from code (own_logs_note), not from the CloudWatch sub-agent's own text: no tool runs for them.
    is_agent_log_group is imported here, not at the top: hooks import utils, and a top-level import would be
    circular.

    Args:
        text: The request or the question.

    Returns:
        The names, each once; [] when it names none.
    """
    from hooks.sql_validator_hook import is_agent_log_group
    words = [word.rstrip(".,;:") for word in re.findall(r"[A-Za-z0-9_./#$\[\]-]+", text)]
    return list(dict.fromkeys(word for word in words if "/" in word or "-" in word if is_agent_log_group(word)))


def own_logs_note(groups: list[str]) -> str:
    """The line for LTTM's own log groups, which are never collected.

    Args:
        groups: The names from own_log_groups_named.

    Returns:
        One "Note (written by code):" line that names them.
    """
    return (f"{NOTE} {', '.join(groups)} {'is' if len(groups) == 1 else 'are'} LTTM's own AgentCore runtime log or "
            f"trace data: not collected into the lake, and not available to questions.")


def cur_note(calls: list[dict]) -> str:
    """CUR: an empty answer says which billing periods the cost data covers.

    Args:
        calls: The calls from athena_calls.

    Returns:
        The note; "" unless every query succeeded and returned no rows.
    """
    if not all_empty(calls):
        return ""
    return (f"{NOTE} these queries found no cost rows that match them. The cost and usage report covers the three "
            f"accounts from the billing period {vars.CUR_FIRST_BILLING_PERIOD} to the current month.")


def with_note(text: str, note: str) -> str:
    """The result with the line after it (never before: OutputIntegrityHook reads a result's first line).

    Args:
        text: The result.
        note: The note; "" for none.

    Returns:
        The result, a blank line and the note; the result alone when there is no note.
    """
    return f"{text}\n\n{note}" if note else text
