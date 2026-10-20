# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
guardduty_history_tool.py — Strands @tool for GuardDuty findings in the data-lake archive.

Registered on the guardduty_agent as @tool query_guardduty_history.

The GuardDuty API keeps findings for 90 days; older findings, and "all time"
questions, come from the archive `lttm_logs.guardduty_findings` (EventBridge →
Firehose → S3). The archive stores events, not findings, so this tool does the
counting itself:

- An updated finding is archived once per update, and every dev and prod finding
  twice (by the account itself and by main, their GuardDuty administrator). Each
  finding counts once, in its latest version in the date range.
- The counts cover every finding in the range, not only the ones listed, and are
  computed by Python, so the answer quotes them instead of counting rows.
- GuardDuty sample findings (`"sample": true`, test data GuardDuty generates on
  request) are counted and labelled apart from real findings.
- Severities are labelled with the API tool's bands (guardduty_tool), so both
  sources label a finding the same way.

The keys inside `detail` are lowercase as Athena reads them (`$.createdat`, not
`$.createdAt`). The archive has us-west-2 findings only from 2026-10-01 on, when
the rule guardduty_forward_main_uswest2 (terraform/guardduty_archive.tf) started
forwarding them.
"""

import re
import time
from datetime import date, datetime, timezone

import boto3
from strands import tool

import utils.agent_vars as _vars
import utils.request_context as request_context
from utils.finding_counts import count_line
from utils.sse_emitter import emit_status
from tools.guardduty_tool import _severity_label
from utils.untrusted_text import one_line_args

TABLE = "lttm_logs.guardduty_findings"
FIRST_DAY = date(2024, 1, 1)     # the table's first partition (projection.year.range in terraform/athena.tf)
MAX_FINDINGS = 10000
SEVERITY_NAMES = ["Critical", "High", "Medium", "Low"]
SAMPLE_NOTE = "GuardDuty test data, not real activity"

_FINDING_TYPE = re.compile(r"^[A-Za-z0-9:/._!&-]+$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")


def _label(account_id: str) -> str:
    """Label an account ID: "222222222222 (dev)" for an LTTM account, the ID alone for another.

    Args:
        account_id: A 12-digit account ID.

    Returns:
        The label.
    """
    return f"{account_id} ({_vars.ACCOUNTS[account_id]['label']})" if account_id in _vars.ACCOUNTS else account_id


def _accounts(account_id: str) -> list[str]:
    """Turn the accounts the model gave into account IDs.

    Args:
        account_id: "", "all", or account IDs or labels (main, dev, prod) separated by commas.

    Returns:
        The account IDs, each once, in order; every LTTM account for "" or "all".

    Raises:
        ValueError: An account that is not an LTTM account.
    """
    by_label = {v["label"]: k for k, v in _vars.ACCOUNTS.items()}
    wanted = [a.strip() for a in account_id.split(",") if a.strip() and a.strip().lower() != "all"]
    accounts = []
    for a in wanted:
        a = by_label.get(a.lower(), a)
        if a not in _vars.ACCOUNTS:
            raise ValueError(f"unknown account '{a}'; the accounts are {', '.join(map(_label, _vars.ACCOUNTS))}")
        accounts.append(a)
    return list(dict.fromkeys(accounts)) or list(_vars.ACCOUNTS)


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
    if not text:
        return default
    try:
        return date.fromisoformat(text[:10])
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


def build_sql(accounts: list[str], start: date, end: date, severity_min: float = 0.0,
              finding_type: str = "", region: str = "") -> str:
    """Build the SQL for every finding archived from start to end (inclusive), one row each, in its latest version.

    `finding_type` and `region` go into the SQL as they are: the tool checks them first against _FINDING_TYPE and
    _REGION, which let no quote or SQL through.

    Args:
        accounts: The account IDs to read.
        start: The first day.
        end: The last day, inclusive.
        severity_min: The lowest severity; 0 for all.
        finding_type: A finding type or its beginning; "" for all.
        region: Only findings raised in this region; "" for all.

    Returns:
        The query: a row per finding and account, its newest version in the range.
    """
    months = _months(start, end)
    years = sorted({y for y, _ in months})
    month_values = sorted({m for _, m in months})
    filters = ["newest = 1"]
    if severity_min > 0:
        filters.append(f"severity >= {float(severity_min)}")
    if finding_type:
        filters.append(f"starts_with(finding_type, '{finding_type}')")
    if region:
        filters.append(f"finding_region = '{region}'")
    return f"""WITH versions AS (
  SELECT account_id,
         json_extract_scalar(detail, '$.id') AS finding_id,
         json_extract_scalar(detail, '$.type') AS finding_type,
         CAST(json_extract_scalar(detail, '$.severity') AS double) AS severity,
         json_extract_scalar(detail, '$.title') AS title,
         json_extract_scalar(detail, '$.region') AS finding_region,
         json_extract_scalar(detail, '$.resource.resourcetype') AS resource_type,
         json_extract_scalar(detail, '$.service.count') AS event_count,
         json_extract_scalar(detail, '$.service.archived') AS archived,
         coalesce(json_extract_scalar(detail, '$.service.additionalinfo.sample'), 'false') AS sample,
         json_extract_scalar(detail, '$.createdat') AS created_at,
         json_extract_scalar(detail, '$.updatedat') AS updated_at,
         row_number() OVER (PARTITION BY account_id, json_extract_scalar(detail, '$.id')
                            ORDER BY json_extract_scalar(detail, '$.updatedat') DESC, time DESC) AS newest
  FROM {TABLE}
  WHERE account_id IN ({_vars.sql_list(accounts)})
    AND year IN ({_vars.sql_list(years)})
    AND month IN ({_vars.sql_list(month_values)})
    AND day BETWEEN '01' AND '31'
    AND concat(year, '-', month, '-', day) BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
)
SELECT account_id, finding_id, finding_type, severity, title, finding_region, resource_type,
       event_count, archived, sample, created_at, updated_at
