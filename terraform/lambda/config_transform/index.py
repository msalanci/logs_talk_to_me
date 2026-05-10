"""
Transformation for AWS Config EventBridge events.

Connects to:
  - aws_kinesis_firehose_delivery_stream.config_* (config_pipeline.tf) — invokes this Lambda as the
    record-transformation processor before writing to the data lake
  - aws_glue_catalog_table.config_logs (athena.tf) — reads the transformed newline-delimited JSON output
  - arn:aws:s3:::lttm-datalake/config/ — where Firehose writes Ok records
  - arn:aws:s3:::lttm-datalake/config-errors/ — where Firehose routes
    ProcessingFailed records
"""

import base64
import json


def handler(event, context):
    """
    Processes each base64-encoded record in the Firehose batch: decodes, extracts `detail.configurationItem` 
    from the EventBridge envelope, lowercases all top-level keys, re-encodes as a single JSON line with a 
    trailing newline, and returns the result to Firehose. 
    Records missing configurationItem or failing JSON parse are marked ProcessingFailed so Firehose routes 
    them to the S3 error prefix.

    Args:
        event: Firehose transformation event with a "records" list; each record carries a "recordId" 
               and base64-encoded "data"
        context: Lambda context (unused)

    Returns:
        dict: {"records": [...]} where each entry has recordId, result ("Ok" or "ProcessingFailed")
              and data (base64-encoded transformed payload, or the original data on failure)
    """
    
    output = []

    for record in event["records"]:
        record_id = record["recordId"]

        try:
            payload = base64.b64decode(record["data"]).decode("utf-8")
            event_data = json.loads(payload)

            config_item = event_data.get("detail", {}).get("configurationItem", {})

            if not config_item:
                output.append({
                    "recordId": record_id,
                    "result": "ProcessingFailed",
                    "data": record["data"],
                })
                continue

            normalized = {k.lower(): v for k, v in config_item.items()}
            result_data = json.dumps(normalized, separators=(",", ":")) + "\n"

            output.append({
                "recordId": record_id,
                "result": "Ok",
                "data": base64.b64encode(result_data.encode("utf-8")).decode("utf-8"),
            })

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            output.append({
                "recordId": record_id,
                "result": "ProcessingFailed",
                "data": record["data"],
            })

    return {"records": output}
