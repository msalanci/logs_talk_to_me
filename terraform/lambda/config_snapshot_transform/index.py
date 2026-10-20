# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
CONFIG-SNAPSHOT: Lambda that transforms AWS Config snapshot files into the
LTTM data lake format.

Triggered by S3 PUT events when Config delivers a snapshot file under
AWSLogs/{account}/Config/{region}/..., in Config's own JSON layout, which the
Glue table can't read. Reads the snapshot JSON, extracts each configuration
item, normalizes field names to lowercase, and writes them as newline-delimited
JSON to config-snapshot/account_id={id}/year={y}/month={m}/day={d}/ in the
data lake (DEST_BUCKET, from the environment), so Athena can query them
through the config_snapshot Glue table.

Config delivers one snapshot per channel a day, at 00:01 UTC
(var.config_snapshot_schedule, terraform/config_snapshot.tf); this Lambda files
it under the day it describes (snapshot_date).

A row equals AWS Config's record: Config's two times, configurationItemCaptureTime and
resourceCreationTime (only when Config has one), are written as UTC YYYY-MM-DDTHH:MM:SS.mmmZ, the same instant
in one form, as the Config stream's Lambda does (terraform/lambda/config_transform). A record without a readable
capture time, or with an unreadable creation time, is left out of the table and written to
config-errors/snapshot-unreadable/, where alerts.tf emails.
"""

import json
import gzip
import os
import re
import boto3
from datetime import datetime, timedelta, timezone

s3 = boto3.client("s3")

DEST_BUCKET = os.environ.get("DEST_BUCKET", "lttm-datalake")
DEST_PREFIX = "config-snapshot"
UNREADABLE_PREFIX = "config-errors/snapshot-unreadable"
TIME_FIELDS = ("configurationItemCaptureTime", "resourceCreationTime")


def canonical_time(value: str) -> str:
    """An ISO 8601 time with Z or any offset, any fraction, as UTC YYYY-MM-DDTHH:MM:SS.mmmZ (the same instant).

    The same as config_transform's canonical_time.

    Args:
        value: A time as Config writes it.

    Returns:
        The time in the canonical form.

    Raises:
        ValueError: It is not a time with a zone.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"not a time: {value!r}")
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"a time without a zone: {value!r}")
    t = t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def canonical_item(item: dict) -> dict:
    """Config's snapshot record with its times in the canonical form.

    Args:
        item: One configurationItem of the snapshot.

    Returns:
        A copy of the record, its TIME_FIELDS in the canonical form (an empty creation time stays as it is).

    Raises:
        ValueError: The capture time is missing, or a time is unreadable.
    """
    item = dict(item)
    if not item.get("configurationItemCaptureTime"):
        raise ValueError("no capture time")
    for field in TIME_FIELDS:
        if item.get(field) not in (None, ""):
            item[field] = canonical_time(item[field])
    return item
DAY_BEFORE_UNTIL_HOUR = 2


def snapshot_date(now: datetime) -> datetime:
    """The day a snapshot delivered at `now` (UTC) is filed under: the day before until 02:00 UTC.

    The daily snapshot is delivered at 00:01 UTC and holds the end-of-day state of the day before. A delivery that
    arrives before DAY_BEFORE_UNTIL_HOUR (UTC) is filed under the day before; any later one under its own day.

    Args:
        now: The delivery time, in UTC.

    Returns:
        The day to file the snapshot under, as a datetime.
    """
    return now - timedelta(days=1) if now.hour < DAY_BEFORE_UNTIL_HOUR else now


_SNAPSHOT_TIME = re.compile(r"_ConfigSnapshot_(\d{8}T\d{6}Z)_")


def snapshot_time(key: str) -> datetime | None:
    """The delivery time in a snapshot file's name. Dating by the file's own time, not by when this
    Lambda runs, keeps a copied or re-processed snapshot on its real day.

    Config names each snapshot file with its delivery time (UTC):
    <account>_Config_<region>_ConfigSnapshot_20261010T000102Z_<uuid>.json.gz

    Args:
        key: The snapshot file's S3 key.

    Returns:
        The delivery time (naive, in UTC); None when the name has none.
    """
    m = _SNAPSHOT_TIME.search(key.rsplit("/", 1)[-1])
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ") if m else None


