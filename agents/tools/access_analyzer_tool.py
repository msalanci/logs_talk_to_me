# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
access_analyzer_tool.py — Strands @tool for querying IAM Access Analyzer findings.

Registered on the access_analyzer_agent as @tool query_access_analyzer_api.
Calls IAM Access Analyzer boto3 APIs directly (ListAnalyzers, ListFindingsV2).
No Athena/Glue/S3 infrastructure needed — pure API-based tool.

Supports cross-account queries via STS AssumeRole into`LTTMAccessAnalyzerReadRole` 
for the account 2 and account 3 member accounts.
Uses default execution-role credentials for the account 1 (main) account.
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool
from utils.sse_emitter import emit_status
import utils.agent_vars as _vars

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMAccessAnalyzerReadRole"
LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID

_analyzer_cache: dict[tuple[str, str], str] = {}

def _get_account_label(account_id: str) -> str:
    """Return human-readable label for an account ID"""
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_aa_client(account_id: str, region: str = "eu-central-1"):
    """
    Return a boto3 accessanalyzer client for the given account.

    For the main account (matching `MAIN_ACCOUNT_ID`), returns a client built from the execution 
    role's default credentials. For any other account, calls STS AssumeRole against `LTTMAccessAnalyzerReadRole` 
    in that account and returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account ID, no role assumption happens
        region: AWS region for the client. Defaults to `eu-central-1`

    Returns:
        A boto3 `accessanalyzer` client scoped to `account_id` and `region`.
    """
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("accessanalyzer", region_name=region)

    # Cross-account: assume role
    role_arn = CROSS_ACCOUNT_ROLE_TEMPLATE.format(account_id=account_id)
    label = _get_account_label(account_id)
    print(f"[LTTM:AccessAnalyzer] Assuming role {role_arn} for {label} account", flush=True)

    sts = boto3.client("sts")
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"lttm-aa-{account_id}",
    )["Credentials"]

    return boto3.client(
        "accessanalyzer",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _discover_analyzer_arn(client, account_id: str, region: str) -> str:
    """
    Discover and cache the Access Analyzer ARN for an (account, region) pair.

    Calls `list_analyzers(type="ACCOUNT")` and returns the ARN of the first analyzer found. 
    Subsequent calls for the same `(account_id, region)` return the cached value without hitting the API.

    Args:
        client: boto3 `accessanalyzer` client already scoped to the target account, obtained from `_get_aa_client()`.
        account_id: AWS Accound ID 
        region: AWS region

    Returns:
        The ARN of the first analyzer in the account/region.

    Raises:
        ValueError: No analyzer is configured for this account/region.
                    The error message includes a ready-to-copy `aws accessanalyzer create-analyzer` command so the caller can surface it to the user.
    """

    cache_key = (account_id, region)
    if cache_key in _analyzer_cache:
        return _analyzer_cache[cache_key]

    print(f"[LTTM:AccessAnalyzer] Discovering analyzer in account {account_id}, region {region}", flush=True)
    response = client.list_analyzers(type="ACCOUNT")
    analyzers = response.get("analyzers", [])

    if not analyzers:
        raise ValueError(
            f"No Access Analyzer configured for account {account_id} in region {region}. "
            f"Create one with: aws accessanalyzer create-analyzer "
            f"--analyzer-name lttm-analyzer --type ACCOUNT --region {region}"
        )

    arn = analyzers[0]["arn"]
    _analyzer_cache[cache_key] = arn
    print(f"[LTTM:AccessAnalyzer] Found analyzer: {arn}", flush=True)
    return arn


def _build_filter(resource_type: str, finding_status: str, resource: str = "") -> dict:
    """Build the filter criteria dict for list_findings_v2."""
    criteria = {}
    if finding_status:
        criteria["status"] = {"eq": [finding_status]}
    if resource_type:
        criteria["resourceType"] = {"eq": [resource_type]}
    if resource:
        criteria["resource"] = {"contains": [resource]}
    return criteria


def _format_finding(finding: dict, account_id: str) -> str:
    """Format a single finding dict into a human-readable text block."""
    label = _get_account_label(account_id)
    resource = finding.get("resource", "N/A")
    res_type = finding.get("resourceType", "N/A")
    status = finding.get("status", "N/A")
    created = finding.get("createdAt", "N/A")
    updated = finding.get("analyzedAt", "N/A")
    principal = finding.get("principal", {})
    
    if isinstance(principal, dict):
        principal_str = ", ".join(f"{k}: {v}" for k, v in principal.items()) if principal else "N/A"
    else:
        principal_str = str(principal) if principal else "N/A"

    condition = finding.get("condition", {})
    
    if isinstance(condition, dict):
        condition_str = ", ".join(f"{k} = {v}" for k, v in condition.items()) if condition else "None"
    else:
        condition_str = str(condition) if condition else "None"

    actions = finding.get("action", [])
    access_str = ", ".join(actions) if actions else "N/A"
    severity = finding.get("severity", "N/A")

    if isinstance(severity, dict):
        severity = severity.get("text", "N/A")

    return (
        f"--- Finding ---\n"
        f"Account: {account_id} ({label})\n"
        f"Resource: {resource}\n"
        f"Resource Type: {res_type}\n"
        f"External Principal: {principal_str}\n"
        f"Condition: {condition_str}\n"
        f"Access Level: {access_str}\n"
        f"Status: {status}\n"
        f"Severity: {severity}\n"
        f"Created: {created}\n"
    )


@tool
def query_access_analyzer_api(
    account_id: str,
    action: str = "list_findings",
    region: str = "eu-central-1",
    resource_type: str = "",
    resource: str = "",
    finding_status: str = "ACTIVE",
    max_results: int = 100,
) -> str:
    """
    Query IAM Access Analyzer for a specific AWS account.

    Calls the IAM Access Analyzer boto3 API to retrieve external access findings
    or list analyzers. Handles cross-account access via STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — list_findings (default) or list_analyzers.
        region: AWS region (default: eu-central-1).
        resource_type: Access Analyzer resource type filter (e.g. AWS::S3::Bucket). Empty = all types.
        resource: Resource ARN or partial ARN to filter findings (e.g. arn:aws:s3:::lttm-datalake or lttm-datalake). Uses 'contains' matching. Empty = all resources.
        finding_status: Finding status filter: ACTIVE, ARCHIVED, or RESOLVED (default: ACTIVE).
        max_results: Maximum number of items to return (default: 100).

    Returns:
        Formatted string with findings or analyzers, or an error message.
    """
    label = _get_account_label(account_id)

    emit_status(f"Querying Access Analyzer in account {account_id} ({label})...",
                source="access_analyzer_tool")

    try:
        client = _get_aa_client(account_id, region)

        if action == "list_analyzers":
            print(f"[LTTM:AccessAnalyzer] Calling list_analyzers", flush=True)
            response = client.list_analyzers(type="ACCOUNT")
            analyzers = response.get("analyzers", [])
            if not analyzers:
                msg = (f"No Access Analyzer configured for account {account_id} in region {region}. "
                       f"Create one with: aws accessanalyzer create-analyzer "
                       f"--analyzer-name lttm-analyzer --type ACCOUNT --region {region}")
                print(f"[LTTM:AccessAnalyzer] {msg}", flush=True)
                emit_status("Access Analyzer query complete — 0 analyzers found",
                            source="access_analyzer_tool")
                return msg
            lines = []
            for a in analyzers[:max_results]:
                lines.append(
                    f"--- Analyzer ---\n"
                    f"ARN: {a.get('arn', 'N/A')}\n"
                    f"Name: {a.get('name', 'N/A')}\n"
                    f"Type: {a.get('type', 'N/A')}\n"
                    f"Status: {a.get('status', 'N/A')}\n"
                    f"Created: {a.get('createdAt', 'N/A')}"
                )
            count = len(lines)
            print(f"[LTTM:AccessAnalyzer] Returning {count} analyzers for account {account_id}", flush=True)
            emit_status(f"Access Analyzer query complete — {count} analyzers returned",
                        source="access_analyzer_tool")
            return "\n\n".join(lines)

        analyzer_arn = _discover_analyzer_arn(client, account_id, region)
        filter_criteria = _build_filter(resource_type, finding_status, resource)
        all_findings = []
        next_token = None

        while True:
            kwargs = {
                "analyzerArn": analyzer_arn,
                "maxResults": min(max_results - len(all_findings), 100),
            }
            if filter_criteria:
                kwargs["filter"] = filter_criteria
            if next_token:
                kwargs["nextToken"] = next_token

            print(f"[LTTM:AccessAnalyzer] Calling list_findings_v2 (collected={len(all_findings)})", flush=True)
            response = client.list_findings_v2(**kwargs)

            findings = response.get("findings", [])
            all_findings.extend(findings)

            next_token = response.get("nextToken")
            if not next_token or len(all_findings) >= max_results:
                break

        if not all_findings:
            msg = "No external access findings found for the given filters."
            print(f"[LTTM:AccessAnalyzer] {msg}", flush=True)
            emit_status(f"Access Analyzer query complete — 0 findings returned",
                        source="access_analyzer_tool")
            return msg

        formatted = [_format_finding(f, account_id) for f in all_findings]
        result = "\n".join(formatted)

        count = len(all_findings)
        print(f"[LTTM:AccessAnalyzer] Returning {count} findings for account {account_id}", flush=True)
        emit_status(f"Access Analyzer query complete — {count} findings returned",
                    source="access_analyzer_tool")
        return result

    except ValueError as e:
        # No analyzer found
        msg = str(e)
        print(f"[LTTM:AccessAnalyzer] {msg}", flush=True)
        return msg

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "AccessDeniedException":
            msg = (f"Error: AccessDenied: Insufficient permissions to query Access Analyzer "
                   f"in account {account_id}. Ensure the execution role has "
                   f"access-analyzer:List* and access-analyzer:Get* permissions.")
        elif error_code == "ResourceNotFoundException":
            msg = (f"Error: ResourceNotFound: No analyzer exists in account {account_id}, "
                   f"region {region}. Create one with: aws accessanalyzer create-analyzer "
                   f"--analyzer-name lttm-analyzer --type ACCOUNT --region {region}")
        elif error_code == "ValidationException":
            msg = f"Error: ValidationException: {error_msg}"
        else:
            msg = f"Error: {error_code}: {error_msg}"

        print(f"[LTTM:AccessAnalyzer] {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        msg = f"Error: {error_type}: {str(e)}"
        print(f"[LTTM:AccessAnalyzer] {msg}", flush=True)
        return msg
