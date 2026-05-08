# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
quotas_tool.py — Strands @tool for querying AWS Service Quotas.

Registered on the quotas_agent as @tool query_quotas_api.

Calls AWS Service Quotas boto3 APIs directly (ListServices, ListServiceQuotas,GetServiceQuota, ListRequestedServiceQuotaChangeHistory). 
No Athena/Glue/S3 infrastructure needed — pure API-based tool.

The Service Quotas API is regional — calls target the region where quotas are managed, defaulting to `eu-central-1` to match the LTTM data layer region.

Supports cross-account queries via STS AssumeRole into `LTTMQuotasReadRole` for the account 2 and account 3 member accounts. 
Uses default execution-role credentials for the account 1 (main) account.
"""

import boto3
from botocore.exceptions import ClientError
from strands import tool
from utils.sse_emitter import emit_status
import utils.agent_vars as _vars

CROSS_ACCOUNT_ROLE_TEMPLATE = "arn:aws:iam::{account_id}:role/LTTMQuotasReadRole"
LTTM_ACCOUNTS = {k: v["label"] for k, v in _vars.ACCOUNTS.items()}
MAIN_ACCOUNT_ID = _vars.ACC1_ID


def _get_account_label(account_id: str) -> str:
    """Return human-readable label for an account ID."""
    return LTTM_ACCOUNTS.get(account_id, account_id)


def _get_sq_client(account_id: str, region: str = "eu-central-1"):
    """Return a boto3 Service Quotas client for the given account and region.

    For the main account (matching `MAIN_ACCOUNT_ID`), returns a client built from the execution role's default credentials. 
    For any other account, calls STS AssumeRole against `LTTMQuotasReadRole` in that account and returns a client configured with the temporary credentials.

    Args:
        account_id: Target AWS account ID. If it equals the main-account ID, no role assumption happens.
        region: AWS region for the client. Service Quotas is regional — quotas live in the region where they are managed, defaults to `eu-central-1`.

    Returns:
        A boto3 `service-quotas` client scoped to `account_id` and `region`.
    """
    
    if account_id == MAIN_ACCOUNT_ID:
        return boto3.client("service-quotas", region_name=region)

    role_arn = CROSS_ACCOUNT_ROLE_TEMPLATE.format(account_id=account_id)
    label = _get_account_label(account_id)
    print(f"[LTTM:Quotas] Assuming role {role_arn} for {label} account", flush=True)

    sts = boto3.client("sts")
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"lttm-quotas-{account_id}",
    )["Credentials"]

    return boto3.client(
        "service-quotas",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _format_quota(quota: dict, account_id: str) -> str:
    """Format a single quota item into a human-readable text block."""
    label = _get_account_label(account_id)
    service_name = quota.get("ServiceName", "N/A")
    service_code = quota.get("ServiceCode", "N/A")
    quota_name = quota.get("QuotaName", "N/A")
    quota_code = quota.get("QuotaCode", "N/A")
    applied_value = quota.get("Value", "N/A")
    default_value = quota.get("DefaultValue", "N/A")
    adjustable = "Yes" if quota.get("Adjustable", False) else "No"
    quota_arn = quota.get("QuotaArn", "")

    lines = [
        "--- Quota ---",
        f"Account: {account_id} ({label})",
        f"Service: {service_name} [{service_code}]",
        f"Quota: {quota_name} [{quota_code}]",
        f"Applied Value: {applied_value}",
        f"Default Value: {default_value}",
        f"Adjustable: {adjustable}",
    ]
    if quota_arn:
        lines.append(f"ARN: {quota_arn}")

    return "\n".join(lines)


def _format_request(request: dict, account_id: str) -> str:
    """Format a single quota request history item into a human-readable text block."""
    label = _get_account_label(account_id)
    service_name = request.get("ServiceName", "N/A")
    service_code = request.get("ServiceCode", "N/A")
    quota_name = request.get("QuotaName", "N/A")
    quota_code = request.get("QuotaCode", "N/A")
    desired_value = request.get("DesiredValue", "N/A")
    status = request.get("Status", "N/A")
    created = request.get("Created", "N/A")

    return (
        f"--- Quota Request ---\n"
        f"Account: {account_id} ({label})\n"
        f"Service: {service_name} [{service_code}]\n"
        f"Quota: {quota_name} [{quota_code}]\n"
        f"Requested Value: {desired_value}\n"
        f"Status: {status}\n"
        f"Created: {created}"
    )


def _format_service(service: dict) -> str:
    """Format a single service item into a human-readable line."""
    service_code = service.get("ServiceCode", "N/A")
    service_name = service.get("ServiceName", "N/A")
    return f"{service_code} — {service_name}"


@tool
def query_quotas_api(
    account_id: str,
    action: str = "list_service_quotas",
    region: str = "eu-central-1",
    service_code: str = "",
    quota_code: str = "",
    keyword: str = "",
    max_results: int = 100,
) -> str:
    """
    Query AWS Service Quotas for a specific AWS account.

    Calls the AWS Service Quotas boto3 API to retrieve quota limits, quota details, request history, or available services. 
    Handles cross-account access via STS AssumeRole for non-main accounts.

    Args:
        account_id: AWS account ID to query (required).
        action: API action — list_service_quotas (default), get_service_quota, list_request_history, or list_services.
        region: AWS region, defaults to eu-central-1.
        service_code: AWS service code (e.g. ec2, lambda), required for list_service_quotas and get_service_quota.
        quota_code: Quota code, required for get_service_quota.
        keyword: Optional keyword filter for list_services.
        max_results: Maximum number of items to return, defaults to 100.

    Returns:
        Formatted string with quota data, or an error message.
    """
    label = _get_account_label(account_id)

    emit_status(
        f"Querying Service Quotas in account {account_id} ({label})...",
        source="quotas_tool",
    )

    try:
        client = _get_sq_client(account_id, region)

        if action == "get_service_quota":
            print(
                f"[LTTM:Quotas] Calling get_service_quota — "
                f"service={service_code}, quota={quota_code}",
                flush=True,
            )
            response = client.get_service_quota(
                ServiceCode=service_code,
                QuotaCode=quota_code,
            )
            quota = response.get("Quota", {})
            result = _format_quota(quota, account_id)
            print(f"[LTTM:Quotas] Returning quota detail for {quota_code}", flush=True)
            emit_status("Quotas query complete — quota detail returned", source="quotas_tool")
            return result

        elif action == "list_request_history":
            print(
                f"[LTTM:Quotas] Calling list_requested_service_quota_change_history"
                f" — service_code filter={service_code!r}",
                flush=True,
            )

            all_requests = []
            next_token = None
            while True:
                kwargs = {}
                if service_code:
                    kwargs["ServiceCode"] = service_code
                if next_token:
                    kwargs["NextToken"] = next_token

                response = client.list_requested_service_quota_change_history(**kwargs)
                requests = response.get("RequestedQuotas", [])
                all_requests.extend(requests)

                next_token = response.get("NextToken")
                if not next_token or len(all_requests) >= max_results:
                    break

            all_requests = all_requests[:max_results]

            if not all_requests:
                msg = f"No quota increase requests found for account {account_id} ({label})."
                if service_code:
                    msg = (
                        f"No quota increase requests found for service "
                        f"'{service_code}' in account {account_id} ({label})."
                    )
                print(f"[LTTM:Quotas] {msg}", flush=True)
                emit_status("Quotas query complete — 0 requests returned", source="quotas_tool")
                return msg

            formatted = [_format_request(r, account_id) for r in all_requests]
            count = len(all_requests)
            print(f"[LTTM:Quotas] Returning {count} request history items", flush=True)
            emit_status(
                f"Quotas query complete — {count} requests returned",
                source="quotas_tool",
            )
            return "\n\n".join(formatted)

        elif action == "list_services":
            print("[LTTM:Quotas] Calling list_services", flush=True)

            all_services = []
            next_token = None
            while True:
                kwargs = {}
                if next_token:
                    kwargs["NextToken"] = next_token

                response = client.list_services(**kwargs)
                services = response.get("Services", [])
                all_services.extend(services)

                next_token = response.get("NextToken")
                if not next_token or len(all_services) >= max_results:
                    break

            all_services = all_services[:max_results]

            if keyword:
                kw_lower = keyword.lower()
                all_services = [
                    s for s in all_services
                    if kw_lower in s.get("ServiceName", "").lower()
                    or kw_lower in s.get("ServiceCode", "").lower()
                ]

            if not all_services:
                msg = f"No services found"
                if keyword:
                    msg += f" matching keyword '{keyword}'"
                msg += f" in account {account_id} ({label})."
                print(f"[LTTM:Quotas] {msg}", flush=True)
                emit_status("Quotas query complete — 0 services returned", source="quotas_tool")
                return msg

            formatted = [_format_service(s) for s in all_services]
            count = len(all_services)
            print(f"[LTTM:Quotas] Returning {count} services", flush=True)
            emit_status(
                f"Quotas query complete — {count} services returned",
                source="quotas_tool",
            )
            return "\n".join(formatted)

        else:
            print(
                f"[LTTM:Quotas] Calling list_service_quotas — "
                f"service={service_code} (collected=0)",
                flush=True,
            )

            all_quotas = []
            next_token = None
            while True:
                kwargs = {"ServiceCode": service_code}
                if next_token:
                    kwargs["NextToken"] = next_token

                response = client.list_service_quotas(**kwargs)
                quotas = response.get("Quotas", [])
                all_quotas.extend(quotas)

                print(
                    f"[LTTM:Quotas] Calling list_service_quotas — "
                    f"service={service_code} (collected={len(all_quotas)})",
                    flush=True,
                )

                next_token = response.get("NextToken")
                if not next_token or len(all_quotas) >= max_results:
                    break

            all_quotas = all_quotas[:max_results]

            if not all_quotas:
                msg = (
                    f"No quotas found for service '{service_code}' "
                    f"in account {account_id} ({label})."
                )
                print(f"[LTTM:Quotas] {msg}", flush=True)
                emit_status("Quotas query complete — 0 quotas returned", source="quotas_tool")
                return msg

            formatted = [_format_quota(q, account_id) for q in all_quotas]
            count = len(all_quotas)
            print(f"[LTTM:Quotas] Returning {count} quotas for service {service_code}", flush=True)
            emit_status(
                f"Quotas query complete — {count} quotas returned",
                source="quotas_tool",
            )
            return "\n\n".join(formatted)

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "InvalidParameterValueException":
            msg = f"Error: InvalidParameterValue: Invalid service code '{service_code}': {error_msg}"
        elif error_code == "NoSuchResourceException":
            msg = f"Error: NoSuchResource: Quota code '{quota_code}' not found for service '{service_code}': {error_msg}"
        elif error_code == "AccessDeniedException":
            msg = (f"Error: AccessDenied: Insufficient permissions to query Service Quotas "
                   f"in account {account_id} ({label}). Ensure servicequotas:List* and "
                   f"servicequotas:Get* are granted.")
        else:
            msg = f"Error: {error_code}: {error_msg}"

        print(f"[LTTM:Quotas] {msg}", flush=True)
        return msg

    except Exception as e:
        error_type = type(e).__name__
        if "AssumeRole" in str(e) or "LTTMQuotasReadRole" in str(e):
            msg = (f"Error: {error_type}: Failed to assume cross-account role LTTMQuotasReadRole "
                   f"in account {account_id} ({label}). Verify the role exists and the trust "
                   f"policy allows assumption. {str(e)}")
        else:
            msg = f"Error: {error_type}: {str(e)}"

        print(f"[LTTM:Quotas] {msg}", flush=True)
        return msg