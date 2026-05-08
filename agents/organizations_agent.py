# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
organizations_agent.py — AWS Organizations sub-agent for org structure analysis.

Registered on supervisor_agent.py as @tool query_organizations.

API-based agent — calls AWS Organizations boto3 APIs directly through `query_organizations_api` (DescribeOrganization, ListAccounts, ListPolicies, ListOrganizationalUnitsForParent, etc.). 
No Athena, Glue, or S3 infrastructure needed.

Management-account only — all calls run with the execution role's default credentials. 
No cross-account STS role assumption needed because Organizations is a management-account-only service by design. 
The Organizations API is global — region is irrelevant.

Supports hierarchy walking: DescribeOrganization → list_ous_for_parent → list_accounts_for_parent, recursively down the OU tree to build a full organizational structure view.

Uses Claude Sonnet for routing and action selection; the actual org data comes straight from the AWS API.
"""

import json
import boto3
from botocore.exceptions import ClientError
from strands import tool
from utils.sse_emitter import emit_status
import utils.agent_vars as _vars


def _get_org_client():
    """Return a boto3 Organizations client. No region needed — global service."""
    return boto3.client("organizations")


def _format_account(account: dict) -> str:
    """Format a single account dict into a human-readable text block."""
    return (
        f"--- Account ---\n"
        f"ID: {account.get('Id', 'N/A')}\n"
        f"Name: {account.get('Name', 'N/A')}\n"
        f"Email: {account.get('Email', 'N/A')}\n"
        f"Status: {account.get('Status', 'N/A')}\n"
        f"Joined: {account.get('JoinedTimestamp', 'N/A')}\n"
        f"Method: {account.get('JoinedMethod', 'N/A')}"
    )


def _format_ou(ou: dict) -> str:
    """Format a single OU dict into a human-readable text block."""
    return (
        f"--- Organizational Unit ---\n"
        f"ID: {ou.get('Id', 'N/A')}\n"
        f"ARN: {ou.get('Arn', 'N/A')}\n"
        f"Name: {ou.get('Name', 'N/A')}"
    )


def _format_policy(policy: dict, include_content: bool = False) -> str:
    """Format a single policy dict into a human-readable text block."""
    lines = [
        "--- Policy ---",
        f"ID: {policy.get('Id', 'N/A')}",
        f"Name: {policy.get('Name', 'N/A')}",
        f"Description: {policy.get('Description', 'N/A')}",
        f"Type: {policy.get('Type', 'N/A')}",
        f"AWS Managed: {policy.get('AwsManaged', False)}",
    ]
    if include_content:
        content = policy.get('Content', '')
        if content:
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
                lines.append(f"Content:\n{json.dumps(parsed, indent=2)}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"Content: {content}")
    return "\n".join(lines)


def _format_org(org: dict) -> str:
    """Format organization info into a human-readable text block."""
    policy_types = org.get("AvailablePolicyTypes", [])
    policy_str = ", ".join(
        f"{pt.get('Type', 'N/A')} ({pt.get('Status', 'N/A')})"
        for pt in policy_types
    ) if policy_types else "None"

    return (
        f"--- Organization ---\n"
        f"ID: {org.get('Id', 'N/A')}\n"
        f"ARN: {org.get('Arn', 'N/A')}\n"
        f"Master Account: {org.get('MasterAccountId', 'N/A')}\n"
        f"Master Email: {org.get('MasterAccountEmail', 'N/A')}\n"
        f"Feature Set: {org.get('FeatureSet', 'N/A')}\n"
        f"Available Policy Types: {policy_str}"
    )


@tool
def query_organizations_api(
    account_id: str = _vars.ACC1_ID,
    action: str = "list_accounts",
    region: str = "us-east-1",
    parent_id: str = "",
    target_id: str = "",
    policy_id: str = "",
    policy_type: str = "SERVICE_CONTROL_POLICY",
    max_results: int = 100,
) -> str:
    """
    Query AWS Organizations structure for a specific AWS account.

    Calls the AWS Organizations boto3 API to retrieve organizational data: accounts, OUs, policies, and hierarchy. 
    Management-account only — no cross-account role assumption needed. Organizations API is global.

    Args:
        account_id: AWS account ID (default: management account). For interface consistency only — Organizations API always uses management account credentials.
        action: API action — describe_organization, list_accounts (default), list_ous_for_parent, describe_ou, list_accounts_for_parent, list_policies, list_policies_for_target, or describe_policy.
        region: AWS region (default: us-east-1). Organizations API is global — this parameter is for interface consistency only.
        parent_id: Parent ID (root or OU) for list_ous_for_parent and list_accounts_for_parent.
        target_id: Target ID (root, OU, or account) for list_policies_for_target and describe_ou.
        policy_id: Policy ID for describe_policy.
        policy_type: Policy type filter: SERVICE_CONTROL_POLICY (default), TAG_POLICY, BACKUP_POLICY, AISERVICES_OPT_OUT_POLICY.
        max_results: Maximum number of items to return (default: 100).

    Returns:
        Formatted string with organizational data, or an error message.
    """

    emit_status(f"Querying Organizations ({action})...", source="organizations_tool")

    try:
        client = _get_org_client()

        if action == "describe_organization":
            response = client.describe_organization()
            org = response.get("Organization", {})
            result = _format_org(org)
            print(f"[LTTM:Organizations] Returning organization info", flush=True)
            emit_status("Organizations query complete", source="organizations_tool")
            return result

        elif action == "list_accounts":
            all_accounts = []
            next_token = None
            while True:
                kwargs = {"MaxResults": min(max_results - len(all_accounts), 20)}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = client.list_accounts(**kwargs)
                all_accounts.extend(response.get("Accounts", []))
                next_token = response.get("NextToken")
                if not next_token or len(all_accounts) >= max_results:
                    break
            if not all_accounts:
                return "No accounts found in the organization."
            formatted = [_format_account(a) for a in all_accounts]
            count = len(all_accounts)
            print(f"[LTTM:Organizations] Returning {count} accounts", flush=True)
            emit_status(f"Organizations query complete — {count} accounts", source="organizations_tool")
            return "\n\n".join(formatted)

        elif action == "list_ous_for_parent":
            if not parent_id:
                return "Error: parent_id is required for list_ous_for_parent."
            all_ous = []
            next_token = None
            while True:
                kwargs = {"ParentId": parent_id, "MaxResults": min(max_results - len(all_ous), 20)}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = client.list_organizational_units_for_parent(**kwargs)
                all_ous.extend(response.get("OrganizationalUnits", []))
                next_token = response.get("NextToken")
                if not next_token or len(all_ous) >= max_results:
                    break
            if not all_ous:
                return f"No organizational units found under parent {parent_id}."
            formatted = [_format_ou(ou) for ou in all_ous]
            count = len(all_ous)
            print(f"[LTTM:Organizations] Returning {count} OUs for parent {parent_id}", flush=True)
            emit_status(f"Organizations query complete — {count} OUs", source="organizations_tool")
            return "\n\n".join(formatted)

        elif action == "describe_ou":
            if not target_id:
                return "Error: target_id (OU ID) is required for describe_ou."
            response = client.describe_organizational_unit(OrganizationalUnitId=target_id)
            ou = response.get("OrganizationalUnit", {})
            result = _format_ou(ou)
            print(f"[LTTM:Organizations] Returning OU details for {target_id}", flush=True)
            emit_status("Organizations query complete", source="organizations_tool")
            return result

        elif action == "list_accounts_for_parent":
            if not parent_id:
                return "Error: parent_id is required for list_accounts_for_parent."
            all_accounts = []
            next_token = None
            while True:
                kwargs = {"ParentId": parent_id, "MaxResults": min(max_results - len(all_accounts), 20)}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = client.list_accounts_for_parent(**kwargs)
                all_accounts.extend(response.get("Accounts", []))
                next_token = response.get("NextToken")
                if not next_token or len(all_accounts) >= max_results:
                    break
            if not all_accounts:
                return f"No accounts found under parent {parent_id}."
            formatted = [_format_account(a) for a in all_accounts]
            count = len(all_accounts)
            print(f"[LTTM:Organizations] Returning {count} accounts for parent {parent_id}", flush=True)
            emit_status(f"Organizations query complete — {count} accounts", source="organizations_tool")
            return "\n\n".join(formatted)

        elif action == "list_policies":
            all_policies = []
            next_token = None
            while True:
                kwargs = {"Filter": policy_type, "MaxResults": min(max_results - len(all_policies), 20)}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = client.list_policies(**kwargs)
                all_policies.extend(response.get("Policies", []))
                next_token = response.get("NextToken")
                if not next_token or len(all_policies) >= max_results:
                    break
            if not all_policies:
                return f"No policies of type {policy_type} found."
            formatted = [_format_policy(p) for p in all_policies]
            count = len(all_policies)
            print(f"[LTTM:Organizations] Returning {count} policies of type {policy_type}", flush=True)
            emit_status(f"Organizations query complete — {count} policies", source="organizations_tool")
            return "\n\n".join(formatted)

        elif action == "list_policies_for_target":
            if not target_id:
                return "Error: target_id is required for list_policies_for_target."
            all_policies = []
            next_token = None
            while True:
                kwargs = {"TargetId": target_id, "Filter": policy_type,
                          "MaxResults": min(max_results - len(all_policies), 20)}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = client.list_policies_for_target(**kwargs)
                all_policies.extend(response.get("Policies", []))
                next_token = response.get("NextToken")
                if not next_token or len(all_policies) >= max_results:
                    break
            if not all_policies:
                return f"No policies of type {policy_type} attached to target {target_id}."
            formatted = [_format_policy(p) for p in all_policies]
            count = len(all_policies)
            print(f"[LTTM:Organizations] Returning {count} policies for target {target_id}", flush=True)
            emit_status(f"Organizations query complete — {count} policies", source="organizations_tool")
            return "\n\n".join(formatted)

        elif action == "describe_policy":
            if not policy_id:
                return "Error: policy_id is required for describe_policy."
            response = client.describe_policy(PolicyId=policy_id)
            policy_summary = response.get("Policy", {}).get("PolicySummary", {})
            content = response.get("Policy", {}).get("Content", "")
            policy_data = {**policy_summary, "Content": content}
            result = _format_policy(policy_data, include_content=True)
            print(f"[LTTM:Organizations] Returning policy details for {policy_id}", flush=True)
            emit_status("Organizations query complete", source="organizations_tool")
            return result

        else:
            return (f"Error: Unknown action '{action}'. Valid actions: describe_organization, "
                    f"list_accounts, list_ous_for_parent, describe_ou, list_accounts_for_parent, "
                    f"list_policies, list_policies_for_target, describe_policy.")

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "AccessDeniedException":
            msg = ("Error: AccessDenied: Insufficient permissions to query Organizations. "
                   "Ensure the execution role has organizations:Describe* and "
                   "organizations:List* permissions on lttm-agent-role.")
        elif error_code == "AWSOrganizationsNotInUseException":
            msg = ("Error: AWSOrganizationsNotInUse: This account is not part of an AWS Organization. "
                   "AWS Organizations must be enabled to use this agent.")
        elif error_code in ("TargetNotFoundException", "ParentNotFoundException",
                            "OrganizationalUnitNotFoundException"):
            msg = f"Error: {error_code}: {error_msg}"
        elif error_code == "PolicyNotFoundException":
            msg = f"Error: PolicyNotFound: {error_msg}"
        else:
            msg = f"Error: {error_code}: {error_msg}"

        print(f"[LTTM:Organizations] {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        msg = f"Error: {error_type}: {str(e)}"
        print(f"[LTTM:Organizations] {msg}", flush=True)
        return msg
