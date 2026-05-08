# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
athena_tool.py — shared Strands @tool for executing Athena queries.

Registered on six Athena-based sub-agents (cloudtrail, cloudwatch, config, cur, flowlogs, guardduty) as @tool run_athena_query. 
Exports`format_athena_rows`, a plain helper those agents use to turn row lists into human-readable text blocks for the supervisor.

Single boto3 entry point — all SQL flows through here so `SQLValidatorHook` and `SQLRewriteHook` have one tool name (`run_athena_query`) to intercept.
"""

import time
import boto3
from strands import tool


def format_athena_rows(rows: list[dict]) -> str:
    """
    Format Athena query results into human-readable text.

    Args:
        rows: List of row dictionaries from run_athena_query, wher each dict has column names as keys, values as strings.

    Returns:
        Formatted string with row count header, numbered rows, and all key-value pairs. Returns "No results found." for empty input.
    """
    if not isinstance(rows, list) or len(rows) == 0:
        return "No results found."

    lines = [f"Results: {len(rows)} rows returned."]
    for i, row in enumerate(rows, start=1):
        lines.append("")
        lines.append(f"Row {i}:")
        for key, value in row.items():
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)


@tool
def run_athena_query(sql: str, timeout_seconds: int = 60) -> list[dict]:
    """
    Execute a SQL query against lttm-athena-workgroup and return results as a list of dicts keyed by column name.

    Args:
        sql:  The SQL query string to execute.
        timeout_seconds: Maximum seconds to wait for query completion (default 60).

    Returns:
        list[dict]: Each dict is one result row, keyed by column name.
                    Returns [] if the query succeeds but returns no rows.

    Raises:
        RuntimeError: if the query reaches FAILED or CANCELLED status.
        TimeoutError: if the query does not complete within timeout_seconds.
    """
    # Server-Sent Events (SSE) real-time status — Requirements 2.2, 2.3, 2.4
    from utils.sse_emitter import emit_status

    athena = boto3.client("athena", region_name="eu-central-1")

    print(f"[LTTM:Athena] Executing query (timeout={timeout_seconds}s): {sql[:300]}", flush=True)
    response = athena.start_query_execution(
        QueryString=sql,
        WorkGroup="lttm-athena-workgroup",
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

        if state == "SUCCEEDED":
            print(f"[LTTM:Athena] Query {execution_id} SUCCEEDED", flush=True)
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = (
                status_response["QueryExecution"]["Status"]
                .get("StateChangeReason", "no reason provided")
            )
            emit_status(f"Athena query error: {reason}", source="athena")
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

    print(f"[LTTM:Athena] Query {execution_id} returned {len(result)} rows", flush=True)
    emit_status(f"Athena query complete — {len(result)} rows returned", source="athena")
    return result