FROM versions
WHERE {' AND '.join(filters)}"""


def _run(sql: str, timeout_seconds: int = 60) -> list[dict]:
    """Run an Athena query and return every row, all result pages followed.

    Every finding is fetched, for the counts. What the query scanned goes on the question's receipt.

    Args:
        sql: The query.
        timeout_seconds: How long to wait for it.

    Returns:
        The rows, as dicts keyed by column name; an empty list when none match.

    Raises:
        RuntimeError: The query failed or was cancelled.
        TimeoutError: The query took longer than `timeout_seconds`; it is stopped.
        ValueError: More than MAX_FINDINGS rows: a narrower range must be asked for.
    """
    athena = boto3.client("athena", region_name=_vars.DEFAULT_REGION)
    print(f"[LTTM:GuardDutyHistory] Executing query: {' '.join(sql.split())}", flush=True)
    execution_id = athena.start_query_execution(QueryString=sql, WorkGroup=_vars.ATHENA_WORKGROUP)["QueryExecutionId"]
    emit_status(f"Athena query executing (QueryExecutionId: {execution_id})", source="guardduty_history_tool")
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
            if len(rows) > MAX_FINDINGS:
                raise ValueError(f"more than {MAX_FINDINGS} findings in this range; ask for a shorter range")
    print(f"[LTTM:GuardDutyHistory] Query {execution_id} returned {len(rows)} findings", flush=True)
    return rows


def _severity(row: dict) -> float:
    """Read a finding row's severity.

    Args:
        row: A finding row from the query.

    Returns:
        The severity as a number; 0.0 when it is missing or not a number.
    """
    try:
        return float(row.get("severity") or 0)
    except ValueError:
        return 0.0


def _counted(name: str, rows: list[dict]) -> str:
    """Make a count line for some findings, under a name: "<name>: N (critical a, …)".

    Args:
        name: What the findings are, such as "Real findings".
        rows: The finding rows to count, by severity.

    Returns:
        The count line.
    """
    return name + count_line([_severity_label(_severity(r)) for r in rows], SEVERITY_NAMES)[len("Findings"):]


@one_line_args
def _format_finding(row: dict) -> str:
    """Format one archived finding as a block; every value on one line (one_line_args).

    Args:
        row: A finding row from the query.

    Returns:
        The "--- GuardDuty Finding ---" block, with "N/A" where a value is missing.
    """
    sample = row.get("sample") == "true"
    return "\n".join([
        "--- GuardDuty Finding ---",
        f"Account: {_label(row.get('account_id', ''))}",
        f"Sample: {'yes — ' + SAMPLE_NOTE if sample else 'no'}",
        f"Finding Type: {row.get('finding_type') or 'N/A'}",
        f"Severity: {_severity(row)} ({_severity_label(_severity(row))})",
        f"Title: {row.get('title') or 'N/A'}",
        f"Resource Type: {row.get('resource_type') or 'N/A'}",
        f"Region: {row.get('finding_region') or 'N/A'}",
        f"Event Count: {row.get('event_count') or 'N/A'}",
        f"Archived: {row.get('archived') or 'N/A'}",
        f"Created: {row.get('created_at') or 'N/A'}",
        f"Updated: {row.get('updated_at') or 'N/A'}",
        f"Finding ID: {row.get('finding_id') or 'N/A'}",
    ])


def summarize(rows: list[dict], accounts: list[str], start: date, end: date, max_results: int) -> str:
    """Write the counts over every finding, then the top max_results findings: real ones first, then by severity.

    Two stable sorts list the newest first among findings of the same kind and severity.

    Args:
        rows: Every finding of the range, one row each.
        accounts: The account IDs read.
        start: The first day read.
        end: The last day read.
        max_results: How many findings to list in detail.

    Returns:
        The count lines (all, sample and real when there are samples, by account when there are several), the
        source line and the finding blocks; "No GuardDuty findings found in the archive for …" when there are none.
    """
    scope = f"accounts {', '.join(map(_label, accounts))}, {start.isoformat()} to {end.isoformat()}"
    if not rows:
        return f"No GuardDuty findings found in the archive for {scope}."
    samples = [r for r in rows if r.get("sample") == "true"]
    lines = [count_line([_severity_label(_severity(r)) for r in rows], SEVERITY_NAMES)]
    if samples:
        lines.append(_counted(f"Sample findings ({SAMPLE_NOTE})", samples))
        lines.append(_counted("Real findings", [r for r in rows if r.get("sample") != "true"]))
    if len(accounts) > 1:
        per_account = {a: 0 for a in accounts}
        for r in rows:
            per_account[r.get("account_id")] = per_account.get(r.get("account_id"), 0) + 1
        lines.append("By account: " + ", ".join(f"{_label(a)} {n}" for a, n in per_account.items()))
    lines.append(f"Source: the GuardDuty archive in the data lake, {scope}. "
                 f"Each finding is counted once, in its latest version in that range.")

    shown = sorted(rows, key=lambda r: r.get("updated_at") or "", reverse=True)
    shown = sorted(shown, key=lambda r: (r.get("sample") == "true", -_severity(r)))[:max_results]
    lines.append(f"Showing {len(shown)} of {len(rows)} findings: real findings first, then by severity."
                 if len(shown) < len(rows) else f"Showing all {len(rows)} findings.")
    return "\n".join(lines) + "\n\n" + "\n\n".join(_format_finding(r) for r in shown)


@tool
def query_guardduty_history(
    start_date: str = "",
    end_date: str = "",
    account_id: str = "",
    severity_min: float = 0.0,
    finding_type: str = "",
    region: str = "",
    max_results: int = 20,
) -> str:
    """
    Count and list GuardDuty findings from the data-lake archive, for any date range.
    Use it for findings older than 90 days, "all time" and "historical" questions.

    Counts every finding in the range once (the archive stores one event per update,
    and dev and prod findings twice), counts GuardDuty sample findings (generated test
    data) apart from real ones, and lists the top findings in detail: real ones first,
    then by severity. One call covers every account and the whole date range.

    Args:
        start_date: First day, YYYY-MM-DD. Empty = from the start of the archive.
        end_date: Last day, YYYY-MM-DD, inclusive. Empty = today.
        account_id: Account ID, several separated by commas, or empty (default) for all
            three accounts. Put every account of the question into this one call.
        severity_min: Minimum severity (0 = all, 4 = Medium and up, 7 = High and up, 9 = Critical).
        finding_type: A finding type or its beginning, e.g. "Recon:" or
            "UnauthorizedAccess:IAMUser/MaliciousIPCaller". Empty = all types.
        region: Only findings raised in this region. Empty = all regions.
        max_results: How many findings to list in detail (default 20, at most 100);
            the counts always cover all of them.

    Returns:
        The counts (all findings by severity; sample and real findings apart; per account),
        then the listed findings, each marked "Sample: yes" or "Sample: no"; or an error message.
    """
    emit_status("Querying the GuardDuty archive...", source="guardduty_history_tool")
    try:
        accounts = _accounts(account_id)
        today = datetime.now(timezone.utc).date()
        start, end = _day(start_date, FIRST_DAY), _day(end_date, today)
        if start > end:
            raise ValueError(f"start_date {start} is after end_date {end}")
        start, end = max(start, FIRST_DAY), min(end, today)
        if finding_type and not _FINDING_TYPE.match(finding_type):
            raise ValueError(f"'{finding_type}' is not a GuardDuty finding type")
        if region and not _REGION.match(region):
            raise ValueError(f"'{region}' is not a region code such as eu-central-1")
        if not 0 <= float(severity_min) <= 10:
            raise ValueError("severity_min must be between 0 and 10")
        max_results = min(max(int(max_results), 1), 100)

        rows = _run(build_sql(accounts, start, end, float(severity_min), finding_type, region))
        emit_status(f"GuardDuty archive query complete — {len(rows)} findings", source="guardduty_history_tool")
        return summarize(rows, accounts, start, end, max_results)
    except Exception as e:
        msg = f"Error querying the GuardDuty archive: {type(e).__name__}: {e}"
        print(f"[LTTM:GuardDutyHistory] {msg}", flush=True)
        return msg
