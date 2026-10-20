# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
athena_tool.py — Shared Strands @tool for executing Athena queries.

Registered on six Athena-based sub-agents (cloudtrail, cloudwatch, config,
cur, flowlogs, guardduty) as @tool run_athena_query. Also exports
`format_athena_rows`, a plain helper those agents use to turn row lists
into human-readable text blocks for the supervisor.

The SQL the sub-agents write flows through here, so `SQLValidatorHook` and
`SQLRewriteHook` have one tool name (`run_athena_query`) to intercept. It runs
in the workgroup ATHENA_WORKGROUP, in DEFAULT_REGION (utils/agent_vars.py).

run_athena_query logs each query whole, on one line: its long region IN lists
would push the filters past any cut. What a query scanned goes on the question's
receipt. A query stopped by the workgroup's per-query scan cap
(terraform/athena.tf) gets a hint to narrow it. Rows that carry their CloudTrail
source file get a provenance label, set in code (cloudtrail_provenance.py).
"""

import time
from collections import Counter

import boto3
from strands import tool

from utils.untrusted_text import one_line
import utils.agent_vars as vars
import utils.request_context as request_context
import tools.cloudtrail_provenance as cloudtrail_provenance


def format_athena_rows(rows: list[dict]) -> str:
    """Format Athena query results into human-readable text.

    Each value is put on one line (one_line), so a value can't start a line of this layout. A list that
    SQLRewriteHook cut ends with a mark that says how many rows it kept (agent_wrapper); the header then says
    that more rows matched. A wrapper may leave rows out after the cut (CloudWatch's
    empty_note.without_accounts), and then the header says how many are shown. More than GROUPED_ROW_LIMIT rows
    can come only from a GROUP BY query, for which SQLRewriteHook asks for one row more than the limit: the extra
    row means there were more groups than are shown, and it is left out.

    Args:
        rows: List of row dictionaries from run_athena_query.
              Each dict has column names as keys, values as strings.
              A last element {vars.MORE_ROWS_KEY: n} is the mark of a cut list, not a row.

    Returns:
        Formatted string with row count header, numbered rows,
        and all key-value pairs. Returns "No results found." for empty input
        (no rows and no mark) or for a value that is not a list.
    """
    more = None
    if isinstance(rows, list) and rows and isinstance(rows[-1], dict) and set(rows[-1]) == {vars.MORE_ROWS_KEY}:
        more, rows = rows[-1][vars.MORE_ROWS_KEY], rows[:-1]

    if not isinstance(rows, list) or (len(rows) == 0 and not more):
        return "No results found."

    lines = [f"Results: {len(rows)} rows returned."]
    if more:
        lines = [f"Results: more than {more} rows; only the first {more} are shown, so these are not all the rows."
                 if len(rows) == more else
                 f"Results: {len(rows)} rows shown; more than {more} rows matched the query, so these are not all "
                 f"the rows."]
    if len(rows) > vars.GROUPED_ROW_LIMIT:
        rows = rows[:vars.GROUPED_ROW_LIMIT]
        lines = [f"Results: more than {vars.GROUPED_ROW_LIMIT} rows; only the first {vars.GROUPED_ROW_LIMIT} are "
                 f"shown, so these are not all the groups."]
    for i, row in enumerate(rows, start=1):
        lines.append("")
        lines.append(f"Row {i}:")
        for key, value in row.items():
            lines.append(f"  {key}: {one_line(value)}")

    return "\n".join(lines)


@tool
def run_athena_query(sql: str, timeout_seconds: int = 60) -> list[dict]:
    """
    Execute a SQL query against lttm-athena-workgroup and return results
    as a list of dicts keyed by column name.

    Args:
        sql: The SQL query string to execute.
        timeout_seconds: Maximum seconds to wait for query completion (default 60).

    Returns:
        list[dict]: Each dict is one result row, keyed by column name.
                    Returns [] if the query succeeds but returns no rows.

    Raises:
        RuntimeError: if the query reaches FAILED or CANCELLED status.
        TimeoutError: if the query does not complete within timeout_seconds.
    """
    from utils.sse_emitter import emit_status

    athena = boto3.client("athena", region_name=vars.DEFAULT_REGION)

    print(f"[LTTM:Athena] Executing query (timeout={timeout_seconds}s): {' '.join(sql.split())}", flush=True)
    response = athena.start_query_execution(
        QueryString=sql,
        WorkGroup=vars.ATHENA_WORKGROUP,
    )
    execution_id = response["QueryExecutionId"]
    emit_status(f"Athena query executing (QueryExecutionId: {execution_id})", source="athena")
    print(f"[LTTM:Athena] QueryExecutionId: {execution_id}", flush=True)

    deadline = time.monotonic() + timeout_seconds
    while True:
        if time.monotonic() >= deadline:
            athena.stop_query_execution(QueryExecutionId=execution_id)
            raise TimeoutError(
                f"Query {execution_id} did not complete within {timeout_seconds}s"
            )

        status_response = athena.get_query_execution(QueryExecutionId=execution_id)
        state = status_response["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            scanned = status_response["QueryExecution"].get("Statistics", {}).get("DataScannedInBytes", 0)
            request_context.current().receipt.add_athena(scanned)

        if state == "SUCCEEDED":
            print(f"[LTTM:Athena] Query {execution_id} SUCCEEDED — scanned {scanned} bytes", flush=True)
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = (
                status_response["QueryExecution"]["Status"]
                .get("StateChangeReason", "no reason provided")
            )
            emit_status(f"Athena query error: {reason}", source="athena")
            if "scanned" in reason.lower() and "limit" in reason.lower():
                reason += (" — the query would scan more data than one query may; narrow it "
                           "(fewer days, months, accounts or log groups) and retry")
            raise RuntimeError(f"Query {execution_id} failed: {reason}")
        time.sleep(1)

    results_response = athena.get_query_results(QueryExecutionId=execution_id)
    rows = results_response["ResultSet"]["Rows"]

    if not rows:
        return []

    header = [col["VarCharValue"] for col in rows[0]["Data"]]
    result = []
    for row in rows[1:]:
        values = [cell.get("VarCharValue", "") for cell in row["Data"]]
        result.append(dict(zip(header, values)))

    if result and cloudtrail_provenance.SOURCE_COLUMN in result[0]:
        result = cloudtrail_provenance.label_rows(result)
        print(f"[LTTM:Provenance] {sorted(Counter(row['provenance'] for row in result).items())}", flush=True)

    print(f"[LTTM:Athena] Query {execution_id} returned {len(result)} rows", flush=True)
    emit_status(f"Athena query complete — {len(result)} rows returned", source="athena")
    return result
