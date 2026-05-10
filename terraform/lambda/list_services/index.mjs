// Copyright (c) 2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * Returns a static JSON array of the 12 available LTTM sub-agents with their names, descriptions, and keyword triggers
 *
 * Connects to:
 *   - aws_api_gateway_rest_api.lttm_stream (apigw.tf) — GET /services
 *   - alexandra.sh --services flag — CLI consumer
 */

const SERVICES = [
  {
    name: "cloudtrail",
    description: "API calls, IAM events, who did what and when",
    keywords: ["api", "iam", "access denied", "who", "cloudtrail"],
  },
  {
    name: "cloudwatch",
    description: "Application logs, Lambda output, log group content",
    keywords: ["logs", "lambda", "application", "error", "cloudwatch"],
  },
  {
    name: "config",
    description: "Resource configuration changes, compliance, what changed",
    keywords: ["config", "changed", "security group", "policy", "compliance"],
  },
  {
    name: "access_analyzer",
    description: "External access findings, publicly accessible resources",
    keywords: ["public", "exposed", "external", "cross-account", "access analyzer"],
  },
  {
    name: "health",
    description: "AWS service health events, outages, maintenance",
    keywords: ["outage", "maintenance", "health", "disruption", "aws status"],
  },
  {
    name: "cur",
    description: "Cost analysis, billing data, spend trends",
    keywords: ["cost", "spend", "billing", "expensive", "budget"],
  },
  {
    name: "organizations",
    description: "Org structure, account membership, SCPs",
    keywords: ["organization", "accounts", "scp", "ou", "member"],
  },
  {
    name: "quotas",
    description: "Service limits, quota values, capacity",
    keywords: ["quota", "limit", "throttle", "capacity", "maximum"],
  },
  {
    name: "flowlogs",
    description: "VPC network traffic, rejected/accepted packets",
    keywords: ["network", "traffic", "flow", "vpc", "port", "ip"],
  },
  {
    name: "guardduty",
    description: "Threat detection, suspicious activity, security findings",
    keywords: ["threat", "suspicious", "attack", "guardduty", "malicious"],
  },
  {
    name: "macie",
    description: "Sensitive data discovery, PII and credentials in S3 buckets",
    keywords: ["sensitive data", "pii", "credentials", "s3", "macie", "classification"],
  },
  {
    name: "inspector",
    description: "Vulnerability findings, CVEs, package and code vulnerabilities",
    keywords: ["vulnerability", "cve", "inspector", "patch", "cvss", "unpatched"],
  },
];

export async function handler(event) {
  return {
    statusCode: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
    body: JSON.stringify(SERVICES),
  };
}
