# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
Firehose Lambda transformation for AWS Config EventBridge events.

Firehose invokes it for each batch of records of the Config stream; each record is a base64-encoded EventBridge
event. EventBridge wraps Config configuration items in an envelope with version, id, detail-type, source,
account, time, region, and detail fields, while the Glue table expects flat configuration item records with
lowercase field names: without this transformation, Athena queries would need json_extract() for every field.
So the Lambda extracts detail.configurationItem, lowercases its top-level keys, writes Config's times in one
form, and returns it as a single JSON line with a newline delimiter. It reads LAKE_BUCKET from the environment.

A record in the lake equals AWS Config's record:
  - Config's two times, configurationItemCaptureTime and resourceCreationTime (only when Config has one), are
    written as UTC YYYY-MM-DDTHH:MM:SS.mmmZ: the same instant as Config's, in one form, so text order is time
    order. A time is never added or dropped.
  - A record whose capture time is missing or unreadable, or whose creation time is unreadable, is
    ProcessingFailed: Firehose writes it to config-errors/, and alerts.tf emails.
  - An oversized notification (a record over the 256 KB event limit: the event carries only a summary and the
    S3 location of the full notification, which Config delivers to the lake's AWSLogs/ prefix) is replaced by
    the full record from that file. Only files in the lake bucket are read.
Code that writes records into config/ outside Firehose runs them through this handler too.
"""

import base64
import gzip
import json
import os
from datetime import datetime, timezone

LAKE_BUCKET = os.environ.get("LAKE_BUCKET", "lttm-datalake")
TIME_FIELDS = ("configurationItemCaptureTime", "resourceCreationTime")
OVERSIZED = "OversizedConfigurationItemChangeNotification"

_s3 = None


def s3_client():
    """The S3 client, made on first use.

    boto3 is imported only when an oversized notification needs the file, so code that reuses this module without
    boto3 at hand can still transform records.

    Args:
        None.

    Returns:
        A boto3 S3 client, the same one on every later call.
    """
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3")
    return _s3


def canonical_time(value: str) -> str:
    """An ISO 8601 time with Z or any offset, any fraction, as UTC YYYY-MM-DDTHH:MM:SS.mmmZ (the same instant).

    The same as config_snapshot_transform's canonical_time.

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


def oversized_item(detail: dict) -> dict:
    """The full configuration item of an oversized notification, from the file Config delivered to the lake.

    Args:
        detail: The EventBridge event's detail, with its s3DeliverySummary.

    Returns:
        The configurationItem of the delivered file.

    Raises:
        ValueError: The file is outside the lake bucket, or holds no configurationItem.
    """
    location = (detail.get("s3DeliverySummary") or {}).get("s3BucketLocation") or ""
    bucket, _, key = location.partition("/")
    if bucket != LAKE_BUCKET or not key:
        raise ValueError(f"oversized notification outside the lake: {location!r}")
    body = s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".gz"):
        body = gzip.decompress(body)
    item = json.loads(body).get("configurationItem")
    if not item:
        raise ValueError(f"no configurationItem in {location}")
    return item


def config_item(event_data: dict) -> dict:
    """Config's record from one EventBridge event, its times in the canonical form.

    Args:
        event_data: The decoded EventBridge event.

    Returns:
        A copy of the configuration item, its TIME_FIELDS in the canonical form.

    Raises:
        ValueError: There is no configuration item, no capture time, or an unreadable time.
        Exception: Any error reading an oversized notification's file.
    """
    detail = event_data.get("detail") or {}
    item = detail.get("configurationItem")
    if not item and detail.get("messageType") == OVERSIZED:
        item = oversized_item(detail)
    if not item:
        raise ValueError("no configurationItem")
    item = dict(item)
    if not item.get("configurationItemCaptureTime"):
        raise ValueError("no capture time")
    for field in TIME_FIELDS:
        if item.get(field) not in (None, ""):
            item[field] = canonical_time(item[field])
    return item


def handler(event, context):
    """Firehose transformation handler.

    Processes a batch of records from Firehose. Each record contains a
    base64-encoded EventBridge event with a Config configuration item
    nested under detail.configurationItem. All top-level field names are
    lowercased: Config writes camelCase (resourceType, awsRegion), the Glue
    table has lowercase columns (resourcetype, awsregion). Each record ends
    with a newline: Firehose concatenates records in a single S3 object, and
    without newlines the JSON records would merge into an unparseable blob.
    For a ProcessingFailed record, Firehose writes the untouched record to
    config-errors/processing-failed/, and alerts.tf emails on every new file
    there.

    Args:
        event: The Firehose event, with its records.
        context: The Lambda context; not read.

    Returns:
        {"records": [...]}, one per record, with:
          - result: "Ok" for successfully transformed records
          - result: "ProcessingFailed" for records that cannot be parsed, have no
            readable capture time, or whose oversized file cannot be read
          - data: base64-encoded JSON line (flattened config item + newline),
            or the untouched record for a failed one
    """
    output = []

    for record in event["records"]:
        record_id = record["recordId"]

        try:
            payload = base64.b64decode(record["data"]).decode("utf-8")
            config_item_data = config_item(json.loads(payload))

            normalized = {k.lower(): v for k, v in config_item_data.items()}

            result_data = json.dumps(normalized, separators=(",", ":")) + "\n"

            output.append({
                "recordId": record_id,
                "result": "Ok",
                "data": base64.b64encode(result_data.encode("utf-8")).decode("utf-8"),
            })

        except Exception as e:  # noqa: BLE001 — any failure routes the record to the error prefix
            print(f"[CONFIG-TRANSFORM] ProcessingFailed: {type(e).__name__}: {e}")
            output.append({
                "recordId": record_id,
                "result": "ProcessingFailed",
                "data": record["data"],
            })

    return {"records": output}
