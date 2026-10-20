# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
guardduty_tool.py — Strands @tool for querying Amazon GuardDuty findings.

Registered on the guardduty_agent as @tool query_guardduty_findings.

Calls GuardDuty boto3 APIs directly (ListDetectors, ListFindings, GetFindings,
GetDetector). No Athena/Glue/S3 infrastructure needed — pure API-based tool.

Supports cross-account queries via STS AssumeRole into `LTTMGuardDutyReadRole`
for the account 2 and account 3 member accounts. Uses default execution-role
credentials for the account 1 (main) account.

GuardDuty is regional. With no region given, the tool queries every region in
`GUARDDUTY_REGIONS` (agents/utils/agent_vars.py) and labels each block of
results with its region, under one total for all of them, added up by code.

The main account is the GuardDuty administrator of dev and prod in eu-central-1,
so its detector also lists their findings. Each account's list keeps only the
account's own findings, so no finding is shown or counted twice.
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool

from utils.sse_emitter import emit_status
from utils.finding_counts import count_line, region_blocks

NO_FINDINGS = ("No GuardDuty findings found", "No GuardDuty detector found")

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMGuardDutyReadRole"

import utils.agent_vars as _vars
from utils.untrusted_text import one_line_args

LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID

_detector_cache: dict[tuple[str, str], str] = {}


def _get_account_label(account_id: str) -> str:
    """Give an account ID's label, for the text the model reads.

    Args:
        account_id: A 12-digit AWS account ID.

    Returns:
        The account's label from agent_vars ACCOUNTS, such as "dev"; the ID itself for an account LTTM does not
        know.
    """
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_gd_client(account_id: str, region: str = _vars.DEFAULT_REGION):
    """Return a boto3 GuardDuty client for the given account and region.

    For the main account (matching `MAIN_ACCOUNT_ID`), returns a client built
    from the execution role's default credentials. For any other account,
    calls STS AssumeRole against `LTTMGuardDutyReadRole` in that account and
    returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account ID,
            no role assumption happens.
        region: AWS region for the client. Defaults to `DEFAULT_REGION`.

    Returns:
        A boto3 `guardduty` client scoped to `account_id` and `region`.
    """
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("guardduty", region_name=region)

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
    """Discover and cache the GuardDuty detector ID for an (account, region) pair.

    Calls `list_detectors()` and returns the first detector ID. Subsequent
    calls for the same `(account_id, region)` in this process return the cached
    value without hitting the API.

    Args:
        client: boto3 `guardduty` client already scoped to the target
            account. Typically obtained from `_get_gd_client()`.
        account_id: AWS account ID, used only as the cache key.
        region: AWS region, used only as the cache key.

    Returns:
        The ID of the first detector found in the account/region.

    Raises:
        ValueError: No detector is configured for this account/region. The message
            says where GuardDuty runs, per account, from GUARDDUTY_REGIONS_BY_ACCOUNT:
            a definite reason, written by code, not "likely not enabled". It starts
            with a NO_FINDINGS prefix, so the regions' total counts the region as
            one without findings.
    """
    cache_key = (account_id, region)
    if cache_key in _detector_cache:
        return _detector_cache[cache_key]

    print(f"[LTTM:GuardDuty] Discovering detector in account {account_id}, region {region}", flush=True)
    response = client.list_detectors()
    detector_ids = response.get("DetectorIds", [])

    if not detector_ids:
        where = _vars.coverage(_vars.GUARDDUTY_REGIONS_BY_ACCOUNT)
        if region not in _vars.GUARDDUTY_REGIONS_BY_ACCOUNT.get(account_id, []):
            raise ValueError(f"No GuardDuty detector found in account {account_id} ({_get_account_label(account_id)}), "
                             f"region {region}: GuardDuty runs in {where}, not in this region.")
        raise ValueError(f"No GuardDuty detector found in account {account_id} ({_get_account_label(account_id)}), "
                         f"region {region}, although Terraform sets GuardDuty up there ({where}): the detector is missing.")

    detector_id = detector_ids[0]
    _detector_cache[cache_key] = detector_id
    print(f"[LTTM:GuardDuty] Found detector: {detector_id}", flush=True)
    return detector_id


def _severity_label(score: float) -> str:
    """Map numeric severity to human-readable label.

    Args:
        score: A GuardDuty severity, 0.0 to 10.0.

    Returns:
        "Critical" from 9, "High" from 7, "Medium" from 4, "Low" below.
    """
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    return "Low"


def _build_finding_criteria(
    account_id: str,
    severity_min: float,
    start_time: str,
    end_time: str,
) -> dict:
    """Build the `FindingCriteria` dict for `list_findings`.

    Assembles the filter shape GuardDuty expects: a `Criterion` dict mapping
    field names (`accountId`, `severity`, `updatedAt`) to comparison operators
    (`Equals`, `GreaterThanOrEqual`, `LessThanOrEqual`). The operator names are
    case-sensitive: the lowercase `eq`/`gte`/`lte` fail every filtered call with
    ParamValidationError. Time values are
    converted from ISO 8601 strings to millisecond epoch timestamps as
    `updatedAt` requires. The finding type is not here: ListFindings matches
    whole types only, so the caller filters types itself.

    Args:
        account_id: Only this account's findings (the main account's detector
            lists dev's and prod's findings too).
        severity_min: Minimum severity threshold. Ignored when 0 or negative.
        start_time: ISO 8601 lower bound on `updatedAt`. Empty string disables.
        end_time: ISO 8601 upper bound on `updatedAt`. Empty string disables.

    Returns:
        A dict of shape `{"Criterion": {...}}` ready to pass as the
        `FindingCriteria` argument.
    """
    criterion = {"accountId": {"Equals": [account_id]}}
    if severity_min > 0:
        criterion["severity"] = {"GreaterThanOrEqual": int(severity_min)}
    if start_time:
        criterion["updatedAt"] = criterion.get("updatedAt", {})
        criterion["updatedAt"]["GreaterThanOrEqual"] = int(
            __import__("datetime").datetime.fromisoformat(start_time.replace("Z", "+00:00")).timestamp() * 1000
        )
    if end_time:
        criterion["updatedAt"] = criterion.get("updatedAt", {})
        criterion["updatedAt"]["LessThanOrEqual"] = int(
            __import__("datetime").datetime.fromisoformat(end_time.replace("Z", "+00:00")).timestamp() * 1000
        )
    return {"Criterion": criterion}


