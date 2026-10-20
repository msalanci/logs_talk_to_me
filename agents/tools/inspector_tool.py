# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
inspector_tool.py — Strands @tool for querying Amazon Inspector v2 vulnerability findings.

Registered on the inspector_agent as @tool query_inspector_findings.

Calls Inspector v2 boto3 APIs directly (ListFindings, BatchGetFindingDetails,
ListCoverage, ListFindingAggregations). No Athena/Glue/S3 infrastructure needed
— pure API-based tool.

Supports cross-account queries via STS AssumeRole into `LTTMInspectorReadRole`
for the account 2 and account 3 member accounts. Uses default execution-role
credentials for the account 1 (main) account.

Inspector is regional. With no region given, the tool queries every region in
`INSPECTOR_REGIONS` (agents/utils/agent_vars.py) and labels each block of
results with its region, under one total for all of them, added up by code.
A region outside that list is answered by code, without a call: there
ListFindings returns 0 findings and no error, which would read as "clean".

Key difference from Macie: Inspector does not need automated discovery
configuration. Once enabled via `aws_inspector2_enabler`, Inspector
automatically scans eligible resources (EC2, Lambda, ECR).

boto3 client: `inspector2` (NOT legacy inspector v1).
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool

from utils.sse_emitter import emit_status
from utils.finding_counts import count_line, region_blocks

NO_FINDINGS = ("No Inspector findings found",)

import utils.agent_vars as _vars
from utils.untrusted_text import one_line_args

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMInspectorReadRole"

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


def _get_inspector_client(account_id: str, region: str = _vars.DEFAULT_REGION):
    """Return a boto3 inspector2 client for the given account and region.

    For the main account (matching `MAIN_ACCOUNT_ID`), returns a client
    built from the execution role's default credentials. For any other
    account, calls STS AssumeRole against `LTTMInspectorReadRole` in that
    account and returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account
            ID, no role assumption happens.
        region: AWS region for the client. Defaults to `DEFAULT_REGION`.

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

    Args:
        severity: A severity name, in any case.

    Returns:
        Its number, 1 to 5; 0 for an empty or unknown severity.
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
    """Build the Inspector `FilterCriteria` dict from parameters.

    Assembles the Inspector-specific filter shape: each filter key maps to
    a list of `{"comparison": "EQUALS", "value": ...}` entries. Severity is
    expanded into every severity at-or-above the minimum (e.g.
    `severity_min="HIGH"` becomes `["HIGH", "CRITICAL"]`). The time window is
    one `firstObservedAt` entry holding both `startInclusive` and
    `endInclusive`: entries of one list are OR'ed, so two entries would filter
    nothing. A CVE or GHSA ID matches each finding's `vulnerabilityId`; titles
    read "<id> - <packages>", so an exact title never matches.

    Args:
        severity_min: Minimum severity — one of `INFORMATIONAL`, `LOW`,
            `MEDIUM`, `HIGH`, `CRITICAL`. Empty string disables.
        finding_type: Finding-type equality filter (e.g.
            `PACKAGE_VULNERABILITY`). Empty string disables.
        resource_type: Resource-type equality filter (e.g.
            `AWS_EC2_INSTANCE`). Empty string disables.
        resource_id: Resource ID equality filter. Empty string disables.
        title_filter: A CVE or GHSA ID, matched against each finding's
            vulnerability ID. Empty string disables.
        start_time: ISO 8601 lower bound on `firstObservedAt`. Empty
            string disables.
        end_time: ISO 8601 upper bound on `firstObservedAt`. Empty string
            disables.

    Returns:
        A dict of shape `{"filterCriteria": {...}}` ready to merge into
        `list_findings` kwargs. Returns an empty dict `{}` when no filter
        conditions were provided.
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
        filter_criteria["vulnerabilityId"] = [
            {"comparison": "EQUALS", "value": title_filter.strip()}
        ]

    if start_time or end_time:
        window = {}
        if start_time:
            window["startInclusive"] = start_time
        if end_time:
            window["endInclusive"] = end_time
        filter_criteria["firstObservedAt"] = [window]

    if not filter_criteria:
        return {}
    return {"filterCriteria": filter_criteria}


@one_line_args
def _format_finding(finding: dict, account_id: str) -> str:
    """Format a single Inspector finding dict into a human-readable text block.

    Includes: account ID and label, finding type, severity level, CVSS score,
    title (CVE ID), resource type and ID, affected package name and version,
    fixed version (if available), exploit availability, network reachability
    details (if applicable), first observed date, and last observed date.
    Every value is put on one line (one_line_args).

    Args:
        finding: A finding as ListFindings or BatchGetFindingDetails returns it.
        account_id: The account the finding was read in.

    Returns:
        The "--- Inspector Finding ---" block, with "N/A" where a value is missing.
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
        f"Status: {finding.get('status', 'N/A')}",
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


@one_line_args
def _format_coverage(coverage: dict, account_id: str) -> str:
    """Format a single Inspector coverage entry into a human-readable text block.

    Includes: resource type, resource ID, scan status, and scan type.
    Every value is put on one line (one_line_args).

    Args:
        coverage: A covered resource as ListCoverage returns it.
        account_id: The account the coverage was read in.

    Returns:
        The "--- Inspector Coverage ---" block, with "N/A" where a value is missing.
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


