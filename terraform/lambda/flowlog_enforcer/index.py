# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

"""
flowlog_enforcer — Gives every VPC in the LTTM accounts a flow log to the data lake.

Flow logs made once at `terraform apply` would miss every VPC made later, so this Lambda runs every
15 minutes, and at once on a CreateVpc in the main account's home region. In each account and region
of TARGETS it lists the VPCs and creates the missing flow logs, with the settings of LTTM's flow logs:
all traffic, straight to S3, Parquet, Hive-compatible hourly partitions.

It reads two environment variables: FLOW_LOG_DESTINATION, the lake's prefix (arn:aws:s3:::<bucket>/flowlogs/),
and TARGETS, a JSON list of {"account": "…", "region": "…", "role": "" or a role ARN}; an empty role is the
Lambda's own account.
"""

import json
import os

import boto3

DESTINATION = os.environ["FLOW_LOG_DESTINATION"]
TARGETS = json.loads(os.environ["TARGETS"])


def _ec2(target: dict):
    """An EC2 client in the target's region; through the target's role when it is another account.

    Args:
        target: One entry of TARGETS.

    Returns:
        A boto3 EC2 client, with the role's temporary credentials when the target has a role.
    """
    if not target.get("role"):
        return boto3.client("ec2", region_name=target["region"])
    creds = boto3.client("sts").assume_role(RoleArn=target["role"], RoleSessionName="lttm-flowlog-enforcer")["Credentials"]
    return boto3.client("ec2", region_name=target["region"], aws_access_key_id=creds["AccessKeyId"],
                        aws_secret_access_key=creds["SecretAccessKey"], aws_session_token=creds["SessionToken"])


def _to_the_lake(flow_log: dict) -> bool:
    """Tell whether a flow log delivers to the data lake.

    Args:
        flow_log: A flow log as DescribeFlowLogs returns it.

    Returns:
        True when it writes straight to S3 at DESTINATION, False otherwise.
    """
    return (flow_log.get("LogDestinationType") == "s3"
            and flow_log.get("LogDestination", "").rstrip("/") == DESTINATION.rstrip("/"))


def enforce(ec2) -> list[str]:
    """Create a flow log for every VPC that has none to the data lake.

    Args:
        ec2: An EC2 client in the target's account and region (_ec2).

    Returns:
        The IDs of the VPCs that got a flow log; [] when none was missing.

    Raises:
        RuntimeError: CreateFlowLogs failed for some VPCs.
    """
    vpcs = [v["VpcId"] for page in ec2.get_paginator("describe_vpcs").paginate() for v in page["Vpcs"]]
    logged = {f["ResourceId"] for page in ec2.get_paginator("describe_flow_logs").paginate()
              for f in page["FlowLogs"] if _to_the_lake(f)}
    missing = [v for v in vpcs if v not in logged]
    if not missing:
        return []
    response = ec2.create_flow_logs(
        ResourceIds=missing,
        ResourceType="VPC",
        TrafficType="ALL",
        LogDestinationType="s3",
        LogDestination=DESTINATION,
        DestinationOptions={"FileFormat": "parquet", "HiveCompatiblePartitions": True, "PerHourPartition": True},
        TagSpecifications=[{"ResourceType": "vpc-flow-log",
                            "Tags": [{"Key": "lttm:created-by", "Value": "flowlog-enforcer"}]}],
    )
    failed = {item["ResourceId"]: item["Error"]["Message"] for item in response.get("Unsuccessful", [])}
    if failed:
        raise RuntimeError(f"CreateFlowLogs failed for {failed}")
    return missing


def handler(event, context):
    """Enforce every target; one failing account or region does not stop the others.

    Args:
        event: The schedule's or the CreateVpc rule's event; not read.
        context: The Lambda context; not read.

    Returns:
        {"<account>/<region>": {"created": [VPC IDs]} or {"error": "…"}} per target. Prints one line per target.
    """
    result = {}
    for target in TARGETS:
        where = f"{target['account']}/{target['region']}"
        try:
            result[where] = {"created": enforce(_ec2(target))}
        except Exception as e:
            result[where] = {"error": f"{type(e).__name__}: {e}"}
        print(f"[flowlog-enforcer] {where}: {json.dumps(result[where])}", flush=True)
    return result
