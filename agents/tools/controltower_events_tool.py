# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
controltower_events_tool.py — Strands @tool for what AWS Control Tower did, and who started it, from CloudTrail.

Registered on the controltower_agent as @tool query_controltower_events.

Everything Control Tower does is a CloudTrail event, already in `lttm_logs.cloudtrail_logs` through the
organization trail: its API calls (EnableControl, UpdateLandingZone, ResetEnabledControl, …) and its lifecycle
events (eventtype AwsServiceEvent: the completion record of an action, SUCCEEDED or FAILED), all with the event
source `controltower.amazonaws.com`, in the management account and Control Tower's home region. The API keeps
landing-zone operations for 90 days only; the lake keeps these events from CLOUDTRAIL_LAKE_START on. Without a start
date, a call reads the last DEFAULT_DAYS days, as far back as the API's operations go.

- The SQL is built here, fixed to that event source, account and region, and it filters every partition key to
  exactly the days asked; the sub-agent writes no SQL, so none of the CloudTrail sub-agent's rules are copied.
- Read-only calls are left out unless asked: they are lookups (LTTM's own Control Tower reads among them), not
  something Control Tower did.
- Counts are made by code over every event in the range, not only the listed ones.
- An empty answer gets a line written by code that says what CloudTrail holds: the supervisor
  is the only one who explains, and without the fact it guessed. Its first line says "no events", so
  OutputIntegrityHook reads it as a part that rightly has nothing.
- Every answer says, in a line written by code, that drift is not among these events: Control Tower reports drift
  only as it is now, and LTTM keeps no record of past drift.
- Every answer also says, in a line written by code (WHY), what the events can tell: what was called or done, by
  whom and when, not why. Without it, answers gave causes that no event holds.
- On the days of Control Tower's lifecycle events, AWS Organizations' account and OU changes (ORG_CHANGES) are read
  too, and listed before the lines written by code, as what happened, not why. Control Tower acts on its own after
  such changes, and without that sequence the answers made up a cause. One _Names resolver labels the events and
  those changes.
- A target is resolved as the API tool resolves it ("dev", "Workloads", an ID or an ARN), with the API tool's own
  labels and target names, so an event matches the account or OU it names, not only the text typed.
- What Athena scanned goes on the question's receipt.
"""

import json
import re
import time
from datetime import date, datetime, timedelta, timezone

import boto3
from strands import tool

import utils.agent_vars as _vars
import utils.request_context as request_context
from utils.sse_emitter import emit_status
from utils.untrusted_text import one_line, one_line_args
from tools.controltower_tool import _Names, _breakdown, _targets

TABLE = "lttm_logs.cloudtrail_logs"
EVENT_SOURCE = "controltower.amazonaws.com"
DEFAULT_DAYS = 90
MAX_EVENTS = 10000
MAX_LISTED = 100
NOTE = "Note (written by code):"
DRIFT = ("Control Tower's drift reports are not CloudTrail events: drift is known only as it is now, and LTTM keeps no "
         "record of past drift.")
WHY = ("CloudTrail records what was called or done, who started it and when, not why. Control Tower's events are read "
       "here (its API calls and lifecycle events), and, on the days of its lifecycle events, AWS Organizations' account "
       "and OU changes; changes made through other services are not among them.")
ORG_SOURCE = "organizations.amazonaws.com"
ORG_CHANGES = ("MoveAccount", "CreateAccount", "CreateOrganizationalUnit", "DeleteOrganizationalUnit",
               "RemoveAccountFromOrganization", "InviteAccountToOrganization")
_TARGET_KEYS = ("targetidentifier", "landingzoneidentifier", "enabledcontrolidentifier", "enabledbaselineidentifier",
                "organizationalunitid", "accountid")
_CONTROL_KEYS = ("controlidentifier", "guardrailid", "guardrailname", "baselineidentifier")


def _today() -> date:
    """Give today's date in UTC, read when a question is asked.

    Args:
        None.

    Returns:
        Today's date.
    """
    return datetime.now(timezone.utc).date()


def _day(text: str, default: date) -> date:
    """Read a YYYY-MM-DD date.

    Args:
        text: The date as the model gave it; only its first 10 characters are read.
        default: The date to use when `text` is empty.

    Returns:
        The date; `default` when `text` is empty.

    Raises:
        ValueError: `text` is not a YYYY-MM-DD date.
    """
    if not str(text or "").strip():
        return default
    try:
        return date.fromisoformat(str(text).strip()[:10])
    except ValueError:
        raise ValueError(f"dates must be YYYY-MM-DD, got '{text}'")


def _months(start: date, end: date) -> list[tuple[str, str]]:
    """List every (year, month) from start to end, as the zero-padded partition values.

    Args:
        start: The first day.
        end: The last day.

    Returns:
        ("2026", "09"), ("2026", "10"), …, oldest first; an empty list when `end` is in an earlier month.
    """
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((str(y), f"{m:02d}"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def build_sql(start: date, end: date, writes_only: bool) -> str:
    """Build the SQL for every Control Tower event from start to end (inclusive); every partition key is filtered.

    Control Tower is a management-account service with one home region: its events are the main account's, there.

    Args:
        start: The first day.
        end: The last day, inclusive.
        writes_only: True to leave out the read-only calls.

    Returns:
        The SELECT, with the event's identity, request and service event details as columns.
    """
    months = _months(start, end)
    years = sorted({y for y, _ in months})
    month_values = sorted({m for _, m in months})
    writes = "\n  AND coalesce(readonly, 'false') <> 'true'" if writes_only else ""
    return f"""SELECT eventid, eventtime, eventname, eventtype, readonly, useridentity.type AS who_type,
  useridentity.arn AS who_arn, useridentity.username AS who_name, sourceipaddress, errorcode, errormessage,
  requestparameters, serviceeventdetails
FROM {TABLE}
WHERE account_id IN ({_vars.sql_list([_vars.ACC1_ID])})
  AND region IN ({_vars.sql_list([_vars.CONTROLTOWER_REGION])})
  AND year IN ({_vars.sql_list(years)}) AND month IN ({_vars.sql_list(month_values)}) AND day BETWEEN '01' AND '31'
  AND concat(year, '-', month, '-', day) BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
  AND eventsource = '{EVENT_SOURCE}'{writes}"""


def build_org_sql(days: list[date]) -> str:
    """Build the SQL for AWS Organizations' account and OU changes on some days; every partition key is filtered.

    ORG_CHANGES are the calls that change which accounts and OUs there are, and where an account sits. AWS
    Organizations logs them in the main account and its global region. Refused calls are left out.

    Args:
        days: The days to read, each a whole day in UTC.

    Returns:
        The SELECT, with the event's identity and request as columns.
    """
    years = sorted({str(d.year) for d in days})
    months = sorted({f"{d.month:02d}" for d in days})
    day_values = sorted({f"{d.day:02d}" for d in days})
    return f"""SELECT eventid, eventtime, eventname, eventtype, useridentity.arn AS who_arn,
  useridentity.username AS who_name, useridentity.type AS who_type, requestparameters, errorcode
FROM {TABLE}
WHERE account_id IN ({_vars.sql_list([_vars.ACC1_ID])})
  AND region IN ({_vars.sql_list([_vars.GLOBAL_SERVICE_REGION])})
  AND year IN ({_vars.sql_list(years)}) AND month IN ({_vars.sql_list(months)}) AND day IN ({_vars.sql_list(day_values)})
  AND concat(year, '-', month, '-', day) IN ({_vars.sql_list([d.isoformat() for d in days])})
  AND eventsource = '{ORG_SOURCE}' AND eventname IN ({_vars.sql_list(list(ORG_CHANGES))})
  AND coalesce(errorcode, '') = ''"""


@one_line_args
def _format_org_event(when: str, name: str, who: str, account: str, source: str, destination: str,
                      event_id: str) -> str:
    """Format one AWS Organizations change as a block; every value on one line (one_line_args).

    Args:
        when: The event time.
        name: The event name, such as MoveAccount.
        who: Who made the call.
        account: The account it concerns, labelled; "" for none.
        source: The parent the account left, labelled; "" for none.
        destination: The parent the account moved to, labelled; "" for none.
        event_id: The CloudTrail event ID.

    Returns:
        The "--- AWS Organizations event ---" block.
    """
    lines = ["--- AWS Organizations event ---", f"Time: {when}", f"Event: {name} (API call, call accepted)",
             f"By: {who}"]
    for label, value in (("Account", account), ("From", source), ("To", destination)):
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"Event ID: {event_id}")
    return "\n".join(lines)


def org_changes(found: list[dict], names: _Names) -> str:
    """Read AWS Organizations' account and OU changes on the days of the lifecycle events found.

    Args:
        found: The Control Tower events of the range (events).
        names: The call's _Names, to label accounts and OUs.

    Returns:
        A head line with the count and the names, then one block per change, oldest first; the head line with
        "none." when there are no changes; "" when `found` has no lifecycle event.

    Raises:
        Exception: Any error of the Athena query (_run); the tool reports it.
    """
    days = sorted({date.fromisoformat((e.get("eventtime") or "")[:10]) for e in found
                   if _lifecycle(e) and (e.get("eventtime") or "")[:10]})
    if not days:
        return ""
    changes = sorted(events(_run(build_org_sql(days))), key=lambda e: e.get("eventtime") or "")
    on = ", ".join(d.isoformat() for d in days)
    head = (f"AWS Organizations account and OU changes on {on}, the days of these lifecycle events (CloudTrail, event "
            f"source {ORG_SOURCE}, the main account in {_vars.GLOBAL_SERVICE_REGION})")
    if not changes:
        return f"{head}: none."
    names_seen = [one_line(e.get("eventname") or "unknown") for e in changes]
    blocks = [_format_org_event(e.get("eventtime") or "N/A", e.get("eventname") or "unknown",
                                one_line(e.get("who_arn") or e.get("who_name") or e.get("who_type") or "unknown"),
                                names.label(acct) if (acct := _find(e["request"], ("accountid",))) else "",
                                names.label(src) if (src := _find(e["request"], ("sourceparentid",))) else "",
                                names.label(dst) if (dst := _find(e["request"], ("destinationparentid",))) else "",
                                e.get("eventid") or "")
              for e in changes]
    return f"{head}: {len(changes)} ({_breakdown(names_seen)}), oldest first.\n\n" + "\n\n".join(blocks)


def _run(sql: str, timeout_seconds: int = 60) -> list[dict]:
    """Run an Athena query and return every row, all result pages followed.

    Every event is fetched, for the counts. What the query scanned goes on the question's receipt.

    Args:
        sql: The query.
        timeout_seconds: How long to wait for it.

    Returns:
        The rows, as dicts keyed by column name; an empty list when none match.

    Raises:
        RuntimeError: The query failed or was cancelled.
        TimeoutError: The query took longer than `timeout_seconds`; it is stopped.
        ValueError: More than MAX_EVENTS rows: a narrower range must be asked for.
    """
    athena = boto3.client("athena", region_name=_vars.DEFAULT_REGION)
    print(f"[LTTM:ControlTowerEvents] Executing query: {' '.join(sql.split())}", flush=True)
    execution_id = athena.start_query_execution(QueryString=sql, WorkGroup=_vars.ATHENA_WORKGROUP)["QueryExecutionId"]
    emit_status(f"Athena query executing (QueryExecutionId: {execution_id})", source="controltower_events_tool")
    deadline = time.monotonic() + timeout_seconds
    while True:
        execution = athena.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"]
        status = execution["Status"]
        if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            request_context.current().receipt.add_athena(execution.get("Statistics", {}).get("DataScannedInBytes", 0))
        if status["State"] == "SUCCEEDED":
            break
        if status["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Query {execution_id} failed: {status.get('StateChangeReason', 'no reason provided')}")
        if time.monotonic() >= deadline:
            athena.stop_query_execution(QueryExecutionId=execution_id)
            raise TimeoutError(f"Query {execution_id} did not complete within {timeout_seconds}s")
        time.sleep(1)

    header, rows = None, []
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=execution_id):
        for row in page["ResultSet"]["Rows"]:
            values = [cell.get("VarCharValue", "") for cell in row["Data"]]
            if header is None:
                header = values
                continue
            rows.append(dict(zip(header, values)))
            if len(rows) > MAX_EVENTS:
                raise ValueError(f"more than {MAX_EVENTS} Control Tower events in this range; ask for a shorter range")
    print(f"[LTTM:ControlTowerEvents] Query {execution_id} returned {len(rows)} events", flush=True)
    return rows


def _lower_keys(value):
    """Put every dict key of a parsed JSON value in lower case, at every depth.

    Args:
        value: A parsed JSON value.

    Returns:
        The same value, its dict keys in lower case.
    """
    if isinstance(value, dict):
        return {str(key).lower(): _lower_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_lower_keys(item) for item in value]
    return value


def _json(text: str) -> dict:
    """Parse a JSON text column into a dict with lower-case keys.

    Args:
        text: The column's text, such as requestparameters.

    Returns:
        The object, its keys in lower case at every depth; {} when the text is empty, not JSON, or not an object.
    """
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return _lower_keys(value) if isinstance(value, dict) else {}


def _find(value, keys: tuple[str, ...]) -> str:
    """Find the first value of any of some keys, at every depth of a parsed JSON object.

    Args:
        value: A parsed JSON value with lower-case keys.
        keys: The keys to look for, in lower case; at each level, the first key in this order wins.

    Returns:
        The value found: a string as it is, anything else as compact JSON; "" when none of the keys has a value.
    """
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) not in (None, "", [], {}):
                found = value[key]
                return found if isinstance(found, str) else json.dumps(found, separators=(",", ":"))
        for item in value.values():
            found = _find(item, keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find(item, keys)
            if found:
                return found
    return ""


def events(rows: list[dict]) -> list[dict]:
    """Turn query rows into events, each once (by its CloudTrail event ID), with their JSON columns parsed.

    Args:
        rows: The rows from _run.

    Returns:
        One dict per event: the row, plus "request" and "service", its parsed requestparameters and
        serviceeventdetails.
    """
    found = {}
    for row in rows:
        event_id = row.get("eventid") or f"no-id-{len(found)}"
        found.setdefault(event_id, {**row, "request": _json(row.get("requestparameters")),
                                    "service": _json(row.get("serviceeventdetails"))})
    return list(found.values())


def _lifecycle(event: dict) -> bool:
    """Tell whether an event is one of Control Tower's lifecycle events, not an API call.

    Args:
        event: An event from events.

    Returns:
        True for an AwsServiceEvent, False otherwise.
    """
    return (event.get("eventtype") or "") == "AwsServiceEvent"


def _who(event: dict) -> str:
    """Say who started an event.

    Args:
        event: An event from events.

    Returns:
        The user or role session of an API call, on one line; "Control Tower (lifecycle event)" for a lifecycle
        event; "unknown" when the event has no identity.
    """
    if _lifecycle(event):
        return "Control Tower (lifecycle event)"
    return one_line(event.get("who_arn") or event.get("who_name") or event.get("who_type") or "unknown")


def _outcome(event: dict) -> str:
    """Say how an event ended.

    Args:
        event: An event from events.

    Returns:
        A lifecycle event's state (SUCCEEDED or FAILED, "unknown" when missing); for an API call, "error <code>"
        or "call accepted".
    """
    if _lifecycle(event):
        return _find(event["service"], ("state",)) or "unknown"
    return f"error {event['errorcode']}" if event.get("errorcode") else "call accepted"


def _control(event: dict) -> str:
    """Find the control (or baseline) an event concerns, by its alias: "AWS-GR_RESTRICT_ROOT_USER", not the full ARN.

    Args:
        event: An event from events.

    Returns:
        The control's alias, or the baseline's identifier; "" when the event names none.
    """
    found = _find(event["request"], _CONTROL_KEYS) or _find(event["service"], _CONTROL_KEYS)
    return found.rsplit("control/", 1)[-1] if "::control/" in found else found


def _target(event: dict, names: _Names) -> str:
    """Find the OU, account or landing zone an event acted on, labelled.

    A landing zone event's records also name the management account, which is not its target, so the landing zone
    is taken. For any other event, the keys of _TARGET_KEYS are searched in their order, the OU first.

    Args:
        event: An event from events.
        names: The call's _Names, to label accounts and OUs.

    Returns:
        The target's label; "" when the event names none.
    """
    if "LandingZone" in (event.get("eventname") or ""):
        return one_line(_find(event["request"], ("landingzoneidentifier",)) or "the landing zone")
    for source in (event["request"], event["service"]):
        ident = _find(source, ("organizationalunitid",) + _TARGET_KEYS)
        if ident:
            return names.label(ident) if ident.startswith(("arn:", "ou-")) or ident.isdigit() else one_line(ident)
    return ""


@one_line_args
def _format_event(when: str, name: str, kind: str, outcome: str, who: str, target: str, control: str,
                  source_ip: str, event_id: str) -> str:
    """Format one event as a block; every value on one line (one_line_args).

    Args:
        when: The event time.
        name: The event name.
        kind: "API call" or "lifecycle event".
        outcome: How it ended (_outcome).
        who: Who started it (_who).
        target: What it acted on (_target); "" for nothing.
        control: The control it concerns (_control); "" for none.
        source_ip: An API call's source IP; "" for none.
        event_id: The CloudTrail event ID.

    Returns:
        The "--- Control Tower event ---" block.
    """
    lines = ["--- Control Tower event ---", f"Time: {when}", f"Event: {name} ({kind}, {outcome})", f"By: {who}"]
    for label, value in (("Target", target), ("Control", control), ("Source IP", source_ip)):
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"Event ID: {event_id}")
    return "\n".join(lines)


def _block(event: dict, names: _Names) -> str:
    """Format an event as a block.

    Args:
        event: An event from events.
        names: The call's _Names, to label the target.

    Returns:
        The event's block (_format_event); a lifecycle event has no source IP.
    """
    kind = "lifecycle event" if _lifecycle(event) else "API call"
    control = _control(event)
    return _format_event(event.get("eventtime") or "N/A", event.get("eventname") or "unknown", kind, _outcome(event),
                         _who(event), _target(event, names), control,
                         "" if _lifecycle(event) else (event.get("sourceipaddress") or ""), event.get("eventid") or "")


def _strings(value) -> list[str]:
    """List every string value of a parsed JSON value, at every depth, in lower case.

    Args:
        value: A parsed JSON value.

    Returns:
        The strings, in lower case; an empty list when there are none.
    """
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return [value.lower()] if isinstance(value, str) else []


def _concerns(target: str, names: _Names):
    """A test for "this event concerns `target`" (several items, separated by commas: any of them).

    An account or OU (an ID, an ARN, a label such as "dev", an OU's name such as "Workloads") is resolved as the
    API tool resolves it; an event concerns it when a value of the event is its ID or ends with "/<its ID>" (its
    ARN). Never inside a longer value: the main account's ID is part of every OU's ARN. Any other item, a control's
    name for example, matches as a whole word of the event: AWS-GR_RESTRICT_ROOT_USER, not
    AWS-GR_RESTRICT_ROOT_USER_ACCESS_KEYS. So does an item that is no account or OU, or any item when AWS
    Organizations can't be read (logged).

    Args:
        target: The items, separated by commas; "all" and "*" are left out.
        names: The call's _Names, to resolve accounts and OUs.

    Returns:
        A function of an event that is True when the event concerns any item, or always True when no item is left.
    """
    tests = []
    for item in [part.strip() for part in target.split(",") if part.strip()]:
        if item.lower() in ("all", "*"):
            continue
        try:
            idents = [t.ident.lower() for t in _targets(item, names)]
        except Exception as e:
            print(f"[LTTM:ControlTowerEvents] Target '{item}' matched as text: {type(e).__name__}: {e}", flush=True)
            idents = []
        if idents:
            tests.append(lambda values, text, idents=idents: any(
                value == ident or value.endswith("/" + ident) for value in values for ident in idents))
        else:
            word = re.compile(rf"(?<![\w-]){re.escape(item.lower())}(?![\w-])")
            tests.append(lambda values, text, word=word: bool(word.search(text)))

    def concerns(event: dict) -> bool:
        """Tell whether an event concerns any of the items.

        Args:
            event: An event from events.

        Returns:
            True when one test matches the event's request or service details, or there are no tests.
        """
        if not tests:
            return True
        parts = [event["request"], event["service"]]
        values, text = _strings(parts), json.dumps(parts).lower()
        return any(test(values, text) for test in tests)
    return concerns


def empty_line(start: date, end: date, asked: str, writes_only: bool, earlier: str = "") -> str:
    """Write what an empty answer means, as code; its "no events" makes OutputIntegrityHook read it as a part that
    rightly has nothing (EMPTY_PART_PATTERNS).

    Args:
        start: The first day read.
        end: The last day read.
        asked: The filters, as " named … concerning …"; "" for none.
        writes_only: True when read-only calls were left out.
        earlier: The sentence about days before the lake starts; "" for none.

    Returns:
        "Control Tower events: none (no events match).", then a note on what was read, and the DRIFT line.
    """
    what = "Control Tower changes (read-only calls left out)" if writes_only else "Control Tower events"
    return (f"Control Tower events: none (no events match).\n{NOTE} These queries read Control Tower's events in "
            f"CloudTrail (event source {EVENT_SOURCE}: its API calls and lifecycle events) of the main account in "
            f"Control Tower's home region {_vars.CONTROLTOWER_REGION}, where it makes its changes; the lake holds "
            f"CloudTrail from {_vars.CLOUDTRAIL_LAKE_START[_vars.ACC1_ID]}. They found no {what}{asked} from "
            f"{start.isoformat()} to {end.isoformat()}.{earlier} {DRIFT}")


def summarize(found: list[dict], start: date, end: date, event_name: str, target: str, writes_only: bool,
              max_results: int, names: _Names, before_lake: bool) -> str:
    """Write the counts over every event of the range, then the newest max_results events.

    The target is resolved once, not per event. Event names keep their case in the counts. The DRIFT and WHY lines
    end the text.

    Args:
        found: The events of the range (events).
        start: The first day read.
        end: The last day read.
        event_name: Only events of this name, in any case; "" for all.
        target: Only events that concern it (_concerns); "" for all.
        writes_only: True when read-only calls were left out.
        max_results: How many events to list in detail.
        names: The call's _Names.
        before_lake: True when the range asked for starts before the lake does.

    Returns:
        The count lines, the source line and the event blocks, then the two notes; empty_line's text when no event
        is left.
    """
    if event_name:
        found = [e for e in found if (e.get("eventname") or "").lower() == event_name.lower()]
    if target:
        concerns = _concerns(target, names)
        found = [e for e in found if concerns(e)]
    asked = "".join([f" named {event_name}" if event_name else "", f" concerning {target}" if target else ""])
    earlier = (f" The lake holds CloudTrail from {_vars.CLOUDTRAIL_LAKE_START[_vars.ACC1_ID]}: earlier days cannot be "
               f"read." if before_lake else "")
    if not found:
        return empty_line(start, end, asked, writes_only, earlier)
    names_seen = [one_line(e.get("eventname") or "unknown") for e in found]
    who = [_who(e) for e in found]
    newest = sorted(found, key=lambda e: e.get("eventtime") or "", reverse=True)[:max_results]
    lines = [f"Control Tower events: {len(found)} ({_breakdown(names_seen)})",
             f"By who: {_breakdown(who, sorted(set(who)))}",
             f"Source: CloudTrail in the data lake, event source {EVENT_SOURCE}, the main account in "
             f"{_vars.CONTROLTOWER_REGION}, from {start.isoformat()} to {end.isoformat()}"
             + (", read-only calls left out" if writes_only else "")
             + ". Counted by code over every event, each once. CloudTrail delivers events within about 5 minutes."
             + earlier,
             f"Showing {len(newest)} of {len(found)} events, newest first." if len(newest) < len(found)
             else f"Showing all {len(found)} events, newest first."]
    return ("\n".join(lines) + "\n\n" + "\n\n".join(_block(e, names) for e in newest) + f"\n\n{NOTE} {DRIFT}"
            f"\n{NOTE} {WHY}")


@tool
def query_controltower_events(
    start_date: str = "",
    end_date: str = "",
    event_name: str = "",
    target: str = "",
    include_reads: bool = False,
    max_results: int = 20,
) -> str:
    """
    What AWS Control Tower did, and who started it, from CloudTrail in the data lake: its API calls (for example
    EnableControl, DisableControl, ResetEnabledControl, UpdateLandingZone) and its lifecycle events (the completion
    record of an action, SUCCEEDED or FAILED). Use it for the past ("on 2026-10-09", "last month", "since March",
    "ever") and for "who changed it"; the current state comes from query_controltower_api.

    Counts every event in the range by event name and by who, and lists the newest first. One call covers the
    whole range.

    Args:
        start_date: First day, YYYY-MM-DD. Empty = 90 days before end_date. For "ever", the day the lake starts.
        end_date: Last day, YYYY-MM-DD, inclusive. Empty = today.
        event_name: One event name, e.g. EnableControl. Empty = all.
        target: What the events must concern: an account (main, dev, prod or its ID), an OU (its name or ID), or a
            control's name (e.g. AWS-GR_RESTRICT_ROOT_USER). Empty = all.
        include_reads: True to include read-only calls (Get…, List…). Default False: changes only.
        max_results: How many events to list in detail (default 20, at most 100); the counts cover all.

    Returns:
        "Control Tower events: N (…)", "By who: …", the source line, then the events; or "Control Tower events:
        none (…)" with a line written by code that says what CloudTrail holds; or an error message.
    """
    emit_status("Querying Control Tower's events in CloudTrail...", source="controltower_events_tool")
    try:
        today = _today()
        end = min(_day(end_date, today), today)
        asked_start = _day(start_date, end - timedelta(days=DEFAULT_DAYS))
        if asked_start > end:
            raise ValueError(f"start_date {asked_start} is after end_date {end}")
        lake_start = date.fromisoformat(_vars.CLOUDTRAIL_LAKE_START[_vars.ACC1_ID])
        start = max(asked_start, lake_start)
        max_results = min(max(int(max_results), 1), MAX_LISTED)
        writes_only = not include_reads
        if end < start:
            return (f"Control Tower events: none (no events can be read for this range).\n{NOTE} The lake holds "
                    f"CloudTrail from {lake_start.isoformat()}: the days from {asked_start.isoformat()} to "
                    f"{end.isoformat()} are before it and cannot be read. {DRIFT}")
        found = events(_run(build_sql(start, end, writes_only)))
        names = _Names()
        text = summarize(found, start, end, one_line(event_name.strip()), one_line(target.strip()), writes_only,
                         max_results, names, asked_start < start)
        org = org_changes(found, names)
        if org and f"\n\n{NOTE} {DRIFT}" in text:
            text = text.replace(f"\n\n{NOTE} {DRIFT}", f"\n\n{org}\n\n{NOTE} {DRIFT}", 1)
        emit_status(f"Control Tower events query complete — {len(found)} events in the range",
                    source="controltower_events_tool")
        return text
    except Exception as e:
        msg = f"Error querying Control Tower's events in CloudTrail: {type(e).__name__}: {e}"
        print(f"[LTTM:ControlTowerEvents] {msg}", flush=True)
        return msg
