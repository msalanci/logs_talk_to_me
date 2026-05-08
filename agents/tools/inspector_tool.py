# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
inspector_tool.py — Strands @tool for querying Amazon Inspector v2 vulnerability findings.

Registered on the inspector_agent as @tool query_inspector_findings.

Calls Inspector v2 boto3 APIs directly (ListFindings, BatchGetFindingDetails, ListCoverage, ListFindingAggregations). 
No Athena/Glue/S3 infrastructure needed — pure API-based tool.

Supports cross-account queries via STS AssumeRole into `LTTMInspectorReadRole` for the account 2 and account 3 member accounts. 
Uses default execution-role credentials for the account 1 (main) account.

Inspector is regional — default region is `eu-central-1` (primary LTTM region).
The tool also supports `us-east-1` and `us-west-2` via the `region` parameter.

Key difference from Macie: Inspector does not need automated discovery configuration. 
Once enabled via `aws_inspector2_enabler`, Inspector automatically scans eligible resources (EC2, Lambda, ECR).

boto3 client: `inspector2` (NOT legacy inspector v1).
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool
from utils.sse_emitter import emit_status
import utils.agent_vars as _vars

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMInspectorReadRole"
LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID


def _get_account_label(account_id: str) -> str:
    """Return human-readable label for an account ID."""
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_inspector_client(account_id: str, region: str = "eu-central-1"):
    """
    Return a boto3 inspector2 client for the given account and region.

    For the main account (matching `MAIN_ACCOUNT_ID`), returns a client built from the execution role's default credentials. 
    For any other account, calls STS AssumeRole against `LTTMInspectorReadRole` in that account and returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account ID, no role assumption happens.
        region: AWS region for the client, defaults to `eu-central-1`.

    Returns:
        A boto3 `inspector2` client scoped to `account_id` and `region`.
    """
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("inspector2", region_name=region)

    role_arn = CROSS_ACCOUNT_ROLE_TEMPLATE.format(account_id=account_id)
    label = _get_account_label(account_id)
    print(f"[LTTM:Inspector] Assuming role {role_arn} for {label} account", flush=True)

    sts = boto3.client("sts")
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"lttm-inspector-{account_id}",
    )["Credentials"]

    return boto3.client(
        "inspector2",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _severity_to_numeric(severity: str) -> int:
    """Map Inspector severity string to numeric value for filtering.

    Inspector severity model: INFORMATIONAL=1, LOW=2, MEDIUM=3, HIGH=4, CRITICAL=5.
    """
    mapping = {
        "INFORMATIONAL": 1,
        "LOW": 2,
        "MEDIUM": 3,
        "HIGH": 4,
        "CRITICAL": 5,
    }
    return mapping.get(severity.upper(), 0) if severity else 0


def _build_filter_criteria(
    severity_min: str,
    finding_type: str,
    resource_type: str,
    resource_id: str,
    title_filter: str,
    start_time: str,
    end_time: str,
) -> dict:
    """
    Build the Inspector `FilterCriteria` dict from parameters.

    Assembles the Inspector-specific filter shape: each filter key maps to a list of `{"comparison": "EQUALS", "value": ...}` entries. 
    Severity is expanded into every severity at-or-above the minimum (e.g. `severity_min="HIGH"` becomes `["HIGH", "CRITICAL"]`). 
    Time filters use the `firstObservedAt` field with paired `startInclusive`/`endInclusive` entries.

    Args:
        severity_min: Minimum severity — one of `INFORMATIONAL`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`, empty string disables.
        finding_type: Finding-type equality filter (e.g. `PACKAGE_VULNERABILITY`), empty string disables.
        resource_type: Resource-type equality filter (e.g. `AWS_EC2_INSTANCE`), empty string disables.
        resource_id: Resource ID equality filter, empty string disables.
        title_filter: Finding title equality filter (typically a CVE ID), empty string disables.
        start_time: Lower bound on `firstObservedAt`, empty string disables.
        end_time: Upper bound on `firstObservedAt`, empty string disables.

    Returns:
        A dict of shape `{"filterCriteria": {...}}` ready to merge into `list_findings` kwargs. 
        Returns an empty dict `{}` when no filter conditions were provided.
    """
    filter_criteria = {}

    if severity_min:
        severities_at_or_above = []
        min_numeric = _severity_to_numeric(severity_min)
        for sev, num in [("INFORMATIONAL", 1), ("LOW", 2), ("MEDIUM", 3), ("HIGH", 4), ("CRITICAL", 5)]:
            if num >= min_numeric:
                severities_at_or_above.append(sev)
        if severities_at_or_above:
            filter_criteria["severity"] = [
                {"comparison": "EQUALS", "value": s} for s in severities_at_or_above
            ]

    if finding_type:
        filter_criteria["findingType"] = [
            {"comparison": "EQUALS", "value": finding_type}
        ]

    if resource_type:
        filter_criteria["resourceType"] = [
            {"comparison": "EQUALS", "value": resource_type}
        ]

    if resource_id:
        filter_criteria["resourceId"] = [
            {"comparison": "EQUALS", "value": resource_id}
        ]

    if title_filter:
        filter_criteria["title"] = [
            {"comparison": "EQUALS", "value": title_filter}
        ]

    if start_time:
        filter_criteria["firstObservedAt"] = filter_criteria.get("firstObservedAt", [])
        filter_criteria["firstObservedAt"].append({
            "startInclusive": start_time,
        })

    if end_time:
        if "firstObservedAt" not in filter_criteria:
            filter_criteria["firstObservedAt"] = []
        filter_criteria["firstObservedAt"].append({
            "endInclusive": end_time,
        })

    if not filter_criteria:
        return {}
    return {"filterCriteria": filter_criteria}


def _format_finding(finding: dict, account_id: str) -> str:
    """
    Format a single Inspector finding dict into a human-readable text block.

    Includes: account ID and label, finding type, severity level, CVSS score,  title (CVE ID), resource type and ID, affected package name and version,
    fixed version (if available), exploit availability, network reachability details (if applicable), first observed date, and last observed date.
    """
    label = _get_account_label(account_id)
    finding_type = finding.get("type", "N/A")
    severity = finding.get("severity", "N/A")
    title = finding.get("title", "N/A")

    inspector_score = finding.get("inspectorScore", "N/A")
    score_details = finding.get("inspectorScoreDetails", {})
    cvss_info = score_details.get("adjustedCvss", {})
    cvss_score = cvss_info.get("score", inspector_score) if cvss_info else inspector_score

    resources = finding.get("resources", [])
    resource_type = "N/A"
    resource_id = "N/A"
    if resources:
        resource_type = resources[0].get("type", "N/A")
        resource_id = resources[0].get("id", "N/A")

    pkg_vuln = finding.get("packageVulnerabilityDetails", {})
    package_name = "N/A"
    package_version = "N/A"
    fixed_version = "N/A"
    exploit_available = "N/A"

    if pkg_vuln:
        exploit_available = pkg_vuln.get("exploitAvailable", "N/A")
        vuln_packages = pkg_vuln.get("vulnerablePackages", [])
        if vuln_packages:
            package_name = vuln_packages[0].get("name", "N/A")
            package_version = vuln_packages[0].get("version", "N/A")
            fixed_version = vuln_packages[0].get("fixedInVersion", "N/A")

    net_reach = finding.get("networkReachabilityDetails", {})

    first_observed = finding.get("firstObservedAt", "N/A")
    last_observed = finding.get("lastObservedAt", "N/A")

    lines = [
        "--- Inspector Finding ---",
        f"Account: {account_id} ({label})",
        f"Finding Type: {finding_type}",
        f"Severity: {severity} (CVSS: {cvss_score})",
        f"Title: {title}",
        f"Resource Type: {resource_type}",
        f"Resource ID: {resource_id}",
        f"Package: {package_name} {package_version}",
        f"Fixed Version: {fixed_version}",
        f"Exploit Available: {exploit_available}",
    ]

    if net_reach:
        protocol = net_reach.get("protocol", "N/A")
        port_range = net_reach.get("openPortRange", {})
        port_begin = port_range.get("begin", "N/A")
        port_end = port_range.get("end", "N/A")
        lines.append(f"Network: {protocol} ports {port_begin}-{port_end}")

    lines.extend([
        f"First Observed: {first_observed}",
        f"Last Observed: {last_observed}",
    ])

    return "\n".join(lines)


def _format_coverage(coverage: dict, account_id: str) -> str:
    """Format a single Inspector coverage entry into a human-readable text block.

    Includes: resource type, resource ID, scan status, and scan type.
    """
    label = _get_account_label(account_id)
    resource_type = coverage.get("resourceType", "N/A")
    resource_id = coverage.get("resourceId", "N/A")
    scan_type = coverage.get("scanType", "N/A")

    scan_status = coverage.get("scanStatus", {})
    status_code = "N/A"
    status_reason = "N/A"
    if isinstance(scan_status, dict):
        status_code = scan_status.get("statusCode", "N/A")
        status_reason = scan_status.get("reason", "N/A")

    lines = [
        "--- Inspector Coverage ---",
        f"Account: {account_id} ({label})",
        f"Resource Type: {resource_type}",
        f"Resource ID: {resource_id}",
        f"Scan Type: {scan_type}",
        f"Scan Status: {status_code} ({status_reason})",
    ]

    return "\n".join(lines)


@tool
def query_inspector_findings(
    account_id: str,
    action: str = "list_findings",
    severity_min: str = "",
    finding_type: str = "",
    resource_type: str = "",
    resource_id: str = "",
    title_filter: str = "",
    start_time: str = "",
    end_time: str = "",
    max_results: int = 20,
    region: str = "eu-central-1",
) -> str:
    """
    Query Amazon Inspector v2 vulnerability findings for a specific AWS account.

    Calls Inspector v2 boto3 APIs to retrieve vulnerability findings, resource coverage status, finding statistics, or detailed finding information.
    Handles cross-account access via STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — list_findings, list_coverage, get_finding_statistics, or batch_get_finding_details.
        severity_min: Minimum severity filter (INFORMATIONAL, LOW, MEDIUM, HIGH, CRITICAL), empty = no filter.
        finding_type: Finding type filter (PACKAGE_VULNERABILITY, CODE_VULNERABILITY, NETWORK_REACHABILITY), empty = all types.
        resource_type: Resource type filter (AWS_EC2_INSTANCE, AWS_LAMBDA_FUNCTION, AWS_ECR_CONTAINER_IMAGE, AWS_ECR_REPOSITORY), empty = all types.
        resource_id: Specific resource ID filter, empty = all resources.
        title_filter: CVE ID search filter, empty = no filter.
        start_time: Start time filter, empty = no start filter.
        end_time: end time filter, empty = no end filter.
        max_results: Maximum number of findings to return, defaults to 20.
        region: AWS region, defaults to eu-central-1).

    Returns:
        Formatted string with finding data, coverage info, statistics, or an error message.
    """
    label = _get_account_label(account_id)

    emit_status(
        f"Querying Inspector in account {account_id} ({label}), region {region}...",
        source="inspector_tool",
    )

    try:
        client = _get_inspector_client(account_id, region)

        if action == "list_coverage":
            print(f"[LTTM:Inspector] Calling list_coverage", flush=True)
            kwargs = {"maxResults": max_results}
            response = client.list_coverage(**kwargs)
            covered = response.get("coveredResources", [])
            if not covered:
                msg = f"No coverage data found for account {account_id} ({label}), region {region}."
                print(f"[LTTM:Inspector] {msg}", flush=True)
                emit_status("Inspector query complete — 0 coverage entries", source="inspector_tool")
                return msg
            formatted = [_format_coverage(c, account_id) for c in covered]
            count = len(covered)
            print(f"[LTTM:Inspector] Returning {count} coverage entry/entries", flush=True)
            emit_status(f"Inspector query complete — {count} coverage entry/entries", source="inspector_tool")
            return "\n\n".join(formatted)

        elif action == "get_finding_statistics":
            print(f"[LTTM:Inspector] Calling list_finding_aggregations", flush=True)
            kwargs = {
                "aggregationType": "FINDING_TYPE",
            }
            filter_dict = _build_filter_criteria(
                severity_min, finding_type, resource_type, resource_id, title_filter, start_time, end_time
            )
            if filter_dict:
                kwargs["filterCriteria"] = filter_dict.get("filterCriteria", {})

            response = client.list_finding_aggregations(**kwargs)
            aggregations = response.get("responses", [])
            if not aggregations:
                msg = f"No finding statistics available for account {account_id} ({label}), region {region}."
                print(f"[LTTM:Inspector] {msg}", flush=True)
                emit_status("Inspector query complete — no finding statistics", source="inspector_tool")
                return msg

            lines = [
                f"--- Inspector Finding Statistics ---",
                f"Account: {account_id} ({label})",
                f"Region: {region}\n",
            ]
            for agg in aggregations:
                finding_type_agg = agg.get("findingTypeAggregation", {})
                if finding_type_agg:
                    agg_type = finding_type_agg.get("findingType", "N/A")
                    sev_counts = finding_type_agg.get("severityCounts", {})
                    critical = sev_counts.get("critical", 0)
                    high = sev_counts.get("high", 0)
                    medium = sev_counts.get("medium", 0)
                    info = sev_counts.get("informational", 0)
                    total = sev_counts.get("all", critical + high + medium + info)
                    lines.append(
                        f"  {agg_type}: total={total} "
                        f"(critical={critical}, high={high}, medium={medium}, informational={info})"
                    )
                else:
                    # Fallback for other aggregation types
                    lines.append(f"  {agg}")

            print(f"[LTTM:Inspector] Returning finding statistics", flush=True)
            emit_status("Inspector query complete — finding statistics returned", source="inspector_tool")
            return "\n".join(lines)

        elif action == "batch_get_finding_details":
            # title_filter is used as comma-separated finding ARNs for this action
            if not title_filter:
                return "Error: title_filter parameter must contain comma-separated finding ARNs for batch_get_finding_details action."
            finding_arns = [arn.strip() for arn in title_filter.split(",") if arn.strip()]
            print(f"[LTTM:Inspector] Calling batch_get_finding_details for {len(finding_arns)} finding(s)", flush=True)
            response = client.batch_get_finding_details(findingDetails=[
                {"findingArn": arn} for arn in finding_arns
            ])
            findings = response.get("findingDetails", [])
            if not findings:
                msg = "No finding details found for the given ARNs."
                print(f"[LTTM:Inspector] {msg}", flush=True)
                emit_status("Inspector query complete — 0 finding details", source="inspector_tool")
                return msg
            formatted = [_format_finding(f, account_id) for f in findings]
            count = len(findings)
            print(f"[LTTM:Inspector] Returning {count} finding detail(s)", flush=True)
            emit_status(f"Inspector query complete — {count} finding detail(s)", source="inspector_tool")
            return "\n\n".join(formatted)

        else:
            filter_dict = _build_filter_criteria(
                severity_min, finding_type, resource_type, resource_id, title_filter, start_time, end_time
            )

            all_findings = []
            next_token = None
            while True:
                kwargs = {
                    "maxResults": 100,
                    "sortCriteria": {"field": "FIRST_OBSERVED_AT", "sortOrder": "DESC"},
                }
                if filter_dict:
                    kwargs["filterCriteria"] = filter_dict.get("filterCriteria", {})
                if next_token:
                    kwargs["nextToken"] = next_token

                print(f"[LTTM:Inspector] Calling list_findings (collected={len(all_findings)})", flush=True)
                response = client.list_findings(**kwargs)

                findings = response.get("findings", [])
                all_findings.extend(findings)

                next_token = response.get("nextToken")
                if not next_token:
                    break

            total_count = len(all_findings)

            if not all_findings:
                msg = f"No Inspector findings found for the given filters in account {account_id} ({label}), region {region}."
                print(f"[LTTM:Inspector] {msg}", flush=True)
                emit_status("Inspector query complete — 0 findings", source="inspector_tool")
                return msg

            truncated = total_count > max_results
            findings_to_show = all_findings[:max_results]

            formatted = [_format_finding(f, account_id) for f in findings_to_show]
            count = len(findings_to_show)
            print(
                f"[LTTM:Inspector] Returning {count} finding(s) for account {account_id} (total: {total_count})",
                flush=True,
            )
            emit_status(f"Inspector query complete — {count} finding(s)", source="inspector_tool")

            result_text = "\n\n".join(formatted)

            if truncated:
                result_text += (
                    f"\n\n--- NOTICE ---\n"
                    f"Showing {count} of {total_count} total findings. "
                    f"Due to token limitations, a maximum of {max_results} findings can be returned per query. "
                    f"To see more, narrow your search using severity_min, finding_type, resource_type, or time range filters."
                )

            return result_text

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "AccessDeniedException":
            msg = (
                f"Insufficient permissions to query Inspector in account {account_id} ({label}). "
                f"Ensure the execution role has inspector2:List*/BatchGet* permissions."
            )
        elif error_code == "BadRequestException":
            msg = f"Invalid request parameters: {error_msg}"
        elif error_code == "InternalServerException":
            msg = f"Inspector service error: {error_msg}"
        else:
            msg = f"Error querying Inspector: {error_code}: {error_msg}"

        print(f"[LTTM:Inspector] ClientError: {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        if "AssumeRole" in str(e) or "LTTMInspectorReadRole" in str(e):
            msg = (
                f"Failed to assume cross-account role LTTMInspectorReadRole in account "
                f"{account_id} ({label}). Verify the role exists and the trust policy "
                f"allows assumption. Error: {error_type}: {str(e)}"
            )
        else:
            msg = f"Error querying Inspector: {error_type}: {str(e)}"

        print(f"[LTTM:Inspector] {msg}", flush=True)
        return msg