def _query_inspector_region(
    account_id: str,
    action: str,
    severity_min: str,
    finding_type: str,
    resource_type: str,
    resource_id: str,
    title_filter: str,
    start_time: str,
    end_time: str,
    max_results: int,
    finding_status: str,
    region: str,
) -> str:
    """Run one Inspector query in one region; `query_inspector_findings` calls it per region.

    list_findings gets full finding objects from ListFindings, every page, newest first observed first, and is the
    only action that filters on status. get_finding_statistics calls ListFindingAggregations, which has no
    filterCriteria and fails with any: finding and resource type go into its aggregation request, and the result
    names the other filters as not applied. A findings list starts with a count line over every match, stated by
    code so the answer quotes it instead of counting.

    Args:
        account_id: The account to query.
        action: list_coverage, get_finding_statistics, batch_get_finding_details, or anything else for
            list_findings.
        severity_min: The lowest severity name; "" for all.
        finding_type: A finding type; "" for all.
        resource_type: A resource type; "" for all.
        resource_id: A resource ID; "" for all.
        title_filter: A CVE or GHSA ID; "" for all. For batch_get_finding_details, the finding ARNs, separated by
            commas.
        start_time: ISO 8601 start of the first-observed window; "" for none.
        end_time: ISO 8601 end of the first-observed window; "" for none.
        max_results: The most findings or coverage entries to return.
        finding_status: ACTIVE, SUPPRESSED, CLOSED, or ALL (or "") for every status; list_findings only.
        region: The region to query.

    Returns:
        The coverage, the statistics, or the findings as text; a message starting with NO_FINDINGS's prefix when
        the region has no findings; or another message or an error message. Never raises. Also sends status
        lines to the CLI.
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
            aggregation = {key: value for key, value in (("findingType", finding_type), ("resourceType", resource_type)) if value}
            if aggregation:
                kwargs["aggregationRequest"] = {"findingTypeAggregation": aggregation}
            not_applied = [name for name, value in (("severity_min", severity_min), ("resource_id", resource_id),
                                                    ("title_filter", title_filter), ("start_time", start_time),
                                                    ("end_time", end_time)) if value]

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
                    lines.append(f"  {agg}")

            if not_applied:
                lines.append(f"\nNote: statistics cannot be filtered by {', '.join(not_applied)}; these counts "
                             f"include all findings. Use list_findings for filtered counts.")
            print(f"[LTTM:Inspector] Returning finding statistics", flush=True)
            emit_status("Inspector query complete — finding statistics returned", source="inspector_tool")
            return "\n".join(lines)

        elif action == "batch_get_finding_details":
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
            if finding_status and finding_status.upper() != "ALL":
                criteria = filter_dict.setdefault("filterCriteria", {})
                criteria["findingStatus"] = [{"comparison": "EQUALS", "value": finding_status.upper()}]

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

            header = count_line([f.get("severity", "UNKNOWN") for f in findings_to_show],
                                ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL", "UNTRIAGED"], total_count)
            result_text = header + "\n\n" + "\n\n".join(formatted)

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
    finding_status: str = "ACTIVE",
    region: str = "",
) -> str:
    """
    Query Amazon Inspector v2 vulnerability findings for a specific AWS account.

    Calls Inspector v2 boto3 APIs to retrieve vulnerability findings, resource
    coverage status, finding statistics, or detailed finding information.
    Handles cross-account access via STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — list_findings, list_coverage, get_finding_statistics,
                or batch_get_finding_details.
        severity_min: Minimum severity filter (INFORMATIONAL, LOW, MEDIUM, HIGH, CRITICAL).
                      Empty = no filter.
        finding_type: Finding type filter (PACKAGE_VULNERABILITY, CODE_VULNERABILITY,
                      NETWORK_REACHABILITY). Empty = all types.
        resource_type: Resource type filter (AWS_EC2_INSTANCE, AWS_LAMBDA_FUNCTION,
                       AWS_ECR_CONTAINER_IMAGE, AWS_ECR_REPOSITORY). Empty = all types.
        resource_id: Specific resource ID filter. Empty = all resources.
        title_filter: CVE or GHSA ID (e.g., "CVE-2021-44228"), matched against each finding's
            vulnerability ID. Empty = no filter. For batch_get_finding_details: comma-separated finding ARNs.
        start_time: ISO 8601 start of the first-observed window. Empty = no start filter.
        end_time: ISO 8601 end of the first-observed window. Empty = no end filter.
            get_finding_statistics can filter only by finding_type and resource_type.
        max_results: Maximum number of findings to return per region (default: 20).
        finding_status: Status filter for list_findings: ACTIVE (default), SUPPRESSED,
            CLOSED, or ALL. Use CLOSED or ALL only when the user asks for closed findings.
        region: AWS region. Leave empty (default) to query every region where Inspector
            runs; set it only when the user names a region.

    Returns:
        Formatted string with finding data, coverage info, statistics, or an error message.
        With several regions, one block per region, each headed with its region.
    """
    if region and region not in _vars.INSPECTOR_REGIONS:
        return (f"Amazon Inspector is not enabled in {region} for the LTTM accounts; it runs in "
                f"{', '.join(_vars.INSPECTOR_REGIONS)}. No findings can exist there.")
    regions = [region] if region else _vars.INSPECTOR_REGIONS
    results = [
        _query_inspector_region(account_id, action, severity_min, finding_type, resource_type,
                                resource_id, title_filter, start_time, end_time, max_results,
                                finding_status, r)
        for r in regions
    ]
    return region_blocks(regions, results, f"Account {account_id} ({_get_account_label(account_id)})", NO_FINDINGS)
