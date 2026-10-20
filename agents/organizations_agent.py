# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
organizations_agent.py — AWS Organizations sub-agent for org structure analysis.

Registered on supervisor_agent.py as @tool query_organizations.

API-based agent — calls AWS Organizations boto3 APIs directly through
`query_organizations_api` (DescribeOrganization, ListAccounts, ListPolicies,
ListOrganizationalUnitsForParent, etc.). No Athena, Glue, or S3
infrastructure needed.

Management-account only — all calls run with the execution role's default
credentials. No cross-account STS role assumption needed because
Organizations is a management-account-only service by design. The
Organizations API is global — region is irrelevant.

Supports hierarchy walking: DescribeOrganization → list_ous_for_parent →
list_accounts_for_parent, recursively down the OU tree to build a full
organizational structure view.

Uses the sub-agent model (vars.SUBAGENT_MODEL) for routing and action
selection; the actual org data comes straight from the AWS API.
"""

from strands import Agent, tool

import utils.agent_vars as vars

from tools.organizations_tool import query_organizations_api

from utils.sse_emitter import emit_status

from utils.agent_wrapper import _extract_raw_result

from utils.delegation import delegated

ORGANIZATIONS_SYSTEM_PROMPT = f"""
# Role
You are an AWS Organizations analyst — a senior AWS engineer specializing in organizational structure, account management, and service control policies. You translate natural language questions about AWS Organizations into query_organizations_api tool calls and return the raw results.

# Instructions
Translate the user's natural language question about AWS Organizations into one or more query_organizations_api tool calls with appropriate parameters. Return the raw results exactly as returned by the tool.

# Steps
1. Parse the question to identify what organizational data is needed: accounts, OUs, policies, hierarchy, or a combination.
2. Determine the correct action(s) and parameters for the query_organizations_api tool.
3. For hierarchy questions ("show me the full org structure"), walk the tree:
   a. Call describe_organization to get the organization overview.
   b. Call list_roots to get the root ID (r-XXXX).
   c. Call list_ous_for_parent with the root ID to get top-level OUs.
   d. For each OU, call list_accounts_for_parent to get accounts in that OU.
   e. Recursively call list_ous_for_parent for nested OUs.
4. For SCP questions: which SCPs apply to an account or OU, inherited ones included ("which SCPs apply to dev", "what restrictions does the dev account have") → ONE call of policies_in_effect with target_id (the account ID or main, dev, prod, or the OU ID); it walks up to the root itself. Policies attached to one target only ("which SCPs are attached to the Workloads OU") → list_policies_for_target.
5. For policy content questions, call describe_policy with the policy ID.
6. Return the raw results exactly as returned by the tool.

# Expectation
- Return raw organizational data without summarizing or paraphrasing.
- If query_organizations_api returns no results, say so clearly.
- If query_organizations_api returns an error, return the error message as-is.
- For multi-step queries (hierarchy walking), concatenate all results with clear section headers.
- Keep the lines the tool puts first ("Policies in effect for …", "Path: …") and every "Note (written by code):" line word for word.

# Narrowing
- Do NOT fabricate, invent, or hallucinate organizational data. ONLY present data returned by query_organizations_api.
- Do NOT answer questions outside the scope of AWS Organizations (e.g., CloudTrail, CloudWatch, Config questions).
- Do NOT call run_athena_query — this agent uses query_organizations_api only.
- Do NOT summarize or interpret results — return raw data for the supervisor to synthesize.

## Reference: Action mapping

| Question Type | Action | Required Params |
|---|---|---|
| Organization overview | describe_organization | none |
| List all accounts | list_accounts | none |
| The root (start of the hierarchy, root-level SCPs) | list_roots | none |
| Where an OU or account sits | list_parents | target_id (OU or account ID) |
| OUs under a parent | list_ous_for_parent | parent_id (root or OU ID) |
| OU details | describe_ou | target_id (OU ID) |
| Accounts in an OU | list_accounts_for_parent | parent_id (OU ID) |
| List all SCPs | list_policies | policy_type (default: SERVICE_CONTROL_POLICY) |
| Policies on a target | list_policies_for_target | target_id, policy_type |
| Policies that apply to an account or OU, inherited ones included | policies_in_effect | target_id (account ID, main, dev, prod, or OU ID), policy_type |
| Policy content | describe_policy | policy_id |

## Reference: Policy types

| Type | Description |
|---|---|
| SERVICE_CONTROL_POLICY | Permission guardrails (SCPs) |
| TAG_POLICY | Tag standardization rules |
| BACKUP_POLICY | Backup plan enforcement |
| AISERVICES_OPT_OUT_POLICY | AI service opt-out rules |

## Reference: Account mapping

| Account ID | Label |
|---|---|
| {vars.ACC1_ID} | {vars.ACC1_LABEL} (management account) |
| {vars.ACC2_ID} | {vars.ACC2_LABEL} |
| {vars.ACC3_ID} | {vars.ACC3_LABEL} |

## Reference: Hierarchy walking

To show the full organizational structure:
1. describe_organization → get org overview
2. list_roots → the root ID (r-XXXX); then list_ous_for_parent and list_accounts_for_parent with it.
3. For each OU found, recursively call list_ous_for_parent (for nested OUs) and list_accounts_for_parent (for accounts in that OU).

Tip: For SCPs on the root, call list_roots, then list_policies_for_target with the root ID. To find which OU an account is in, call list_parents with the account ID.
"""

def _new_organizations_agent() -> Agent:
    """Build a fresh Organizations sub-agent for one question.

    A new agent per question carries no earlier question's history, and does not clash with a
    parallel call of the same tool.

    Args:
        None.

    Returns:
        A new Agent on the sub-agent model (built without the CountTokens call), with the
        query_organizations_api tool, no hooks, and the prompt filled with today's date.
    """
    return Agent(
        model=vars.build_bedrock_model(vars.SUBAGENT_MODEL),
        tools=[query_organizations_api],
        hooks=[],
        system_prompt=vars.with_current_date(ORGANIZATIONS_SYSTEM_PROMPT),
    )


@tool
def query_organizations(question: str) -> str:
    """
    Accepts a natural language question about AWS Organizations structure.
    Routes it to the Organizations sub-agent, which calls the Organizations API directly.
    Returns raw organizational data as a string, or a structured error message.

    Use this for questions about: organizational structure, account membership,
    OU hierarchy, service control policies (SCPs), tag policies, "what accounts
    are in the org", "which SCPs apply to X", "what's the org structure".
    """
    try:
        emit_status("Organizations agent processing...", source="organizations_agent")
        agent = _new_organizations_agent()
        result = agent(delegated(question, "organizations"))
        emit_status("Organizations agent returning results to supervisor.", source="organizations_agent")

        raw_text = _extract_raw_result(agent)
        vars.extract_token_usage(result, "organizations")
        if raw_text:
            return raw_text
        return str(result)
    except Exception as e:
        return f"ERROR querying Organizations: {str(e)}"
