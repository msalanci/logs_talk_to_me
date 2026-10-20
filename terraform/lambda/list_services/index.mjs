// Copyright (c) 2025-2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * The services Lambda (lttm-list-services): GET /services, for alexandra.sh --services.
 *
 * It returns the 13 available LTTM sub-agents, each with its name, a description and the keywords of the
 * questions it takes. The list is written here, not read from the agents: a new sub-agent needs an entry here
 * too, and this Lambda redeployed. Control Tower's keywords leave SCPs to organizations and configuration drift
 * to config.
 *
 * Environment: none.
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
  {
    name: "controltower",
    description: "Control Tower landing zone, controls (guardrails), baselines, drift, and what Control Tower did and who started it",
    keywords: ["control tower", "landing zone", "guardrail", "control tower drift"],
  },
];

/**
 * Answer GET /services with the list of sub-agents.
 *
 * @param {object} event - The API Gateway proxy event; not used.
 * @returns {Promise<object>} The API Gateway proxy response: 200 with SERVICES as a JSON array.
 */
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
