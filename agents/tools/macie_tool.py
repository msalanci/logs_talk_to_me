# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
macie_tool.py — Strands @tool for querying Amazon Macie sensitive data findings.

Registered on the macie_agent as @tool query_macie_findings.

Calls Macie boto3 APIs directly (ListFindings, GetFindings, DescribeBuckets,
ListResourceProfiles, GetBucketStatistics, GetFindingStatistics,
SearchResources). No Athena/Glue/S3 infrastructure needed — pure API-based
tool.

Supports cross-account queries via STS AssumeRole into `LTTMMacieReadRole`
for the account 2 and account 3 member accounts. Uses default execution-role
credentials for the account 1 (main) account.

Macie is regional. With no region given, the tool queries every region in
`MACIE_REGIONS` (agents/utils/agent_vars.py) and labels each block of results
with its region, under one total for all of them, added up by code. A region
outside that list is answered by code, without a call: there AWS would only
return an error.
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool

from utils.sse_emitter import emit_status
from utils.finding_counts import count_line, region_blocks

NO_FINDINGS = ("No Macie findings found",)

import utils.agent_vars as _vars
from utils.untrusted_text import one_line_args

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMMacieReadRole"

LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID


def _get_account_label(account_id: str) -> str:
    """Give an account ID's label, for the text the model reads.

    Args:
        account_id: A 12-digit AWS account ID.

    Returns:
        The account's label from agent_vars ACCOUNTS, such as "dev"; the ID itself for an account LTTM does not
        know.
    """
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_macie_client(account_id: str, region: str = _vars.DEFAULT_REGION):
    """Return a boto3 macie2 client for the given account and region.

    For the main account (matching `MAIN_ACCOUNT_ID`), returns a client built
    from the execution role's default credentials. For any other account,
    calls STS AssumeRole against `LTTMMacieReadRole` in that account and
    returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account ID,
            no role assumption happens.
        region: AWS region for the client. Defaults to `DEFAULT_REGION`.

    Returns:
        A boto3 `macie2` client scoped to `account_id` and `region`.
    """
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("macie2", region_name=region)

    role_arn = CROSS_ACCOUNT_ROLE_TEMPLATE.format(account_id=account_id)
    label = _get_account_label(account_id)
    print(f"[LTTM:Macie] Assuming role {role_arn} for {label} account", flush=True)

    sts = boto3.client("sts")
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"lttm-macie-{account_id}",
    )["Credentials"]

    return boto3.client(
        "macie2",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _severity_label(score) -> str:
    """Map Macie numeric severity to human-readable label.

    Macie severity model: 1=Low, 2=Medium, 3=High.

    Args:
        score: A Macie severity score, as a number or text; None or 0 for none.

    Returns:
        "High" from 3, "Medium" from 2, "Low" below.
    """
    score = float(score) if score else 0.0
    if score >= 3.0:
        return "High"
    if score >= 2.0:
        return "Medium"
    return "Low"


SEVERITY_LABELS = ["Low", "Medium", "High"]


def _build_finding_criteria(
    severity_min: float,
    finding_type: str,
    start_time: str,
    end_time: str,
    bucket_name: str = "",
) -> dict:
    """Build the Macie `FindingCriteria` dict for `list_findings`.

    Assembles the Macie-specific filter shape. Time values are converted
    from ISO 8601 strings to millisecond epoch timestamps because the
    `updatedAt` field requires milliseconds. SEVERITY_LABELS is in score order
    (1=Low, 2=Medium, 3=High), so its slice from `severity_min - 1` is every
    label at or above the minimum.

    Args:
        severity_min: Minimum severity threshold (1=Low, 2=Medium, 3=High).
            Values <=0 disable. Sent as `severity.description` equal to each
            label at or above it: Macie rejects `severity` and `severity.score`
            as criterion keys (ValidationException, checked live).
        finding_type: Finding-type equality filter. Empty string disables.
        start_time: ISO 8601 lower bound on `updatedAt`. Empty string
            disables.
        end_time: ISO 8601 upper bound on `updatedAt`. Empty string
            disables.
        bucket_name: Only findings for this S3 bucket
            (`resourcesAffected.s3Bucket.name`). Empty string disables.

    Returns:
        A dict of shape `{"criterion": {...}}` ready to pass as the
        `findingCriteria` argument. Returns an empty dict `{}` when no
        filter conditions were provided, which callers treat as "do not
        include the findingCriteria argument at all."
    """
    criterion = {}
    if severity_min > 0:
        criterion["severity.description"] = {"eq": SEVERITY_LABELS[max(0, min(int(severity_min), 3) - 1):]}
    if bucket_name:
        criterion["resourcesAffected.s3Bucket.name"] = {"eq": [bucket_name]}
    if finding_type:
        criterion["type"] = {"eq": [finding_type]}
    if start_time:
        criterion["updatedAt"] = criterion.get("updatedAt", {})
        criterion["updatedAt"]["gte"] = int(
            __import__("datetime").datetime.fromisoformat(
                start_time.replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
    if end_time:
        criterion["updatedAt"] = criterion.get("updatedAt", {})
        criterion["updatedAt"]["lte"] = int(
            __import__("datetime").datetime.fromisoformat(
                end_time.replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
    if not criterion:
        return {}
    return {"criterion": criterion}


@one_line_args
def _format_finding(finding: dict, account_id: str) -> str:
    """Format a single Macie finding dict into a human-readable text block.

    Every value is put on one line (one_line_args).

    Args:
        finding: A finding as GetFindings returns it.
        account_id: The account the finding was read in.

    Returns:
        The "--- Macie Finding ---" block: the bucket and object, the sensitive data categories and types with their
        counts, and their total; "N/A" where a value is missing.
    """
    label = _get_account_label(account_id)
    finding_type = finding.get("type", "N/A")
    severity = finding.get("severity", {})
    sev_score = severity.get("score", 0) if isinstance(severity, dict) else severity
    sev_label = _severity_label(sev_score)
    region = finding.get("region", "N/A")
    created = finding.get("createdAt", "N/A")
    updated = finding.get("updatedAt", "N/A")

    resources = finding.get("resourcesAffected", {})
    s3_bucket = resources.get("s3Bucket", {})
    bucket_name = s3_bucket.get("name", "N/A")
    s3_object = resources.get("s3Object", {})
    object_key = s3_object.get("key", "N/A")

    classification = finding.get("classificationDetails", {})
    result = classification.get("result", {})
    sensitive_data = result.get("sensitiveData", [])

    categories = []
    data_types_info = []
    total_occurrences = 0

    for sd in sensitive_data:
        cat = sd.get("category", "UNKNOWN")
        if cat not in categories:
            categories.append(cat)
        detections = sd.get("detections", [])
        for det in detections:
            dt_type = det.get("type", "UNKNOWN")
            dt_count = det.get("count", 0)
            data_types_info.append(f"{dt_type} ({dt_count})")
            total_occurrences += dt_count

    categories_str = ", ".join(categories) if categories else "N/A"
    data_types_str = ", ".join(data_types_info) if data_types_info else "N/A"

    lines = [
        "--- Macie Finding ---",
        f"Account: {account_id} ({label})",
        f"Finding Type: {finding_type}",
        f"Severity: {sev_score} ({sev_label})",
        f"S3 Bucket: {bucket_name}",
        f"S3 Object Key: {object_key}",
        f"Sensitive Data Categories: {categories_str}",
        f"Data Types Found: {data_types_str}",
        f"Total Occurrences: {total_occurrences}",
        f"Region: {region}",
        f"Created: {created}",
        f"Updated: {updated}",
    ]

    return "\n".join(lines)


@one_line_args
def _format_bucket(bucket: dict, account_id: str) -> str:
    """Format a single Macie bucket description into a human-readable text block.

    Every value is put on one line (one_line_args).

    Args:
        bucket: A bucket as DescribeBuckets returns it.
        account_id: The account the bucket was read in.

    Returns:
        The "--- Macie Bucket Profile ---" block, the size in bytes, KB, MB or GB; "N/A" where a value is missing.
    """
    label = _get_account_label(account_id)
    bucket_name = bucket.get("bucketName", "N/A")
    region = bucket.get("region", "N/A")

    sensitivity_score = bucket.get("sensitivityScore", "N/A")

    object_count = bucket.get("classifiableObjectCount", "N/A")
    total_size = bucket.get("classifiableSizeInBytes", 0)
    if isinstance(total_size, (int, float)) and total_size > 0:
        if total_size >= 1_073_741_824:
            size_str = f"{total_size / 1_073_741_824:.1f} GB"
        elif total_size >= 1_048_576:
            size_str = f"{total_size / 1_048_576:.1f} MB"
        elif total_size >= 1024:
            size_str = f"{total_size / 1024:.1f} KB"
        else:
            size_str = f"{total_size} bytes"
    else:
        size_str = "N/A"

    encryption = bucket.get("serverSideEncryption", {})
    enc_type = "N/A"
    if isinstance(encryption, dict):
        enc_type = encryption.get("type", "N/A")

    public_access = bucket.get("publicAccess", {})
    is_public = "N/A"
    if isinstance(public_access, dict):
        effective = public_access.get("effectivePermission", "N/A")
        is_public = "Not public" if effective == "NOT_PUBLIC" else effective

    last_run = bucket.get("lastAutomatedDiscoveryTime", "N/A")

    lines = [
        "--- Macie Bucket Profile ---",
        f"Account: {account_id} ({label})",
        f"Bucket: {bucket_name}",
        f"Region: {region}",
        f"Sensitivity Score: {sensitivity_score}",
        f"Classifiable Objects: {object_count}",
        f"Total Size: {size_str}",
        f"Encryption: {enc_type}",
        f"Public Access: {is_public}",
        f"Last Discovery Run: {last_run}",
    ]

    return "\n".join(lines)


def _query_macie_region(
    account_id: str,
    action: str,
    severity_min: float,
    finding_type: str,
    bucket_name: str,
    start_time: str,
    end_time: str,
    max_results: int,
    region: str,
) -> str:
    """Run one Macie query in one region; `query_macie_findings` calls it per region.

    For list_findings, ListFindings collects every matching ID, newest update first, then GetFindings reads the
    details of the first max_results. A findings list starts with a count line over every match, stated by code so
    the answer quotes it instead of counting. AWS answers a region where Macie is not enabled with an
    AccessDenied that says "not enabled"; it is reported as Macie not enabled, not as missing permissions.

    Args:
        account_id: The account to query.
        action: describe_buckets, list_resource_profiles, get_bucket_statistics, get_finding_statistics,
            search_resources, or anything else for list_findings.
        severity_min: The lowest severity (1=Low, 2=Medium, 3=High); 0 for all.
        finding_type: A finding type; "" for all.
        bucket_name: Only this bucket; "" for all.
        start_time: ISO 8601 lower bound on updatedAt; "" for none.
        end_time: ISO 8601 upper bound on updatedAt; "" for none.
        max_results: The most findings, profiles or resources to return.
        region: The region to query.

    Returns:
        The buckets, profiles, statistics, resources or findings as text; a message starting with NO_FINDINGS's
        prefix when the region has no findings; or another message or an error message. Never raises. Also sends
        status lines to the CLI.
    """
    label = _get_account_label(account_id)

    emit_status(
        f"Querying Macie in account {account_id} ({label}), region {region}...",
        source="macie_tool",
    )

    try:
        client = _get_macie_client(account_id, region)

        if action == "describe_buckets":
            print(f"[LTTM:Macie] Calling describe_buckets", flush=True)
            kwargs = {}
            if bucket_name:
                kwargs["criteria"] = {
                    "bucketName": {"eq": [bucket_name]}
                }
            response = client.describe_buckets(**kwargs)
            buckets = response.get("buckets", [])
            if not buckets:
                msg = f"No S3 buckets found in Macie for account {account_id} ({label}), region {region}."
                if bucket_name:
                    msg = f"Bucket '{bucket_name}' not found in Macie for account {account_id} ({label}), region {region}."
                print(f"[LTTM:Macie] {msg}", flush=True)
                emit_status("Macie query complete — 0 buckets", source="macie_tool")
                return msg
            formatted = [_format_bucket(b, account_id) for b in buckets]
            count = len(buckets)
            print(f"[LTTM:Macie] Returning {count} bucket(s)", flush=True)
            emit_status(f"Macie query complete — {count} bucket(s)", source="macie_tool")
            return "\n\n".join(formatted)

        elif action == "list_resource_profiles":
            print(f"[LTTM:Macie] Calling list_resource_profiles", flush=True)
            response = client.list_resource_profiles(maxResults=max_results)
            profiles = response.get("resourceProfiles", [])
            if not profiles:
                msg = f"No resource profiles found in Macie for account {account_id} ({label}), region {region}."
                print(f"[LTTM:Macie] {msg}", flush=True)
                emit_status("Macie query complete — 0 resource profiles", source="macie_tool")
                return msg
            lines = [f"Macie Resource Profiles — account {account_id} ({label}), region {region}:\n"]
            for p in profiles:
                arn = p.get("resourceArn", "N/A")
                score = p.get("sensitivityScore", "N/A")
                lines.append(f"  - {arn} (sensitivity score: {score})")
            count = len(profiles)
            print(f"[LTTM:Macie] Returning {count} resource profile(s)", flush=True)
            emit_status(f"Macie query complete — {count} resource profile(s)", source="macie_tool")
            return "\n".join(lines)

        elif action == "get_bucket_statistics":
            print(f"[LTTM:Macie] Calling get_bucket_statistics", flush=True)
            response = client.get_bucket_statistics(accountId=account_id)
            total_buckets = response.get("bucketsCount", 0)
            classifiable = response.get("classifiableObjectCount", 0)
            size_bytes = response.get("classifiableSizeInBytes", 0)
            if isinstance(size_bytes, (int, float)) and size_bytes > 0:
                if size_bytes >= 1_073_741_824:
                    size_str = f"{size_bytes / 1_073_741_824:.1f} GB"
                elif size_bytes >= 1_048_576:
                    size_str = f"{size_bytes / 1_048_576:.1f} MB"
                else:
                    size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = "0 bytes"

            result_text = (
                f"--- Macie Bucket Statistics ---\n"
                f"Account: {account_id} ({label})\n"
                f"Region: {region}\n"
                f"Total Buckets: {total_buckets}\n"
                f"Classifiable Objects: {classifiable}\n"
                f"Classifiable Size: {size_str}"
            )
            print(f"[LTTM:Macie] Returning bucket statistics", flush=True)
            emit_status("Macie query complete — bucket statistics returned", source="macie_tool")
            return result_text

        elif action == "get_finding_statistics":
            print(f"[LTTM:Macie] Calling get_finding_statistics", flush=True)
            kwargs = {"groupBy": "severity.description"}
            criteria = _build_finding_criteria(severity_min, finding_type, start_time, end_time, bucket_name)
            if criteria:
                kwargs["findingCriteria"] = criteria
            response = client.get_finding_statistics(**kwargs)
            counts = response.get("countsByGroup", [])
            if not counts:
                msg = f"No finding statistics available for account {account_id} ({label}), region {region}."
                print(f"[LTTM:Macie] {msg}", flush=True)
                emit_status("Macie query complete — no finding statistics", source="macie_tool")
                return msg
            lines = [f"--- Macie Finding Statistics ---\nAccount: {account_id} ({label})\nRegion: {region}\n"]
            for group in counts:
                group_key = group.get("groupKey", "N/A")
                count = group.get("count", 0)
                lines.append(f"  {group_key}: {count}")
            print(f"[LTTM:Macie] Returning finding statistics", flush=True)
            emit_status("Macie query complete — finding statistics returned", source="macie_tool")
            return "\n".join(lines)

        elif action == "search_resources":
            print(f"[LTTM:Macie] Calling search_resources", flush=True)
            kwargs = {"maxResults": max_results}
            if bucket_name:
                kwargs["bucketCriteria"] = {
                    "includes": {
                        "and": [
                            {"simpleCriterion": {"key": "S3_BUCKET_NAME", "comparator": "EQ", "values": [bucket_name]}}
                        ]
                    }
                }
            response = client.search_resources(**kwargs)
            resources = response.get("matchingResources", [])
            if not resources:
                msg = f"No matching resources found in Macie for account {account_id} ({label}), region {region}."
                if bucket_name:
                    msg = f"No resources matching bucket '{bucket_name}' found in Macie for account {account_id} ({label}), region {region}."
                print(f"[LTTM:Macie] {msg}", flush=True)
                emit_status("Macie query complete — 0 resources", source="macie_tool")
                return msg
            lines = [f"Macie Resources — account {account_id} ({label}), region {region}:\n"]
            for r in resources:
                resource = r.get("matchingBucket", {})
                name = resource.get("bucketName", "N/A")
                score = resource.get("sensitivityScore", "N/A")
                obj_count = resource.get("classifiableObjectCount", "N/A")
                lines.append(f"  - {name} (sensitivity: {score}, classifiable objects: {obj_count})")
            count = len(resources)
            print(f"[LTTM:Macie] Returning {count} resource(s)", flush=True)
            emit_status(f"Macie query complete — {count} resource(s)", source="macie_tool")
            return "\n".join(lines)

        else:
            criteria = _build_finding_criteria(severity_min, finding_type, start_time, end_time, bucket_name)

            all_finding_ids = []
            next_token = None
            while True:
                kwargs = {
                    "maxResults": 50,
                    "sortCriteria": {"attributeName": "updatedAt", "orderBy": "DESC"},
                }
                if criteria:
                    kwargs["findingCriteria"] = criteria
                if next_token:
                    kwargs["nextToken"] = next_token

                print(f"[LTTM:Macie] Calling list_findings (collected={len(all_finding_ids)})", flush=True)
                response = client.list_findings(**kwargs)

                finding_ids = response.get("findingIds", [])
                all_finding_ids.extend(finding_ids)

                next_token = response.get("nextToken")
                if not next_token:
                    break

            total_count = len(all_finding_ids)

            if not all_finding_ids:
                msg = f"No Macie findings found for the given filters in account {account_id} ({label}), region {region}."
                print(f"[LTTM:Macie] {msg}", flush=True)
                emit_status("Macie query complete — 0 findings", source="macie_tool")
                return msg

            truncated = total_count > max_results
            ids_to_fetch = all_finding_ids[:max_results]

            all_findings = []
            for i in range(0, len(ids_to_fetch), 50):
                batch = ids_to_fetch[i : i + 50]
                print(f"[LTTM:Macie] Calling get_findings for batch of {len(batch)}", flush=True)
                details_resp = client.get_findings(findingIds=batch)
                all_findings.extend(details_resp.get("findings", []))

            formatted = [_format_finding(f, account_id) for f in all_findings]
            count = len(all_findings)
            print(
                f"[LTTM:Macie] Returning {count} finding(s) for account {account_id} (total: {total_count})",
                flush=True,
            )
            emit_status(f"Macie query complete — {count} finding(s)", source="macie_tool")

            severities = [_severity_label(f["severity"].get("score", 0) if isinstance(f.get("severity"), dict)
                                          else f.get("severity")) for f in all_findings]
            header = count_line(severities, ["High", "Medium", "Low"], total_count)
            result_text = header + "\n\n" + "\n\n".join(formatted)

            if truncated:
                result_text += (
                    f"\n\n--- NOTICE ---\n"
                    f"Showing {count} of {total_count} total findings. "
                    f"Due to token limitations, a maximum of {max_results} findings can be returned per query. "
                    f"To see more, narrow your search using severity_min, finding_type, or time range filters."
                )

            return result_text

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "AccessDeniedException" and "not enabled" in error_msg.lower():
            msg = f"Macie is not enabled in account {account_id} ({label}), region {region}."
        elif error_code == "AccessDeniedException":
            msg = (
                f"Insufficient permissions to query Macie in account {account_id} ({label}). "
                f"Ensure the execution role has macie2:List*/Get*/Describe*/Search* permissions."
            )
        elif error_code == "BadRequestException":
            msg = f"Invalid request parameters: {error_msg}"
        elif error_code == "InternalServerErrorException":
            msg = f"Macie service error: {error_msg}"
        else:
            msg = f"Error querying Macie: {error_code}: {error_msg}"

        print(f"[LTTM:Macie] ClientError: {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        if "AssumeRole" in str(e) or "LTTMMacieReadRole" in str(e):
            msg = (
                f"Failed to assume cross-account role LTTMMacieReadRole in account "
                f"{account_id} ({label}). Verify the role exists and the trust policy "
                f"allows assumption. Error: {error_type}: {str(e)}"
            )
        else:
            msg = f"Error querying Macie: {error_type}: {str(e)}"

        print(f"[LTTM:Macie] {msg}", flush=True)
        return msg


@tool
def query_macie_findings(
    account_id: str,
    action: str = "list_findings",
    severity_min: float = 0.0,
    finding_type: str = "",
    bucket_name: str = "",
    start_time: str = "",
    end_time: str = "",
    max_results: int = 20,
    region: str = "",
) -> str:
    """
    Query Amazon Macie sensitive data findings for a specific AWS account.

    Calls Macie boto3 APIs to retrieve sensitive data findings, bucket profiles,
    resource statistics, or finding statistics. Handles cross-account access via
    STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — list_findings, describe_buckets, list_resource_profiles,
                get_bucket_statistics, get_finding_statistics, or search_resources.
        severity_min: Minimum severity filter (0.0 = no filter, 1.0 = Low+, 2.0 = Medium+, 3.0 = High).
        finding_type: Finding type filter (e.g. "SensitiveData:S3Object/Personal"). Empty = all types.
        bucket_name: S3 bucket name filter for list_findings, get_finding_statistics, describe_buckets and search_resources. Empty = all buckets.
        start_time: ISO 8601 start time filter for updatedAt. Empty = no start filter.
        end_time: ISO 8601 end time filter for updatedAt. Empty = no end filter.
        max_results: Maximum number of findings to return per region (default: 20).
        region: AWS region. Leave empty (default) to query every region where Macie
            runs; set it only when the user names a region.

    Returns:
        Formatted string with finding data, bucket profiles, or an error message.
        With several regions, one block per region, each headed with its region.
    """
    if region and region not in _vars.MACIE_REGIONS:
        return (f"Macie is not enabled in {region} for the LTTM accounts; it runs in "
                f"{', '.join(_vars.MACIE_REGIONS)}.")
    regions = [region] if region else _vars.MACIE_REGIONS
    results = [
        _query_macie_region(account_id, action, severity_min, finding_type, bucket_name,
                            start_time, end_time, max_results, r)
        for r in regions
    ]
    return region_blocks(regions, results, f"Account {account_id} ({_get_account_label(account_id)})", NO_FINDINGS)
