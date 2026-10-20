# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
lake_check — a daily check of the lake's new rows.

Every day (lake_check.tf, 02:30 UTC) it checks what is filed under the day before.
  With Athena:
  - config_logs: every capture time in the form UTC YYYY-MM-DDTHH:MM:SS.mmmZ and filed under its own UTC day,
    every creation time (when there is one) in that form, the account ID present and equal to the folder's,
    no item (account, type, ID, configurationStateId) twice;
  - config_snapshot (the snapshot of that day): the same time and account checks;
  - cloudtrail_logs, guardduty_findings: every time in UTC (Z).
  With S3, Config's snapshot deliveries against config_snapshot:
  - every snapshot file Config delivered for the day has its table file, with the same records;
  - every table file of the day has its delivered file (none processed twice, none changed);
  - every delivery channel (SNAPSHOT_CHANNELS) delivered a snapshot for the day.
Any finding, or a check that could not run, is emailed through the lake alarm topic (alerts.tf). A clean day
sends nothing.

A record in the lake equals AWS Config's record, its times in UTC. The Config Lambdas guarantee it on the way in;
this check catches what slips past them (a new writer, a new source) on its first day, where local times written
by a backfill could otherwise go unnoticed for months. A snapshot that never reaches the snapshot Lambda leaves no
trace in the table, and a channel that stops delivering freezes the inventory without an error.

One Athena query per check, all started at once, each returning a count and one example; the snapshot
deliveries are read from S3 and matched to the day's table files by their records. A manual run can name the
day: {"day": "2026-10-09"}.

