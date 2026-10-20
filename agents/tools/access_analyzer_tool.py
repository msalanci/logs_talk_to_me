# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
access_analyzer_tool.py — Strands @tool for querying IAM Access Analyzer findings.

Registered on the access_analyzer_agent as @tool query_access_analyzer_api.

Calls the IAM Access Analyzer APIs directly with boto3: ListAnalyzers, ListFindingsV2, and GetFinding per
finding, because the list summary has no principal, condition or actions. No Athena, Glue or S3 is involved.

Reads the ORGANIZATION analyzer `ACCESS_ANALYZER_NAME` in the main account with the execution role's own
credentials; no role is assumed. Its zone of trust is the organization, so access between main, dev and prod is
never a finding. One analyzer covers all three accounts; the tool keeps the findings of the requested accounts
through the `resourceOwnerAccount` filter. Organization IDs in a finding's condition are marked as this
organization or not (DescribeOrganization), so an answer cannot mistake another organization for this one.

One call answers every account and region of a question. Analyzers are regional: with no region given, the tool
queries every region in `ACCESS_ANALYZER_REGIONS` (utils/agent_vars.py) and labels each block of results with its
region. A region outside that list is answered by code, without an API call: Terraform makes the analyzers in
those regions only, by design, and the API's "no analyzer" error there would lead the answer to advise deploying
one. The regions' findings are not added up: an IAM role is global, and Access Analyzer reports an externally
accessible role in every region, so a total would count it once per region.
"""

import re
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError
from strands import tool

from utils.sse_emitter import emit_status
from utils.finding_counts import count_line
import utils.agent_vars as _vars
from utils.untrusted_text import one_line_args

LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}

_analyzer_cache: dict[str, str] = {}


def _get_account_label(account_id: str) -> str:
    """Give an account ID's label, for the text the model reads.

    Args:
        account_id: A 12-digit AWS account ID.

    Returns:
        The account's label from agent_vars ACCOUNTS, such as "main"; the ID itself for an account LTTM does
        not know.
    """
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_aa_client(region: str):
    """Create an Access Analyzer client with the execution role's own credentials.

    Args:
        region: The AWS region to call, such as "eu-central-1".

    Returns:
        A boto3 accessanalyzer client in that region.
    """
    return boto3.client("accessanalyzer", region_name=region)


def _discover_analyzer_arn(client, region: str) -> str:
    """Find the ARN of the organization analyzer `ACCESS_ANALYZER_NAME` in a region.

    The ARN is cached per region, and only once the analyzer is ACTIVE.

    Args:
        client: An accessanalyzer client in `region`.
        region: The region to look in.

    Returns:
        The analyzer's ARN.

    Raises:
        ValueError: The analyzer does not exist in `region`, or it is not ACTIVE
            (while CREATING, DISABLED or FAILED its findings are incomplete).
    """
    if region in _analyzer_cache:
        return _analyzer_cache[region]

    name = _vars.ACCESS_ANALYZER_NAME
    print(f"[LTTM:AccessAnalyzer] Discovering organization analyzer {name} in region {region}", flush=True)
    analyzers = client.list_analyzers(type="ORGANIZATION").get("analyzers", [])
    analyzer = next((a for a in analyzers if a.get("name") == name), None)

    if analyzer is None:
        raise ValueError(
            f"No organization analyzer named {name} in region {region}. "
            f"Terraform creates it (terraform apply)."
        )
    if analyzer.get("status") != "ACTIVE":
        raise ValueError(
            f"The organization analyzer {name} in region {region} is {analyzer.get('status')}, "
            f"not ACTIVE, so its findings are incomplete."
        )

    _analyzer_cache[region] = analyzer["arn"]
    print(f"[LTTM:AccessAnalyzer] Found analyzer: {analyzer['arn']}", flush=True)
    return analyzer["arn"]


def _build_filter(accounts: list, resource_type: str, finding_status: str, resource: str = "") -> dict:
    """Build the filter criteria for ListFindingsV2, always limited to the resources of the given accounts.

    Args:
        accounts: The account IDs whose resources' findings are wanted.
        resource_type: An Access Analyzer resource type, such as "AWS::S3::Bucket"; "" for every type.
        finding_status: ACTIVE, ARCHIVED or RESOLVED; "" for every status.
        resource: A resource ARN or part of one, matched with "contains"; "" for every resource.

    Returns:
        The filter, keyed by finding attribute (resourceOwnerAccount always, the others when given).
    """
    criteria = {"resourceOwnerAccount": {"eq": accounts}}
    if finding_status:
        criteria["status"] = {"eq": [finding_status]}
    if resource_type:
        criteria["resourceType"] = {"eq": [resource_type]}
    if resource:
        criteria["resource"] = {"contains": [resource]}
    return criteria


_ORG_ID = re.compile(r"\bo-[a-z0-9]{10,32}\b")


@lru_cache(maxsize=1)
def _get_organization_id() -> str:
    """Read this organization's ID with DescribeOrganization, once per process.

    Args:
        None.

    Returns:
        The organization ID, such as "o-abcdefghij"; "" when it cannot be read (logged). The result is cached,
        a failed read too, so it is not asked again in this process.
    """
    try:
        client = boto3.client("organizations", region_name=_vars.GLOBAL_SERVICE_REGION)
        return client.describe_organization()["Organization"]["Id"]
    except Exception as e:
        print(f"[LTTM:AccessAnalyzer] Could not read the organization ID: {e}", flush=True)
        return ""


def _org_note(value) -> str:
    """Say whether the organization IDs in a finding's condition value are this organization's.

    An organization ID is "o-" and 10 to 32 lower-case letters or digits.

    Args:
        value: A condition value from a finding: a string, a list, or any value with organization IDs in its text.

    Returns:
        " (this organization)", " (not this organization, which is o-…)", or, with several IDs, the other IDs
        followed by that note; "" when the value holds no organization ID, or this organization's ID can't be read.
    """
    ids = set(_ORG_ID.findall(str(value)))
    org = _get_organization_id() if ids else ""
    if not org:
        return ""
    others = sorted(ids - {org})
    if not others:
        return " (this organization)"
    if len(ids) == 1:
        return f" (not this organization, which is {org})"
    return f" ({', '.join(others)}: not this organization, which is {org})"


def _with_details(client, analyzer_arn: str, finding: dict) -> dict:
    """Add GetFinding's details to a finding summary, which has no principal, condition or actions.

    Args:
        client: The accessanalyzer client in the finding's region.
        analyzer_arn: The ARN of the analyzer that reported the finding.
        finding: One finding summary from ListFindingsV2.

    Returns:
        The summary plus principal, condition, action and isPublic, each one where GetFinding returns it.
    """
    detail = client.get_finding(analyzerArn=analyzer_arn, id=finding["id"]).get("finding", {})
    return {**finding, **{k: detail[k] for k in ("principal", "condition", "action", "isPublic") if k in detail}}


@one_line_args
def _format_finding(finding: dict) -> str:
    """Format one finding as a text block for the model.

    Decorated with one_line_args, so a value with a line break in it stays on one line and can't start a line
    of the block.

    Args:
        finding: A finding summary with GetFinding's details added (_with_details). Its severity is a string, or
            a dict with a "text" key.

    Returns:
        The "--- Finding ---" block: account, resource, type, principal, condition, access level, public, status,
        severity and creation time, with "N/A" or "None" where a value is missing.
    """
    owner = finding.get("resourceOwnerAccount", "N/A")
    label = _get_account_label(owner)
    resource = finding.get("resource", "N/A")
    res_type = finding.get("resourceType", "N/A")
    status = finding.get("status", "N/A")
    created = finding.get("createdAt", "N/A")

    principal = finding.get("principal", {})
    if isinstance(principal, dict):
        principal_str = ", ".join(f"{k}: {v}" for k, v in principal.items()) if principal else "N/A"
    else:
        principal_str = str(principal) if principal else "N/A"

    condition = finding.get("condition", {})
    if isinstance(condition, dict):
        condition_str = ", ".join(f"{k} = {v}{_org_note(v)}" for k, v in condition.items()) if condition else "None"
    else:
        condition_str = str(condition) if condition else "None"

    actions = finding.get("action", [])
    access_str = ", ".join(actions) if actions else "N/A"

    is_public = finding.get("isPublic")
    public_str = "N/A" if is_public is None else ("Yes" if is_public else "No")

    severity = finding.get("severity", "N/A")
    if isinstance(severity, dict):
        severity = severity.get("text", "N/A")

    return (
        f"--- Finding ---\n"
        f"Account: {owner} ({label})\n"
        f"Resource: {resource}\n"
        f"Resource Type: {res_type}\n"
        f"External Principal: {principal_str}\n"
        f"Condition: {condition_str}\n"
        f"Access Level: {access_str}\n"
        f"Public: {public_str}\n"
        f"Status: {status}\n"
        f"Severity: {severity}\n"
        f"Created: {created}\n"
    )


def _query_access_analyzer_region(
    accounts: list,
    action: str,
    region: str,
    resource_type: str,
    resource: str,
    finding_status: str,
    max_results: int,
) -> str:
    """Run one Access Analyzer query in one region for all of the given accounts.

    query_access_analyzer_api calls it once per region. A findings list starts with a count line per account
    (count_line), stated by code so the answer quotes it instead of counting the findings itself.

    Args:
        accounts: The account IDs to report on.
        action: "list_analyzers" for the organization analyzers; anything else for the findings.
        region: The region to query.
        resource_type: An Access Analyzer resource type filter; "" for every type.
        resource: A resource ARN or part of one; "" for every resource.
        finding_status: ACTIVE, ARCHIVED or RESOLVED; "" for every status.
        max_results: The most analyzers or findings to return.

    Returns:
        The analyzers or the findings as text; otherwise a message saying why there are none, or an error
        message. Never raises. Also sends status lines to the CLI.
    """
    label = ", ".join(f"{a} ({_get_account_label(a)})" for a in accounts)

    emit_status(f"Querying Access Analyzer for {label} in {region}...",
                source="access_analyzer_tool")

    try:
        client = _get_aa_client(region)

        if action == "list_analyzers":
            print(f"[LTTM:AccessAnalyzer] Calling list_analyzers", flush=True)
            response = client.list_analyzers(type="ORGANIZATION")
            analyzers = response.get("analyzers", [])
            if not analyzers:
                msg = (f"No organization analyzer in region {region}. "
                       f"Terraform creates {_vars.ACCESS_ANALYZER_NAME} (terraform apply).")
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
            print(f"[LTTM:AccessAnalyzer] Returning {count} analyzers in {region}", flush=True)
            emit_status(f"Access Analyzer query complete — {count} analyzers returned",
                        source="access_analyzer_tool")
            return "\n\n".join(lines)

        analyzer_arn = _discover_analyzer_arn(client, region)

        filter_criteria = _build_filter(accounts, resource_type, finding_status, resource)

        all_findings = []
        next_token = None

        while True:
            kwargs = {
                "analyzerArn": analyzer_arn,
                "maxResults": min(max_results - len(all_findings), 100),
                "filter": filter_criteria,
            }
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

        print(f"[LTTM:AccessAnalyzer] Calling get_finding for {len(all_findings)} findings", flush=True)
        all_findings = [_with_details(client, analyzer_arn, f) for f in all_findings]

        formatted = [_format_finding(f) for f in all_findings]
        header = count_line([_get_account_label(f.get("resourceOwnerAccount", "")) for f in all_findings],
                            list(LTTM_ACCOUNTS.values()))
        result = header + "\n\n" + "\n".join(formatted)

        count = len(all_findings)
        print(f"[LTTM:AccessAnalyzer] Returning {count} findings for accounts {', '.join(accounts)} in {region}", flush=True)
        emit_status(f"Access Analyzer query complete — {count} findings returned",
                    source="access_analyzer_tool")
        return result

    except ValueError as e:
        msg = str(e)
        print(f"[LTTM:AccessAnalyzer] {msg}", flush=True)
        return msg

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "AccessDeniedException":
            msg = (f"Error: AccessDenied: Insufficient permissions to query Access Analyzer "
                   f"in the main account, region {region}. Ensure the execution role has "
                   f"access-analyzer:List* and access-analyzer:Get* permissions.")
        elif error_code == "ResourceNotFoundException":
            msg = (f"Error: ResourceNotFound: No organization analyzer named "
                   f"{_vars.ACCESS_ANALYZER_NAME} in region {region}. "
                   f"Terraform creates it (terraform apply).")
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


@tool
def query_access_analyzer_api(
    account_id: str = "",
    action: str = "list_findings",
    region: str = "",
    resource_type: str = "",
    resource: str = "",
    finding_status: str = "ACTIVE",
    max_results: int = 100,
) -> str:
    """
    Query IAM Access Analyzer for one, several or all LTTM accounts in a single call.

    Calls the IAM Access Analyzer boto3 API to retrieve external access findings
    or list analyzers. One organization analyzer in the main account covers all three accounts; only findings for the accounts in account_id are returned.

    Args:
        account_id: AWS account ID to query; several IDs separated by commas; empty (default) = all three
            accounts. Put every account of the question into this one call — never one call per account.
        action: API action — list_findings (default) or list_analyzers.
        region: AWS region. Leave empty (default) to query every region with an analyzer;
            set it only when the user names a region (several separated by commas).
        resource_type: Access Analyzer resource type filter (e.g. AWS::S3::Bucket). Empty = all types.
        resource: Resource ARN or partial ARN to filter findings (e.g. arn:aws:s3:::lttm-datalake or lttm-datalake). Uses 'contains' matching. Empty = all resources.
        finding_status: Finding status filter: ACTIVE, ARCHIVED, or RESOLVED (default: ACTIVE).
        max_results: Maximum number of items to return per region (default: 100).

    Returns:
        Formatted string with findings or analyzers, or an error message. With several
        regions, one block per region, each headed with its region.
    """
    accounts = [a.strip() for a in account_id.split(",") if a.strip()] or list(_vars.ACCOUNTS)
    regions = [r.strip() for r in region.split(",") if r.strip()] or _vars.ACCESS_ANALYZER_REGIONS
    results = [
        _query_access_analyzer_region(accounts, action, r, resource_type, resource, finding_status, max_results)
        if r in _vars.ACCESS_ANALYZER_REGIONS else
        (f"Access Analyzer is not enabled in {r} for the LTTM accounts; it runs in "
         f"{', '.join(_vars.ACCESS_ANALYZER_REGIONS)}. No findings can exist there.")
        for r in regions
    ]
    if len(results) == 1:
        return results[0]
    return "\n\n".join(f"=== Region {r} ===\n{text}" for r, text in zip(regions, results))