@one_line_args
def _format_finding(finding: dict, account_id: str) -> str:
    """Format a single finding dict into a human-readable text block, under the account it belongs to.

    The account is the finding's own AccountId, not the account that was asked: the main account's detector lists
    dev's and prod's findings too. Every value is put on one line (one_line_args).

    Args:
        finding: A finding as GetFindings returns it.
        account_id: The account that was asked, used when the finding has no AccountId.

    Returns:
        The "--- GuardDuty Finding ---" block, with "N/A" where a value is missing.
    """
    account_id = finding.get("AccountId") or account_id
    label = _get_account_label(account_id)
    finding_type = finding.get("Type", "N/A")
    severity = finding.get("Severity", 0.0)
    sev_label = _severity_label(severity)
    title = finding.get("Title", "N/A")
    description = finding.get("Description", "N/A")
    region = finding.get("Region", "N/A")
    created = finding.get("CreatedAt", "N/A")
    updated = finding.get("UpdatedAt", "N/A")

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


def _query_guardduty_region(
    account_id: str,
    action: str,
    detector_id: str,
    severity_min: float,
    finding_type: str,
    start_time: str,
    end_time: str,
    max_results: int,
    region: str,
) -> str:
    """Run one GuardDuty query in one region; `query_guardduty_findings` calls it per region.

    For list_findings, ListFindings collects every matching ID, then GetFindings reads the details of the first
    max_results. ListFindings matches whole finding types only, and the agent passes beginnings such as "Recon:",
    so the findings whose type starts with `finding_type` are kept by code. A findings list starts with a count
    line over every match, stated by code so the answer quotes it instead of counting.

    Args:
        account_id: The account to query.
        action: list_detectors, get_detector, get_findings, or anything else for list_findings.
        detector_id: The detector ID; "" to discover it.
        severity_min: The lowest severity; 0 for all.
        finding_type: A finding type or its beginning; "" for all. For get_findings, the finding IDs, separated by
            commas.
        start_time: ISO 8601 lower bound on updatedAt; "" for none.
        end_time: ISO 8601 upper bound on updatedAt; "" for none.
        max_results: The most findings read in detail.
        region: The region to query.

    Returns:
        The detectors, the detector, or the findings as text; a message starting with a NO_FINDINGS prefix when the
        region has no findings or no detector; or another message or an error message. Never raises. Also sends
        status lines to the CLI.
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
            criteria = _build_finding_criteria(account_id, severity_min, start_time, end_time)

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

            if finding_type and all_finding_ids:
                typed = []
                for i in range(0, len(all_finding_ids), 50):
                    batch = client.get_findings(DetectorId=detector_id, FindingIds=all_finding_ids[i:i + 50])
                    typed += [f["Id"] for f in batch.get("Findings", []) if f.get("Type", "").startswith(finding_type)]
                all_finding_ids = typed

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

            severities = [_severity_label(f.get("Severity", 0.0)) for f in all_findings]
            header = count_line(severities, ["Critical", "High", "Medium", "Low"], total_count)
            result = header + "\n\n" + "\n\n".join(formatted)

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
    region: str = "",
) -> str:
    """
    Query Amazon GuardDuty findings for a specific AWS account.

    Calls GuardDuty boto3 APIs to retrieve threat detection findings, detector
    info, or finding details. Handles cross-account access via STS AssumeRole
    for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — list_detectors, list_findings, get_findings, or get_detector.
        detector_id: GuardDuty detector ID. Auto-discovered if empty.
        severity_min: Minimum severity filter (0.0 = no filter, 7.0 = High+, 9.0 = Critical).
        finding_type: Finding type, or its beginning (e.g. "Recon:" or "Recon:EC2/PortProbeUnprotectedPort").
            Empty = all types. For get_findings: the finding IDs, separated by commas.
        start_time: ISO 8601 start time filter for updatedAt. Empty = no start filter.
        end_time: ISO 8601 end time filter for updatedAt. Empty = no end filter.
        max_results: Maximum number of findings to return per region (default: 20).
        region: AWS region. Leave empty (default) to query every region where GuardDuty
            runs; set it only when the user names a region.

    Returns:
        Formatted string with finding data, or an error message. With several
        regions, one block per region, each headed with its region, after one
        total for all of them.
    """
    regions = [region] if region else _vars.GUARDDUTY_REGIONS
    results = [
        _query_guardduty_region(account_id, action, detector_id, severity_min, finding_type,
                                start_time, end_time, max_results, r)
        for r in regions
    ]
    return region_blocks(regions, results, f"Account {account_id} ({_get_account_label(account_id)})", NO_FINDINGS)