It reads DATABASE, WORKGROUP, TOPIC_ARN, BUCKET and SNAPSHOT_CHANNELS from the environment. SNAPSHOT_CHANNELS
lists Config's snapshot delivery channels as "account/region", separated by commas; lake_check.tf builds it from
config_snapshot.tf's channels.
"""

import datetime as dt
import gzip
import json
import os
import re
import time

import boto3

DATABASE = os.environ.get("DATABASE", "lttm_logs")
WORKGROUP = os.environ.get("WORKGROUP", "lttm-athena-workgroup")
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
BUCKET = os.environ.get("BUCKET", "lttm-datalake")
SNAPSHOT_CHANNELS = [c for c in os.environ.get("SNAPSHOT_CHANNELS", "").split(",") if c]
CANONICAL = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
UTC_Z = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
QUERY_TIMEOUT_S = 240

athena = boto3.client("athena")
sns = boto3.client("sns")
s3 = boto3.client("s3")


def checks(day: dt.date) -> list[tuple[str, str]]:
    """(what is wrong, the SQL that counts it and returns one example) for the rows filed under `day`.

    CANONICAL is the form of Config's times, as the Config Lambdas write them; UTC_Z is any UTC time (CloudTrail's
    have no fraction).

    Args:
        day: The day the rows are filed under.

    Returns:
        One (description, SQL) per check, each SQL returning n and example.
    """
    part = f"year = '{day:%Y}' AND month = '{day:%m}' AND day = '{day:%d}'"
    db = DATABASE

    def count(table, condition, example='"$path"'):
        """The SQL that counts a table's rows of the day that meet a condition, with one example.

        Args:
            table: The table, in DATABASE.
            condition: What is wrong, as SQL.
            example: The column the example is taken from; the file's path by default.

        Returns:
            The SELECT.
        """
        return f"SELECT count(*) AS n, arbitrary({example}) AS example FROM {db}.{table} WHERE {part} AND ({condition})"

    def bad_time(column, pattern, may_be_empty=False):
        """The SQL condition for a time column that does not match a pattern.

        Args:
            column: The time column.
            pattern: The regular expression a good time matches.
            may_be_empty: True when NULL is allowed (only a time that is there must match).

        Returns:
            The condition.
        """
        test = f"NOT regexp_like({column}, '{pattern}')"
        return f"{column} IS NOT NULL AND {test}" if may_be_empty else f"NOT regexp_like(coalesce({column}, ''), '{pattern}')"

    return [
        ("config_logs: capture time not UTC YYYY-MM-DDTHH:MM:SS.mmmZ", count("config_logs", bad_time("configurationitemcapturetime", CANONICAL))),
        ("config_logs: creation time not UTC YYYY-MM-DDTHH:MM:SS.mmmZ", count("config_logs", bad_time("resourcecreationtime", CANONICAL, True))),
        ("config_logs: filed under another day than its UTC capture day",
         count("config_logs", f"substr(configurationitemcapturetime, 1, 10) <> '{day:%Y-%m-%d}'")),
        ("config_logs: account ID missing or not the folder's", count("config_logs", "coalesce(awsaccountid, '') <> account_id")),
        ("config_logs: the same item more than once",
         f"SELECT count(*) AS n, arbitrary(k) AS example FROM (SELECT account_id || ' ' || resourcetype || ' ' || resourceid || ' ' || "
         f"split_part(configurationstateid, '.', 1) AS k FROM {db}.config_logs WHERE {part} GROUP BY 1 HAVING count(*) > 1)"),
        ("config_snapshot: capture time not UTC YYYY-MM-DDTHH:MM:SS.mmmZ", count("config_snapshot", bad_time("configurationitemcapturetime", CANONICAL))),
        ("config_snapshot: creation time not UTC YYYY-MM-DDTHH:MM:SS.mmmZ", count("config_snapshot", bad_time("resourcecreationtime", CANONICAL, True))),
        ("config_snapshot: account ID missing or not the folder's", count("config_snapshot", "coalesce(awsaccountid, '') <> account_id")),
        ("cloudtrail_logs: event time not in UTC", count("cloudtrail_logs", bad_time("eventtime", UTC_Z))),
        ("guardduty_findings: time not in UTC", count("guardduty_findings", bad_time("time", UTC_Z))),
    ]


def run(queries: list[tuple[str, str]]) -> list[dict]:
    """Start every query, and wait for all.

    A check that cannot start is reported, not skipped. Each query has until QUERY_TIMEOUT_S from the start.

    Args:
        queries: (description, SQL) per check, as checks returns them.

    Returns:
        One {"check", "n", "example"} or {"check", "error"} per query, in order.
    """
    started = []
    for what, sql in queries:
        try:
            qid = athena.start_query_execution(QueryString=sql, WorkGroup=WORKGROUP)["QueryExecutionId"]
            started.append((what, qid, None))
        except Exception as e:  # noqa: BLE001 — a check that cannot start is reported, not skipped
            started.append((what, None, f"{type(e).__name__}: {e}"))
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    results = []
    for what, qid, error in started:
        if error:
            results.append({"check": what, "error": error})
            continue
        while True:
            status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED") or time.monotonic() > deadline:
                break
            time.sleep(2)
        if status["State"] != "SUCCEEDED":
            results.append({"check": what, "error": f"{status['State']}: {status.get('StateChangeReason', 'timed out')}"})
            continue
        rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
        values = [c.get("VarCharValue") for c in rows[1]["Data"]] if len(rows) > 1 else ["0", None]
        results.append({"check": what, "n": int(values[0] or 0), "example": values[1]})
    return results


DAY_BEFORE_UNTIL_HOUR = 2
SNAPSHOT_TIME = re.compile(r"_ConfigSnapshot_(\d{8}T\d{6}Z)_")
SNAPSHOT_TIMES = ("configurationitemcapturetime", "resourcecreationtime")
DELIVERY_CHECKS = ("config_snapshot: a snapshot Config delivered is not in the table",
                   "config_snapshot: a table file matches no delivered snapshot (processed twice, or changed)",
                   "config_snapshot: a delivery channel delivered no snapshot")


def snapshot_day(key: str) -> dt.date | None:
    """The day of config-snapshot/ that a delivered file is filed under, from the delivery time in its name.

    The same rule as the snapshot Lambda's (config_snapshot_transform), which writes each delivered file as one
    table file: before 02:00 UTC (DAY_BEFORE_UNTIL_HOUR) under the day before, for the 00:01 UTC daily snapshot;
    later under its own day.

    Args:
        key: The delivered file's S3 key.

    Returns:
        The day; None when the name has no delivery time.
    """
    m = SNAPSHOT_TIME.search(key.rsplit("/", 1)[-1])
    if not m:
        return None
    t = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ")
    return (t - dt.timedelta(days=1)).date() if t.hour < DAY_BEFORE_UNTIL_HOUR else t.date()


def delivery_folders(account: str, region: str, day: dt.date) -> list[str]:
    """Config's folders that can hold the deliveries filed under `day`: that day's and the next. Config's date path
    has no zero padding (2026/4/9).

    Args:
        account: The account ID.
        region: The region.
        day: The day the deliveries are filed under.

    Returns:
        The two S3 prefixes.
    """
    return [f"AWSLogs/{account}/Config/{region}/{d.year}/{d.month}/{d.day}/ConfigSnapshot/" for d in (day, day + dt.timedelta(days=1))]


def instant(value):
    """A time as a UTC instant with milliseconds, for comparing.

    Args:
        value: A time as Config or the snapshot Lambda writes it.

    Returns:
        The instant in ISO 8601; an unreadable value as it is, so it is compared as it is (the snapshot Lambda
        leaves such a record out).
    """
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")
    except (AttributeError, ValueError):
        return value


def signature(records: list[dict]) -> tuple:
    """A file's records, order-free, keys lowercased, the two times as instants: the same for a delivered file and
    the table file the snapshot Lambda writes from it.

    Args:
        records: The file's records.

    Returns:
        The records as sorted JSON strings.
    """
    out = []
    for r in records:
        r = {k.lower(): v for k, v in r.items()}
        for f in SNAPSHOT_TIMES:
            if r.get(f):
                r[f] = instant(r[f])
        out.append(json.dumps(r, sort_keys=True))
    return tuple(sorted(out))


def unmatched(deliveries: list[tuple[str, tuple]], table_files: list[tuple[str, tuple]]) -> tuple[list[str], list[str]]:
    """Match one account's deliveries and table files of one day, as (key, signature).

    One table file answers one delivery, the newest delivery first.

    Args:
        deliveries: The delivered files with records, as (key, signature).
        table_files: The day's table files, as (key, signature).

    Returns:
        (deliveries without a table file, table files without a delivery), as keys.
    """
    left, missing = list(table_files), []
    for key, sig in sorted(deliveries, key=lambda d: SNAPSHOT_TIME.search(d[0]).group(1), reverse=True):
        match = next((i for i, (_, s) in enumerate(left) if s == sig), None)
        if match is None:
            missing.append(key)
        else:
            left.pop(match)
    return missing, [key for key, _ in left]


def _keys(prefix: str) -> list[str]:
    """List every key under a prefix of the lake bucket.

    Args:
        prefix: The S3 prefix.

    Returns:
        The keys, all pages followed; [] when there are none.
    """
    return [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
            for o in page.get("Contents", [])]


def _regions(account: str) -> list[str]:
    """List the regions Config delivers to the lake for an account.

    Args:
        account: The account ID.

    Returns:
        The region folders under AWSLogs/<account>/Config/.
    """
    return [p["Prefix"].split("/")[-2] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=f"AWSLogs/{account}/Config/", Delimiter="/") for p in page.get("CommonPrefixes", [])]


def _read(key: str) -> bytes:
    """Read a file of the lake bucket, decompressed when it is gzip.

    Args:
        key: The S3 key.

    Returns:
        The file's bytes.
    """
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return gzip.decompress(body) if key.endswith(".gz") else body


def delivery_checks(day: dt.date) -> list[dict]:
    """Config's snapshot deliveries filed under `day` against the day's table files, and every channel delivered.

    A delivery without records gets no table file, so it is left out. Without SNAPSHOT_CHANNELS every check would
    pass on an empty lake, so all three report an error instead. A check that cannot run is reported, not skipped.

    Args:
        day: The day the deliveries are filed under.

    Returns:
        Three results, in run()'s form, each with its unit.
    """
    if not SNAPSHOT_CHANNELS:
        return [{"check": what, "error": "no delivery channels given (SNAPSHOT_CHANNELS)"} for what in DELIVERY_CHECKS]
    try:
        missing, extra, silent = [], [], []
        for account in sorted({c.split("/")[0] for c in SNAPSHOT_CHANNELS}):
            deliveries, delivered_from = [], set()
            for region in _regions(account):
                for prefix in delivery_folders(account, region, day):
                    for key in _keys(prefix):
                        if snapshot_day(key) != day:
                            continue
                        delivered_from.add(region)
                        records = json.loads(_read(key)).get("configurationItems") or []
                        if records:
                            deliveries.append((key, signature(records)))
            table = []
            for key in _keys(f"config-snapshot/account_id={account}/year={day:%Y}/month={day:%m}/day={day:%d}/"):
                table.append((key, signature([json.loads(line) for line in _read(key).decode().splitlines() if line.strip()])))
            m, x = unmatched(deliveries, table)
            missing += m
            extra += x
            silent += [c for c in SNAPSHOT_CHANNELS if c.split("/")[0] == account and c.split("/")[1] not in delivered_from]
    except Exception as e:  # noqa: BLE001 — a check that cannot run is reported, not skipped
        return [{"check": what, "error": f"{type(e).__name__}: {e}"} for what in DELIVERY_CHECKS]
    found = (missing, extra, silent)
    units = ("file(s)", "file(s)", "channel(s)")
    return [{"check": what, "n": len(f), "example": ", ".join(f[:3]) or None, "unit": unit}
            for what, f, unit in zip(DELIVERY_CHECKS, found, units)]


def report(day: dt.date, results: list[dict]) -> tuple[str, str] | None:
    """(subject, message) when anything is wrong or could not be checked; None for a clean day.

    Args:
        day: The day checked.
        results: The results of run and delivery_checks.

    Returns:
        The email's subject (at most 100 characters) and message; None when every check is clean.
    """
    bad = [r for r in results if r.get("error") or r.get("n")]
    if not bad:
        return None
    lines = [f"LTTM lake check of the rows filed under {day:%Y-%m-%d}: {len(bad)} of {len(results)} checks need a look.", ""]
    for r in bad:
        if r.get("error"):
            lines.append(f"- {r['check']}: COULD NOT RUN ({r['error']})")
        else:
            lines.append(f"- {r['check']}: {r['n']} {r.get('unit', 'row(s)')}; e.g. {r['example']}")
    lines += ["", "The rule: a record in the lake equals AWS Config's record, its times in UTC, and every snapshot Config "
                  "delivers is in the table."]
    return f"LTTM lake check {day:%Y-%m-%d}: {len(bad)} problem(s)"[:100], "\n".join(lines)


def handler(event, context):
    """Check the day before, or the day a manual run names, and email what is wrong.

    Args:
        event: The schedule's event, or {"day": "YYYY-MM-DD"} for a manual run.
        context: The Lambda context; not read.

    Returns:
        {"day", "results", "emailed"}. Publishes to TOPIC_ARN when a check found something or could not run.
    """
    day = dt.date.fromisoformat(event["day"]) if (event or {}).get("day") else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    results = run(checks(day)) + delivery_checks(day)
    found = report(day, results)
    if found:
        sns.publish(TopicArn=TOPIC_ARN, Subject=found[0], Message=found[1])
        print(f"[LAKE-CHECK] {found[0]}\n{found[1]}")
    else:
        print(f"[LAKE-CHECK] {day:%Y-%m-%d}: all {len(results)} checks clean")
    return {"day": f"{day:%Y-%m-%d}", "results": results, "emailed": bool(found)}
