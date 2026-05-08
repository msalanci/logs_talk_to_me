# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
guardduty_tool.py — Strands @tool for querying Amazon GuardDuty findings.

Registered on the guardduty_agent as @tool query_guardduty_findings.

Calls GuardDuty boto3 APIs directly (ListDetectors, ListFindings, GetFindings, GetDetector). 
No Athena/Glue/S3 infrastructure needed — pure API-based tool.

Supports cross-account queries via STS AssumeRole into `LTTMGuardDutyReadRole` for the account 2 and account 3 member accounts. 
Uses default execution-role credentials for the account 1 (main) account.

GuardDuty is regional — default region is `eu-central-1` (primary LTTM region).
The tool also supports `us-west-2` (AgentCore runtime region) via the `region` parameter.
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool
from utils.sse_emitter import emit_status
import utils.agent_vars as _vars

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMGuardDutyReadRole"
LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID
_detector_cache: dict[tuple[str, str], str] = {}


def _get_account_label(account_id: str) -> str:
    """Return human-readable label for an account ID."""
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_gd_client(account_id: str, region: str = "eu-central-1"):
    """
    Return a boto3 GuardDuty client for the given account and region.

    For the main account (matching `ACC1_ID`), returns a client built from the execution role's default credentials. 
    For any other account, calls STS AssumeRole against `LTTMGuardDutyReadRole` in that account and returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account ID, no role assumption happens.
        region: AWS region for the client, defaults to `eu-central-1`.

    Returns:
        A boto3 `guardduty` client scoped to `account_id` and `region`.
    """
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("guardduty", region_name=region)

    # Cross-account: assume role
    role_arn = CROSS_ACCOUNT_ROLE_TEMPLATE.format(account_id=account_id)
    label = _get_account_label(account_id)
    print(f"[LTTM:GuardDuty] Assuming role {role_arn} for {label} account", flush=True)

    sts = boto3.client("sts")
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"lttm-gd-{account_id}",
    )["Credentials"]

    return boto3.client(
        "guardduty",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _discover_detector_id(client, account_id: str, region: str) -> str:
    """
    Discover and cache the GuardDuty detector ID for an (account, region) pair.

    Calls `list_detectors()` and returns the first detector ID. 
    Subsequent calls for the same `(account_id, region)` return the cached value without hitting the API.

    Args:
        client: boto3 `guardduty` client already scoped to the target account, obtained from `_get_gd_client()`.
        account_id: AWS account ID
        region: AWS region

    Returns:
        The ID of the first detector found in the account/region.

    Raises:
        ValueError: No detector is configured for this account/region — GuardDuty is likely not enabled. 
                    The error message suggests enabling it via the AWS console or Terraform.
    """
    cache_key = (account_id, region)
    if cache_key in _detector_cache:
        return _detector_cache[cache_key]

    print(f"[LTTM:GuardDuty] Discovering detector in account {account_id}, region {region}", flush=True)
    response = client.list_detectors()
    detector_ids = response.get("DetectorIds", [])

    if not detector_ids:
        raise ValueError(
            f"No GuardDuty detector found in account {account_id}, region {region}. "
            f"GuardDuty may not be enabled. Enable it via the AWS console or Terraform."
        )

    detector_id = detector_ids[0]
    _detector_cache[cache_key] = detector_id
    print(f"[LTTM:GuardDuty] Found detector: {detector_id}", flush=True)
    return detector_id


def _severity_label(score: float) -> str:
    """Map numeric severity to human-readable label."""
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    return "Low"


def _build_finding_criteria(
    severity_min: float,
    finding_type: str,
    start_time: str,
    end_time: str,
) -> dict:
    """
    Build the `FindingCriteria` dict for `list_findings`.

    Assembles the filter shape GuardDuty expects: a `Criterion` dict mapping field names (`severity`, `type`, `updatedAt`) to comparison operators (`gte`, `lte`, `eq`). 
    Time values are converted from ISO 8601 strings to millisecond epoch timestamps as `updatedAt` requires.

    Args:
        severity_min: Minimum severity threshold. Ignored when 0 or negative.
        finding_type: Finding-type equality filter. Empty string disables.
        start_time: ISO 8601 lower bound on `updatedAt`. Empty string disables.
        end_time: ISO 8601 upper bound on `updatedAt`. Empty string disables.

    Returns:
        A dict of shape `{"Criterion": {...}}` ready to pass as the `FindingCriteria` argument. 
        Returns an empty dict `{}` when no filter conditions were provided, which callers treat as "do not include the FindingCriteria argument at all."
    """
    criterion = {}
    if severity_min > 0:
        criterion["severity"] = {"gte": int(severity_min)}
    if finding_type:
        criterion["type"] = {"eq": [finding_type]}
    if start_time:
        criterion["updatedAt"] = criterion.get("updatedAt", {})
        criterion["updatedAt"]["gte"] = int(
            __import__("datetime").datetime.fromisoformat(start_time.replace("Z", "+00:00")).timestamp() * 1000
        )
    if end_time:
        criterion["updatedAt"] = criterion.get("updatedAt", {})
        criterion["updatedAt"]["lte"] = int(
            __import__("datetime").datetime.fromisoformat(end_time.replace("Z", "+00:00")).timestamp() * 1000
        )
    if not criterion:
        return {}
    return {"Criterion": criterion}


def _format_finding(finding: dict, account_id: str) -> str:
    """Format a single finding dict into a human-readable text block."""
    label = _get_account_label(account_id)
    finding_type = finding.get("Type", "N/A")
    severity = finding.get("Severity", 0.0)
    sev_label = _severity_label(severity)
    title = finding.get("Title", "N/A")
    description = finding.get("Description", "N/A")
    region = finding.get("Region", "N/A")
    created = finding.get("CreatedAt", "N/A")
    updated = finding.get("UpdatedAt", "N/A")

    # Resource details
    resource = finding.get("Resource", {})
    resource_type = resource.get("ResourceType", "N/A")
    resource_id = "N/A"
    if resource_type == "Instance":
        instance = resource.get("InstanceDetails", {})
        resource_id = instance.get("InstanceId", "N/A")
    elif resource_type == "AccessKey":
        access_key = resource.get("AccessKeyDetails", {})
        resource_id = access_key.get("AccessKeyId", "N/A")
    elif resource_type == "S3Bucket":
        s3 = resource.get("S3BucketDetails", [])
        if s3:
            resource_id = s3[0].get("Name", "N/A")

    # Actor details
    service = finding.get("Service", {})
    action = service.get("Action", {})
    action_type = action.get("ActionType", "N/A")
    remote_ip = "N/A"
    if "AwsApiCallAction" in action:
        remote_ip = action["AwsApiCallAction"].get("RemoteIpDetails", {}).get("IpAddressV4", "N/A")
    elif "NetworkConnectionAction" in action:
        remote_ip = action["NetworkConnectionAction"].get("RemoteIpDetails", {}).get("IpAddressV4", "N/A")
    elif "PortProbeAction" in action:
        probes = action["PortProbeAction"].get("PortProbeDetails", [])
        if probes:
            remote_ip = probes[0].get("RemoteIpDetails", {}).get("IpAddressV4", "N/A")

    event_count = service.get("Count", "N/A")
    first_seen = service.get("EventFirstSeen", "N/A")
    last_seen = service.get("EventLastSeen", "N/A")

    lines = [
        "--- GuardDuty Finding ---",
        f"Account: {account_id} ({label})",
        f"Finding Type: {finding_type}",
        f"Severity: {severity} ({sev_label})",
        f"Title: {title}",
        f"Description: {description}",
        f"Resource Type: {resource_type}",
        f"Resource ID: {resource_id}",
        f"Action Type: {action_type}",
        f"Remote IP: {remote_ip}",
        f"Region: {region}",
        f"Event Count: {event_count}",
        f"First Seen: {first_seen}",
        f"Last Seen: {last_seen}",
        f"Created: {created}",
        f"Updated: {updated}",
    ]

    return "\n".join(lines)


@tool
def query_guardduty_findings(
    account_id: str,
    action: str = "list_findings",
    detector_id: str = "",
    severity_min: float = 0.0,
    finding_type: str = "",
    start_time: str = "",
    end_time: str = "",
    max_results: int = 20,
    region: str = "eu-central-1",
) -> str:
    """
    Query Amazon GuardDuty findings for a specific AWS account.

    Calls GuardDuty boto3 APIs to retrieve threat detection findings, detector info, or finding details. 
    Handles cross-account access via STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required)
        action: API action — list_detectors, list_findings, get_findings, or get_detector
        detector_id: GuardDuty detector ID. Auto-discovered if empty
        severity_min: Minimum severity filter (0.0 = no filter, 7.0 = High+, 9.0 = Critical)
        finding_type: Finding type filter (e.g. "Recon:EC2/PortProbeUnprotectedPort")
        start_time: Start time filter for updatedAt
        end_time: End time filter for updatedAt
        max_results: Maximum number of findings to return, defaults to 20
        region: AWS region, defaults to eu-central-1)

    Returns:
        Formatted string with finding data, or an error message
    """
    label = _get_account_label(account_id)

    emit_status(f"Querying GuardDuty in account {account_id} ({label})...",
                source="guardduty_tool")

    try:
        client = _get_gd_client(account_id, region)

        if not detector_id:
            detector_id = _discover_detector_id(client, account_id, region)

        if action == "list_detectors":
            print(f"[LTTM:GuardDuty] Calling list_detectors", flush=True)
            response = client.list_detectors()
            ids = response.get("DetectorIds", [])
            if not ids:
                msg = f"No GuardDuty detectors found in account {account_id} ({label}), region {region}."
                print(f"[LTTM:GuardDuty] {msg}", flush=True)
                emit_status("GuardDuty query complete — 0 detectors", source="guardduty_tool")
                return msg
            result = f"GuardDuty detectors in account {account_id} ({label}), region {region}:\n"
            result += "\n".join(f"  - {did}" for did in ids)
            print(f"[LTTM:GuardDuty] Returning {len(ids)} detector(s)", flush=True)
            emit_status(f"GuardDuty query complete — {len(ids)} detector(s)", source="guardduty_tool")
            return result

        elif action == "get_detector":
            print(f"[LTTM:GuardDuty] Calling get_detector for {detector_id}", flush=True)
            response = client.get_detector(DetectorId=detector_id)
            status = response.get("Status", "N/A")
            created = response.get("CreatedAt", "N/A")
            updated = response.get("UpdatedAt", "N/A")
            finding_pub = response.get("FindingPublishingFrequency", "N/A")
            result = (
                f"--- GuardDuty Detector ---\n"
                f"Account: {account_id} ({label})\n"
                f"Detector ID: {detector_id}\n"
                f"Status: {status}\n"
                f"Finding Publishing Frequency: {finding_pub}\n"
                f"Created: {created}\n"
                f"Updated: {updated}"
            )
            print(f"[LTTM:GuardDuty] Returning detector info", flush=True)
            emit_status("GuardDuty query complete — detector info returned", source="guardduty_tool")
            return result

        elif action == "get_findings":
            if not finding_type:
                return "Error: finding_type parameter must contain comma-separated finding IDs for get_findings action."
            finding_ids = [fid.strip() for fid in finding_type.split(",") if fid.strip()]
            print(f"[LTTM:GuardDuty] Calling get_findings for {len(finding_ids)} finding(s)", flush=True)
            response = client.get_findings(DetectorId=detector_id, FindingIds=finding_ids)
            findings = response.get("Findings", [])
            if not findings:
                msg = "No findings found for the given IDs."
                print(f"[LTTM:GuardDuty] {msg}", flush=True)
                emit_status("GuardDuty query complete — 0 findings", source="guardduty_tool")
                return msg
            formatted = [_format_finding(f, account_id) for f in findings]
            count = len(findings)
            print(f"[LTTM:GuardDuty] Returning {count} finding(s)", flush=True)
            emit_status(f"GuardDuty query complete — {count} finding(s)", source="guardduty_tool")
            return "\n\n".join(formatted)

        else:
            criteria = _build_finding_criteria(severity_min, finding_type, start_time, end_time)
            all_finding_ids = []
            next_token = None

            while True:
                kwargs = {
                    "DetectorId": detector_id,
                    "MaxResults": 50,
                }
                if criteria:
                    kwargs["FindingCriteria"] = criteria
                if next_token:
                    kwargs["NextToken"] = next_token

                print(f"[LTTM:GuardDuty] Calling list_findings (collected={len(all_finding_ids)})", flush=True)
                response = client.list_findings(**kwargs)

                finding_ids = response.get("FindingIds", [])
                all_finding_ids.extend(finding_ids)

                next_token = response.get("NextToken")
                if not next_token:
                    break

            total_count = len(all_finding_ids)

            if not all_finding_ids:
                msg = "No GuardDuty findings found for the given filters."
                print(f"[LTTM:GuardDuty] {msg}", flush=True)
                emit_status("GuardDuty query complete — 0 findings", source="guardduty_tool")
                return msg

            truncated = total_count > max_results
            ids_to_fetch = all_finding_ids[:max_results]

            all_findings = []
            for i in range(0, len(ids_to_fetch), 50):
                batch = ids_to_fetch[i:i + 50]
                print(f"[LTTM:GuardDuty] Calling get_findings for batch of {len(batch)}", flush=True)
                details_resp = client.get_findings(DetectorId=detector_id, FindingIds=batch)
                all_findings.extend(details_resp.get("Findings", []))

            formatted = [_format_finding(f, account_id) for f in all_findings]
            count = len(all_findings)
            print(f"[LTTM:GuardDuty] Returning {count} finding(s) for account {account_id} (total: {total_count})", flush=True)
            emit_status(f"GuardDuty query complete — {count} finding(s)", source="guardduty_tool")

            result = "\n\n".join(formatted)

            if truncated:
                result += (
                    f"\n\n--- NOTICE ---\n"
                    f"Showing {count} of {total_count} total findings. "
                    f"Due to token limitations, a maximum of {max_results} findings can be returned per query. "
                    f"To see more, narrow your search using severity_min, finding_type, or time range filters."
                )

            return result

    except ValueError as e:
        msg = str(e)
        print(f"[LTTM:GuardDuty] {msg}", flush=True)
        return msg

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "AccessDeniedException":
            msg = (f"Insufficient permissions to query GuardDuty in account {account_id} ({label}). "
                   f"Ensure the execution role has guardduty:List*/Get* permissions.")
        elif error_code == "BadRequestException":
            msg = f"Invalid request parameters: {error_msg}"
        elif error_code == "InternalServerErrorException":
            msg = f"GuardDuty service error: {error_msg}"
        else:
            msg = f"Error querying GuardDuty: {error_code}: {error_msg}"

        print(f"[LTTM:GuardDuty] ClientError: {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        if "AssumeRole" in str(e) or "LTTMGuardDutyReadRole" in str(e):
            msg = (f"Failed to assume cross-account role LTTMGuardDutyReadRole in account "
                   f"{account_id} ({label}). Verify the role exists and the trust policy "
                   f"allows assumption. Error: {error_type}: {str(e)}")
        else:
            msg = f"Error querying GuardDuty: {error_type}: {str(e)}"

        print(f"[LTTM:GuardDuty] {msg}", flush=True)
        return msg
