# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
health_tool.py — Strands @tool for querying AWS Health events.

Registered on the health_agent as @tool query_health_api.

Calls AWS Health boto3 APIs directly (DescribeEvents, DescribeEventDetails, DescribeAffectedEntities). 
No Athena/Glue/S3 infrastructure needed - pure API-based tool.

The Health API is a global service — all calls go to `us-east-1` regardless of where the agent runs.

Supports cross-account queries via STS AssumeRole into `LTTMHealthReadRole` for the account 2 and account 3 member accounts. 
Uses default execution-role credentials for the account 1 (main) account.

Requires Business or Enterprise Support plan. 
Accounts on Basic or Developer plans receive `SubscriptionRequiredException`, which is caught and returned as a user-readable string (not raised).
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool
from utils.sse_emitter import emit_status
import utils.agent_vars as _vars

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMHealthReadRole"
LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID


def _get_account_label(account_id: str) -> str:
    """Return human-readable label for an account ID."""
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_health_client(account_id: str, region: str = "us-east-1"):
    """
    Return a boto3 Health client for the given account.

    For the main account (matching `ACC1_ID`), returns a client built from the execution role's default credentials. 
    For any other account, calls STS AssumeRole against `LTTMHealthReadRole` in that account and returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. 
                    If it equals the main-account ID, no role assumption happens.
        region: AWS region for the client. 
                Defaults to `us-east-1` because the AWS Health API is a global service routed through `us-east-1`

    Returns: A boto3 `health` client scoped to `account_id`.
    """
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("health", region_name=region)

    role_arn = CROSS_ACCOUNT_ROLE_TEMPLATE.format(account_id=account_id)
    label = _get_account_label(account_id)
    print(f"[LTTM:Health] Assuming role {role_arn} for {label} account", flush=True)

    sts = boto3.client("sts")
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"lttm-health-{account_id}",
    )["Credentials"]

    return boto3.client(
        "health",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _build_event_filter(
    service: str,
    event_type_category: str,
    event_status: str,
    start_time: str,
    end_time: str,
) -> dict:
    """
    Build the filter dict for `describe_events`.

    Assembles the Health-API-specific filter shape. Note that time filters use wrapped lists of dicts (`startTimes`=[{"from": ...}],
    `endTimes`=[{"to": ...}]) — this is not a typo, it is the shape the Health API expects.

    Args:
        service: AWS service code filter (e.g. `EC2`, `LAMBDA`), empty string disables.
        event_type_category: Category filter — one of `issue`,`accountNotification`, `scheduledChange`, `investigation`, empty string disables.
        event_status: Status filter — one of `open`, `closed`, `upcoming`, empty string disables.
        start_time: Lower bound on event start, empty string disables.
        end_time: Upper bound on event end, empty string disables.

    Returns:
        A dict ready to pass as the `filter` argument to `describe_events`. Returns an empty dict `{}` when no filter conditions were provided.
        Callers treat that as "do not pass a filter at all."
    """
    f = {}
    if service:
        f["services"] = [service]
    if event_type_category:
        f["eventTypeCategories"] = [event_type_category]
    if event_status:
        f["eventStatusCodes"] = [event_status]
    if start_time:
        f["startTimes"] = [{"from": start_time}]
    if end_time:
        f["endTimes"] = [{"to": end_time}]
    return f


def _format_event(event: dict, account_id: str, description: str = "") -> str:
    """Format a single health event dict into a human-readable text block."""
    label = _get_account_label(account_id)
    service = event.get("service", "N/A")
    event_type = event.get("eventTypeCategory", "N/A")
    status = event.get("statusCode", "N/A")
    region = event.get("region", "N/A")
    az = event.get("availabilityZone", "")
    start = event.get("startTime", "N/A")
    end = event.get("endTime", "N/A")
    event_code = event.get("eventTypeCode", "N/A")

    lines = [
        "--- Health Event ---",
        f"Account: {account_id} ({label})",
        f"Service: {service}",
        f"Event Type: {event_type}",
        f"Event Code: {event_code}",
        f"Status: {status}",
        f"Region: {region}",
    ]
    if az:
        lines.append(f"AZ: {az}")
    lines.append(f"Start: {start}")
    lines.append(f"End: {end}")
    if description:
        lines.append(f"Description: {description}")

    return "\n".join(lines)


def _format_entity(entity: dict, account_id: str) -> str:
    """Format a single affected entity into a human-readable text block."""
    label = _get_account_label(account_id)
    return (
        f"--- Affected Entity ---\n"
        f"Account: {account_id} ({label})\n"
        f"Entity: {entity.get('entityValue', 'N/A')}\n"
        f"Status: {entity.get('statusCode', 'N/A')}\n"
        f"Last Updated: {entity.get('lastUpdatedTime', 'N/A')}"
    )


@tool
def query_health_api(
    account_id: str,
    action: str = "describe_events",
    region: str = "us-east-1",
    event_arn: str = "",
    service: str = "",
    event_type_category: str = "",
    event_status: str = "",
    start_time: str = "",
    end_time: str = "",
    max_results: int = 50,
) -> str:
    """
    Query AWS Health events for a specific AWS account.

    Calls the AWS Health boto3 API to retrieve service health events, event details, or affected entities. Handles cross-account access via STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — describe_events (default), describe_event_details, or describe_affected_entities.
        region: AWS region (default: us-east-1). Health API is global but routed via this region.
        event_arn: Event ARN (required for describe_event_details and describe_affected_entities).
        service: AWS service code filter (e.g. EC2, LAMBDA, RDS). Empty = all services.
        event_type_category: Filter by category: issue, accountNotification, scheduledChange, investigation.
        event_status: Filter by status: open, closed, upcoming.
        start_time: ISO 8601 start time filter. Empty = no start filter.
        end_time: ISO 8601 end time filter. Empty = no end filter.
        max_results: Maximum number of items to return (default: 50).

    Returns:
        Formatted string with event data, or an error message.
    """
    label = _get_account_label(account_id)

    emit_status(f"Querying Health in account {account_id} ({label})...",
                source="health_tool")

    try:
        client = _get_health_client(account_id, region)

        if action == "describe_event_details":
            if not event_arn:
                return "Error: event_arn is required for describe_event_details."
            print(f"[LTTM:Health] Calling describe_event_details for {event_arn}", flush=True)
            response = client.describe_event_details(eventArns=[event_arn])

            successful = response.get("successfulSet", [])
            if not successful:
                failed = response.get("failedSet", [])
                if failed:
                    error_msg = failed[0].get("errorMessage", "Unknown error")
                    return f"Failed to get event details: {error_msg}"
                return "No event details found for the given ARN."

            item = successful[0]
            event = item.get("event", {})
            desc = item.get("eventDescription", {}).get("latestDescription", "")
            result = _format_event(event, account_id, description=desc)
            print(f"[LTTM:Health] Returning event details for {event_arn}", flush=True)
            emit_status("Health query complete — event details returned", source="health_tool")
            return result

        elif action == "describe_affected_entities":
            if not event_arn:
                return "Error: event_arn is required for describe_affected_entities."
            print(f"[LTTM:Health] Calling describe_affected_entities for {event_arn}", flush=True)

            all_entities = []
            next_token = None
            while True:
                kwargs = {"filter": {"eventArns": [event_arn]}, "maxResults": min(max_results - len(all_entities), 100)}
                if next_token:
                    kwargs["nextToken"] = next_token
                response = client.describe_affected_entities(**kwargs)
                entities = response.get("entities", [])
                all_entities.extend(entities)
                next_token = response.get("nextToken")
                if not next_token or len(all_entities) >= max_results:
                    break

            if not all_entities:
                msg = "No affected entities found for the given event."
                print(f"[LTTM:Health] {msg}", flush=True)
                emit_status("Health query complete — 0 entities returned", source="health_tool")
                return msg

            formatted = [_format_entity(e, account_id) for e in all_entities]
            count = len(all_entities)
            print(f"[LTTM:Health] Returning {count} affected entities", flush=True)
            emit_status(f"Health query complete — {count} entities returned", source="health_tool")
            return "\n\n".join(formatted)

        else:
            event_filter = _build_event_filter(service, event_type_category, event_status, start_time, end_time)

            all_events = []
            next_token = None
            while True:
                kwargs = {"maxResults": min(max_results - len(all_events), 100)}
                if event_filter:
                    kwargs["filter"] = event_filter
                if next_token:
                    kwargs["nextToken"] = next_token

                print(f"[LTTM:Health] Calling describe_events (collected={len(all_events)})", flush=True)
                response = client.describe_events(**kwargs)

                events = response.get("events", [])
                all_events.extend(events)

                next_token = response.get("nextToken")
                if not next_token or len(all_events) >= max_results:
                    break

            if not all_events:
                msg = "No health events found for the given filters."
                print(f"[LTTM:Health] {msg}", flush=True)
                emit_status("Health query complete — 0 events returned", source="health_tool")
                return msg

            event_arns = [e["arn"] for e in all_events if "arn" in e]
            descriptions = {}
            if event_arns:
                for i in range(0, len(event_arns), 10):
                    batch = event_arns[i:i + 10]
                    try:
                        details_resp = client.describe_event_details(eventArns=batch)
                        for item in details_resp.get("successfulSet", []):
                            arn = item.get("event", {}).get("arn", "")
                            desc = item.get("eventDescription", {}).get("latestDescription", "")
                            if arn and desc:
                                descriptions[arn] = desc
                    except Exception:
                        pass

            formatted = [
                _format_event(e, account_id, description=descriptions.get(e.get("arn", ""), ""))
                for e in all_events
            ]
            result = "\n\n".join(formatted)

            count = len(all_events)
            print(f"[LTTM:Health] Returning {count} events for account {account_id}", flush=True)
            emit_status(f"Health query complete — {count} events returned", source="health_tool")
            return result

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "SubscriptionRequiredException":
            msg = (f"Error: SubscriptionRequired: Account {account_id} ({label}) requires a "
                   f"Business or Enterprise Support plan to use the AWS Health API.")
        elif error_code == "AccessDeniedException":
            msg = (f"Error: AccessDenied: Insufficient permissions to query Health in account "
                   f"{account_id} ({label}). Ensure the execution role has health:Describe* permissions.")
        else:
            msg = f"Error: {error_code}: {error_msg}"

        print(f"[LTTM:Health] {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        if "AssumeRole" in str(e) or "LTTMHealthReadRole" in str(e):
            msg = (f"Error: {error_type}: Failed to assume cross-account role LTTMHealthReadRole "
                   f"in account {account_id} ({label}). Verify the role exists and the trust "
                   f"policy allows assumption. {str(e)}")
        else:
            msg = f"Error: {error_type}: {str(e)}"

        print(f"[LTTM:Health] {msg}", flush=True)
        return msg
