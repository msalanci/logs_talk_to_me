"""
Transforms AWS Config snapshot files into the LTTM data lake format.

Connects to:
  - aws_s3_bucket.config_bucket / notification (config_pipeline.tf) —triggers this Lambda on snapshot PUT
  - arn:aws:s3:::lttm-datalake/config-snapshot/ — destination prefix
  - aws_glue_catalog_table.config_snapshot (athena.tf) — reads the output
  - config_transform/ (the other Lambda) — handles the streaming path for non-snapshot configuration items
"""

import json
import gzip
import os
import boto3
from datetime import datetime

s3 = boto3.client("s3")

DEST_BUCKET = os.environ.get("DEST_BUCKET", "lttm-datalake")
DEST_PREFIX = "config-snapshot"

def handler(event, context):
    """
    Transform an AWS Config snapshot file into partitioned NDJSON in the data lake.

    Triggered by S3 PUT events when AWS Config delivers a snapshot file. 
    For each record in the event: downloads the snapshot JSON (gzip-aware), extracts configurationItems, lowercases 
    all field names, groups items by AWS account ID, and writes one newline-delimited JSON file per account under: 
    s3://{DEST_BUCKET}/{DEST_PREFIX}/account_id={id}/year=YYYY/month=MM/day=DD/snapshot-{request_id}.json

    Args:
        event: S3 event notification with one or more Records referencing snapshot objects.
        context: Lambda context; aws_request_id is used to make each output key unique.

    Returns:
        dict: {"statusCode": 200} on success.

    Raises:
        Exception: re-raises any S3 or JSON parsing error so Lambda marks the invocation
            as failed and S3 retries per the notification configuration.
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

            now = datetime.utcnow()
            snapshot_year = now.strftime("%Y")
            snapshot_month = now.strftime("%m")
            snapshot_day = now.strftime("%d")

            groups = {}
            for item in items:
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

            print(f"[CONFIG-SNAPSHOT] Total: {total_written} items from {source_key}")

        except Exception as e:
            print(f"[CONFIG-SNAPSHOT] ERROR processing {source_key}: {e}")
            raise

    return {"statusCode": 200}
