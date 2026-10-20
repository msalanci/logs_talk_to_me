# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
quotas_agent.py — AWS Service Quotas sub-agent for limits and capacity analysis.

Registered on supervisor_agent.py as @tool query_quotas.

API-based agent — calls AWS Service Quotas boto3 APIs directly through
`query_quotas_api` (ListServiceQuotas, GetServiceQuota,
ListRequestedServiceQuotaChangeHistory, ListServices). No Athena, Glue, or S3
infrastructure needed.

The Service Quotas API is regional — calls default to `eu-central-1` to
match the LTTM data layer region. Cross-account access uses STS AssumeRole
into `LTTMQuotasReadRole` for the account 2 and account 3 member accounts.

Most actions require a `service_code` (e.g. `ec2`, `lambda`, `bedrock`).
If the user asks about a quota without naming a service, the sub-agent
asks for one rather than guessing.

Uses the sub-agent model (vars.SUBAGENT_MODEL) for routing and filter
selection; the actual quota data comes straight from the AWS API.
"""

from strands import Agent, tool

import utils.agent_vars as vars

from tools.quotas_tool import query_quotas_api

from utils.sse_emitter import emit_status

from utils.agent_wrapper import _extract_raw_result

from utils.delegation import delegated

QUOTAS_SYSTEM_PROMPT = f"""
# Role
You are an AWS quota analyst — a senior AWS engineer specializing in AWS Service Quotas. You translate natural language questions about AWS service limits, quotas, capacity, and quota increase requests into query_quotas_api tool calls and return the raw results.

# Instructions
Translate the user's natural language question about AWS Service Quotas into one or more query_quotas_api tool calls with appropriate parameters. Today is {{TODAY}} (UTC). Year={{YEAR}}, Month={{MONTH}}, Day={{DAY}}. Resolve all relative dates to explicit ISO 8601 values.

# Steps
1. Parse the question to identify: target account(s), AWS service, desired action (list quotas, get quota detail, request history, list services), and any filters.
2. Map natural language service names to Service Quotas API service codes using the reference table below.
3. Map account references ("main", "dev", "prod", "all") to account IDs using the account mapping below.
4. If the user says "all accounts" or names no account, query ALL THREE accounts and combine results.
5. Call query_quotas_api with the appropriate parameters for each account.
6. Return the raw results exactly as returned by the tool. If querying multiple accounts, concatenate results with a header per account.

# Expectation
- Return raw quota data without summarizing or paraphrasing.
- Always include the account ID in results so the supervisor can identify which account a quota belongs to.
- If query_quotas_api returns no results, say "No quotas found for the given filters."
- If query_quotas_api returns an error, return the error message as-is.
- For multi-account queries where some accounts fail: return results from successful accounts and include error notes for failed accounts.

# Narrowing
- Do NOT fabricate, invent, or hallucinate quota values. ONLY present data returned by query_quotas_api.
- Do NOT answer questions outside the scope of AWS Service Quotas (e.g., CloudTrail, CloudWatch, Health, Access Analyzer questions).
- Do NOT call run_athena_query — this agent uses query_quotas_api only.
- Do NOT summarize or interpret quotas — return raw data for the supervisor to synthesize.

## Reference: Service code mapping

| Natural Language | Service Quotas API service code |
|-----------------|-------------------------------|
| EC2, instances | ec2 |
| Lambda, functions | lambda |
| S3, storage, buckets | s3 |
| RDS, database | rds |
| ECS, containers | ecs |
| DynamoDB | dynamodb |
| CloudFront | cloudfront |
| Route 53 | route53 |
| ELB, load balancer | elasticloadbalancing |
| VPC | vpc |
| IAM | iam |
| SQS | sqs |
| SNS | sns |
| CloudWatch | monitoring |
| KMS | kms |
| Bedrock | bedrock |

If the user does not specify a service, ask them which service they want to query — a service code is required for listing quotas.

## Reference: Account mapping

| Account ID | Label |
|------------|-------|
| {vars.ACC1_ID} | {vars.ACC1_LABEL} |
| {vars.ACC2_ID} | {vars.ACC2_LABEL} |
| {vars.ACC3_ID} | {vars.ACC3_LABEL} |

If the user does not specify an account, or says "all accounts" or "across all accounts", query all three.

## Reference: Available actions

| Action | Description | Required params |
|--------|-------------|-----------------|
| list_service_quotas | List all quotas for a service | service_code |
| get_service_quota | Get details of a specific quota | service_code, quota_code |
| list_request_history | List quota increase request history | (optional service_code filter) |
| list_services | List all AWS services in Service Quotas | (optional keyword filter) |

## Reference: Region

The Service Quotas API is regional. Default region is {vars.DEFAULT_REGION}. Pass a different region parameter if the user asks about quotas in another region. Quotas of global services ({', '.join(vars.QUOTAS_GLOBAL_SERVICES)}) exist only in {vars.GLOBAL_SERVICE_REGION}; the tool switches to it automatically.

## Reference: Date range resolution

- "today" → {{TODAY}}
- "yesterday" → compute the previous calendar day from {{TODAY}}
- "this week" → Monday through today of the current week (ISO week)
- "last week" → Monday through Sunday of the previous ISO week
- "this month" → 1st to today in the current month
- "last month" → all days of the previous calendar month
"""

def _new_quotas_agent() -> Agent:
    """Build a fresh Quotas sub-agent for one question.

    A new agent per question carries no earlier question's history, and does not clash with a
    parallel call of the same tool.

    Args:
        None.

    Returns:
        A new Agent on the sub-agent model (built without the CountTokens call), with the
        query_quotas_api tool, no hooks, and the prompt filled with today's date.
    """
    return Agent(
        model=vars.build_bedrock_model(vars.SUBAGENT_MODEL),
        tools=[query_quotas_api],
        hooks=[],
        system_prompt=vars.with_current_date(QUOTAS_SYSTEM_PROMPT),
    )


@tool
def query_quotas(question: str) -> str:
    """
    Accepts a natural language question about AWS Service Quotas.
    Routes it to the Quotas sub-agent, which calls the AWS Service Quotas API directly.
    Returns raw quota data as a string, or a structured error message.

    Use this for questions about: service limits, quotas, capacity, throttling,
    "how many can I create", quota increase requests, maximum allowed resources.
    """
    try:
        emit_status("Quotas agent processing...", source="quotas_agent")
        agent = _new_quotas_agent()
        result = agent(delegated(question, "quotas"))
        emit_status("Quotas agent returning results to supervisor.", source="quotas_agent")

        raw_text = _extract_raw_result(agent)
        vars.extract_token_usage(result, "quotas")
        if raw_text:
            return raw_text
        return str(result)
    except Exception as e:
        return f"ERROR querying Quotas: {str(e)}"
