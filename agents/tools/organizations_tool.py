# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
organizations_tool.py — Strands @tool for querying AWS Organizations structure.

Registered on the organizations_agent as @tool query_organizations_api.

Calls AWS Organizations boto3 APIs directly (DescribeOrganization, ListAccounts,
ListPolicies, ListOrganizationalUnitsForParent, etc.). No Athena/Glue/S3
infrastructure needed — pure API-based tool.

Management-account only — no cross-account STS role assumption needed.
Organizations API is global (no region parameter required); the `region`
parameter on the tool exists for interface consistency only.

Two answers are made in code:
- policies_in_effect walks from an account or OU up to the root in code and lists every policy attached on the way,
  each once, with where it is attached: no single API call answers "which SCPs apply to dev, including inherited
  ones".
- An SCP named aws-guardrails-… is marked, in code, as managed by AWS Control Tower: Control Tower creates the SCPs
  of its preventive controls under that name. The policy stays Organizations data; the Control Tower sub-agent
  shows the control.
"""

import json
import boto3
from botocore.exceptions import ClientError
from strands import tool

from utils.sse_emitter import emit_status

import utils.agent_vars as _vars
from utils.untrusted_text import one_line, one_line_all, one_line_args
from utils.empty_note import NOTE

CONTROL_TOWER_SCP_PREFIX = "aws-guardrails-"
MAX_PATH = 7


def _get_org_client():
    """Return a boto3 Organizations client. No region needed — global service.

    Args:
        None.

    Returns:
        A boto3 organizations client with the execution role's own credentials.
    """
    return boto3.client("organizations")


@one_line_args
def _format_account(account: dict) -> str:
    """Format a single account dict into a human-readable text block.

    Every value is put on one line (one_line_args).

    Args:
        account: An account as ListAccounts or ListAccountsForParent returns it.

    Returns:
        The "--- Account ---" block, with "N/A" where a value is missing.
    """
    return (
        f"--- Account ---\n"
        f"ID: {account.get('Id', 'N/A')}\n"
        f"Name: {account.get('Name', 'N/A')}\n"
        f"Email: {account.get('Email', 'N/A')}\n"
        f"Status: {account.get('Status', 'N/A')}\n"
        f"Joined: {account.get('JoinedTimestamp', 'N/A')}\n"
        f"Method: {account.get('JoinedMethod', 'N/A')}"
    )


@one_line_args
def _format_ou(ou: dict) -> str:
    """Format a single OU dict into a human-readable text block.

    Every value is put on one line (one_line_args).

    Args:
        ou: An OU as ListOrganizationalUnitsForParent or DescribeOrganizationalUnit returns it.

    Returns:
        The "--- Organizational Unit ---" block, with "N/A" where a value is missing.
    """
    return (
        f"--- Organizational Unit ---\n"
        f"ID: {ou.get('Id', 'N/A')}\n"
        f"ARN: {ou.get('Arn', 'N/A')}\n"
        f"Name: {ou.get('Name', 'N/A')}"
    )


def _format_policy(policy: dict, include_content: bool = False) -> str:
    """Format a single policy dict into a human-readable text block.

    Every value is kept on one line. The policy content is printed as indented JSON:
    json.dumps writes a line break inside a value as \\n, so the indentation lines are the JSON's own.
    An SCP named CONTROL_TOWER_SCP_PREFIX… is marked as managed by AWS Control Tower.

    Args:
        policy: A policy summary, with its "Content" for describe_policy.
        include_content: True to print the policy's content too.

    Returns:
        The "--- Policy ---" block, with "N/A" where a value is missing.
    """
    content = policy.get('Content', '')
    policy = one_line_all({key: value for key, value in policy.items() if key != 'Content'})
    lines = [
        "--- Policy ---",
        f"ID: {policy.get('Id', 'N/A')}",
        f"Name: {policy.get('Name', 'N/A')}",
        f"Description: {policy.get('Description', 'N/A')}",
        f"Type: {policy.get('Type', 'N/A')}",
        f"AWS Managed: {policy.get('AwsManaged', False)}",
    ]
    if str(policy.get('Name', '')).startswith(CONTROL_TOWER_SCP_PREFIX):
        lines.append(f"Managed by: AWS Control Tower (named {CONTROL_TOWER_SCP_PREFIX}…, as Control Tower names the "
                     f"SCPs of its controls)")
    if include_content:
        if content:
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
                lines.append(f"Content:\n{json.dumps(parsed, indent=2)}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"Content: {one_line(content)}")
    return "\n".join(lines)


@one_line_args
def _format_org(org: dict) -> str:
    """Format organization info into a human-readable text block.

    Every value is put on one line (one_line_args).

    Args:
        org: The organization as DescribeOrganization returns it.

    Returns:
        The "--- Organization ---" block, with "N/A" where a value is missing.
    """
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


def _kind(target_id: str) -> str:
    """Tell what an Organizations ID is, from its form.

    Args:
        target_id: A root ("r-…"), OU ("ou-…") or account ID.

    Returns:
        "ROOT", "ORGANIZATIONAL_UNIT" or "ACCOUNT".
    """
    if target_id.startswith("r-"):
        return "ROOT"
    return "ORGANIZATIONAL_UNIT" if target_id.startswith("ou-") else "ACCOUNT"


def _label(client, target_id: str, kind: str) -> str:
    """Label a root, OU or account: "222222222222 (dev)", "OU Workloads (ou-…)" or "Root (r-…)".

    Args:
        client: The organizations client, to read an OU's name.
        target_id: The ID to label.
        kind: Its kind, from _kind or the API.

    Returns:
        The label; an OU whose name can't be read, and an account LTTM does not know, by ID only.
    """
    if kind == "ROOT":
        return f"Root ({target_id})"
    if kind == "ORGANIZATIONAL_UNIT":
        try:
            name = client.describe_organizational_unit(OrganizationalUnitId=target_id)["OrganizationalUnit"].get("Name")
        except ClientError:
            name = ""
        return f"OU {one_line(name)} ({target_id})" if name else f"OU {target_id}"
    label = _vars.ACCOUNTS.get(target_id, {}).get("label")
    return f"{target_id} ({label})" if label else target_id


def _path_to_root(client, target_id: str) -> list[tuple[str, str]]:
    """Walk from a target up to the root.

    An account sits under at most 5 levels of OUs, so the walk stops after MAX_PATH steps whatever the API says.

    Args:
        client: The organizations client.
        target_id: The account or OU to start from.

    Returns:
        The target and each of its parents up to the root, as (ID, kind), the target first; the last one is not
        the root when the walk stopped early.
    """
    path = [(target_id, _kind(target_id))]
    while path[-1][1] != "ROOT" and len(path) < MAX_PATH:
        parents = [p for page in client.get_paginator("list_parents").paginate(ChildId=path[-1][0])
                   for p in page.get("Parents", [])]
        if not parents:
            break
        path.append((parents[0]["Id"], parents[0].get("Type") or _kind(parents[0]["Id"])))
    return path


def _scp_rule(target_id: str, kind: str, target: str) -> str:
    """Say how the SCPs of a path combine, for the target's kind (AWS Organizations User Guide, SCP evaluation).

    Args:
        target_id: The target's ID; ACC1_ID is the management account, which SCPs do not restrict.
        kind: The target's kind.
        target: The target's label.

    Returns:
        One sentence for the management account, an account, or an OU.
    """
    if target_id == _vars.ACC1_ID:
        return f"SCPs do not restrict the management account: none of these policies limits {target}."
    if kind == "ACCOUNT":
        return (f"An action is allowed in {target} only if an SCP attached at every level of this path allows it, and "
                f"no SCP on the path denies it.")
    return (f"For an account under {target}, an action is allowed only if an SCP attached at every level from the root "
            f"down to the account itself allows it, and no SCP on that path denies it; the account's own SCPs are not "
            f"in this list.")


def _policies_in_effect(client, target_id: str, policy_type: str) -> str:
    """List every policy of a type attached to a target or to a parent up to the root, each once, with where it is
    attached; the counts and the path are written by code.

    Args:
        client: The organizations client.
        target_id: An account (its ID, or main, dev, prod) or an OU ID.
        policy_type: The policy type, such as SERVICE_CONTROL_POLICY.

    Returns:
        A count line, the path, for SCPs a note on how they combine, then one block per policy with where it is
        attached (directly or inherited); a "No policies of type … attached …" line with the path when there are
        none.
    """
    by_label = {info["label"]: account for account, info in _vars.ACCOUNTS.items()}
    target_id = by_label.get(target_id.strip().lower(), target_id.strip())
    path = _path_to_root(client, target_id)
    labels = {ident: _label(client, ident, kind) for ident, kind in path}
    found = {}
    for ident, _ in path:
        for page in client.get_paginator("list_policies_for_target").paginate(TargetId=ident, Filter=policy_type):
            for policy in page.get("Policies", []):
                found.setdefault(policy["Id"], (policy, []))[1].append(ident)
    target = labels[target_id]
    route = " → ".join(labels[ident] for ident, _ in path)
    if path[-1][1] != "ROOT":
        route += " (the walk stopped before the root)"
    if not found:
        return f"No policies of type {policy_type} attached to {target} or to any parent up to the root.\nPath: {route}"
    lines = [f"Policies in effect for {target}: {len(found)} of type {policy_type} (counted by code over {target} "
             f"and every parent up to the root; each policy once)", f"Path: {route}"]
    if policy_type == "SERVICE_CONTROL_POLICY":
        lines.append(f"{NOTE} {_scp_rule(target_id, path[0][1], target)}")
    blocks = []
    for policy, where in found.values():
        attached = ", ".join(f"{labels[ident]} ({'directly' if ident == target_id else 'inherited'})" for ident in where)
        blocks.append(f"{_format_policy(policy)}\nAttached to: {one_line(attached)}")
    return "\n".join(lines) + "\n\n" + "\n\n".join(blocks)


@tool
def query_organizations_api(
    account_id: str = _vars.ACC1_ID,
    action: str = "list_accounts",
    region: str = _vars.GLOBAL_SERVICE_REGION,
    parent_id: str = "",
    target_id: str = "",
    policy_id: str = "",
    policy_type: str = "SERVICE_CONTROL_POLICY",
    max_results: int = 100,
) -> str:
    """
    Query AWS Organizations structure for a specific AWS account.

    Calls the AWS Organizations boto3 API to retrieve organizational data: accounts,
    OUs, policies, and hierarchy. Management-account only — no cross-account
    role assumption needed. Organizations API is global.

    Args:
        account_id: AWS account ID (default: management account). For interface consistency only — Organizations API always uses management account credentials.
        action: API action — describe_organization, list_accounts (default), list_roots, list_parents, list_ous_for_parent, describe_ou, list_accounts_for_parent, list_policies, list_policies_for_target, policies_in_effect, or describe_policy. policies_in_effect: the policies that apply to an account or OU, inherited ones included (one call walks up to the root).
        region: AWS region (default: us-east-1). Organizations API is global — this parameter is for interface consistency only.
        parent_id: Parent ID (root or OU) for list_ous_for_parent and list_accounts_for_parent.
        target_id: Target ID (root, OU, or account) for list_policies_for_target and describe_ou; the child (OU or account) for list_parents; the account (its ID, or main, dev, prod) or OU ID for policies_in_effect.
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

        elif action == "list_roots":
            roots = [r for p in client.get_paginator("list_roots").paginate() for r in p.get("Roots", [])]
            if not roots:
                return "No roots found in the organization."
            lines = []
            for root in roots:
                policy_types = ", ".join(f"{t.get('Type')} ({t.get('Status')})" for t in root.get("PolicyTypes", [])) or "None"
                lines.append(f"--- Root ---\nID: {root.get('Id', 'N/A')}\nName: {root.get('Name', 'N/A')}\n"
                             f"ARN: {root.get('Arn', 'N/A')}\nEnabled policy types: {policy_types}")
            print(f"[LTTM:Organizations] Returning {len(roots)} root(s)", flush=True)
            emit_status(f"Organizations query complete — {len(roots)} root(s)", source="organizations_tool")
            return "\n\n".join(lines)

        elif action == "list_parents":
            if not target_id:
                return "Error: target_id (the OU or account ID) is required for list_parents."
            parents = [p for page in client.get_paginator("list_parents").paginate(ChildId=target_id)
                       for p in page.get("Parents", [])]
            if not parents:
                return f"No parent found for {target_id}."
            lines = [f"--- Parent of {target_id} ---\nID: {p.get('Id', 'N/A')}\nType: {p.get('Type', 'N/A')}" for p in parents]
            print(f"[LTTM:Organizations] Returning the parent of {target_id}", flush=True)
            emit_status("Organizations query complete", source="organizations_tool")
            return "\n\n".join(lines)

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

        elif action == "policies_in_effect":
            if not target_id:
                return "Error: target_id (the account or OU) is required for policies_in_effect."
            result = _policies_in_effect(client, target_id, policy_type)
            print(f"[LTTM:Organizations] Returning the {policy_type} policies in effect for {target_id}", flush=True)
            emit_status("Organizations query complete", source="organizations_tool")
            return result

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
                    f"list_accounts, list_roots, list_parents, list_ous_for_parent, describe_ou, "
                    f"list_accounts_for_parent, list_policies, list_policies_for_target, policies_in_effect, "
                    f"describe_policy.")

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