def handler(event, context):
    """S3 event handler for Config snapshot files.

    Only ConfigSnapshot files are processed; ConfigHistory and other files are skipped. The items are partitioned
    by the snapshot's day, not by each item's configurationitemcapturetime: a snapshot is a point-in-time inventory
    of all resources, so an S3 bucket created in 2024 that hasn't changed still appears in every snapshot, and by
    capture time the items would scatter across years of partitions. The snapshot's day is its delivery day, taken
    from the file's own name (snapshot_time), or the day before for the 00:01 UTC delivery (snapshot_date); the
    agent reads each account's newest day. Items without a readable time are left out of the table and written to
    UNREADABLE_PREFIX, whose error folder makes alerts.tf email.

    Args:
        event: The S3 event, with one record per delivered file.
        context: The Lambda context; its aws_request_id names the files written.

    Returns:
        {"statusCode": 200}. Writes one newline-delimited JSON file per account to the lake, and one error file
        when items had no readable time.

    Raises:
        Exception: Any error reading or writing a file is logged and raised, so Lambda reports the failure.
    """
    for record in event.get("Records", []):
        source_bucket = record["s3"]["bucket"]["name"]
        source_key = record["s3"]["object"]["key"]

        if "ConfigSnapshot" not in source_key:
            print(f"[CONFIG-SNAPSHOT] Skipping non-snapshot file: {source_key}")
            continue

        print(f"[CONFIG-SNAPSHOT] Processing: s3://{source_bucket}/{source_key}")

        try:
            response = s3.get_object(Bucket=source_bucket, Key=source_key)
            body = response["Body"].read()

            if source_key.endswith(".gz"):
                body = gzip.decompress(body)

            data = json.loads(body.decode("utf-8"))

            items = data.get("configurationItems", [])
            if not items:
                print(f"[CONFIG-SNAPSHOT] No configurationItems found in {source_key}")
                continue

            delivered = snapshot_time(source_key)
            if delivered is None:
                print(f"[CONFIG-SNAPSHOT] No delivery time in the file name, dated by processing time: {source_key}")
                delivered = datetime.utcnow()
            now = snapshot_date(delivered)
            snapshot_year = now.strftime("%Y")
            snapshot_month = now.strftime("%m")
            snapshot_day = now.strftime("%d")

            groups = {}
            unreadable = []
            for item in items:
                try:
                    item = canonical_item(item)
                except ValueError as e:
                    unreadable.append({"error": str(e), "configurationItem": item})
                    continue
                normalized = {k.lower(): v for k, v in item.items()}

                account_id = normalized.get("awsaccountid", "unknown")

                group_key = account_id
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append(normalized)

            total_written = 0
            for account_id, group_items in groups.items():
                dest_key = (
                    f"{DEST_PREFIX}/account_id={account_id}/year={snapshot_year}/"
                    f"month={snapshot_month}/day={snapshot_day}/snapshot-{context.aws_request_id}.json"
                )

                lines = []
                for item in group_items:
                    lines.append(json.dumps(item, separators=(",", ":")))
                body_str = "\n".join(lines) + "\n"

                s3.put_object(
                    Bucket=DEST_BUCKET,
                    Key=dest_key,
                    Body=body_str.encode("utf-8"),
                    ContentType="application/json",
                )
                total_written += len(group_items)
                print(f"[CONFIG-SNAPSHOT] Wrote {len(group_items)} items to s3://{DEST_BUCKET}/{dest_key}")

            if unreadable:
                error_key = f"{UNREADABLE_PREFIX}/{source_key.rsplit('/', 1)[-1].removesuffix('.gz')}"
                s3.put_object(
                    Bucket=DEST_BUCKET,
                    Key=error_key,
                    Body=("\n".join(json.dumps(u, separators=(",", ":")) for u in unreadable) + "\n").encode("utf-8"),
                    ContentType="application/json",
                )
                print(f"[CONFIG-SNAPSHOT] {len(unreadable)} items without a readable time written to s3://{DEST_BUCKET}/{error_key}")

            print(f"[CONFIG-SNAPSHOT] Total: {total_written} items from {source_key}")

        except Exception as e:
            print(f"[CONFIG-SNAPSHOT] ERROR processing {source_key}: {e}")
            raise

    return {"statusCode": 200}
