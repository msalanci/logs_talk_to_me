// Copyright (c) 2025-2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * The health Lambda (lttm-health-check): GET /health, for alexandra.sh --health.
 *
 * It reads the runtime's status with GetAgentRuntime on the AgentCore control plane: no agent is invoked and no
 * model is called. The client calls the runtime's region, not the Lambda's own region.
 *
 * Environment: AGENT_RUNTIME_ARN (the runtime's ARN, which ends in runtime/<runtime ID>), AGENTCORE_REGION (the
 * runtime's region, var.agentcore_region).
 */

import {
  BedrockAgentCoreControlClient,
  GetAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore-control";

const client = new BedrockAgentCoreControlClient({ region: process.env.AGENTCORE_REGION });

const runtimeId = process.env.AGENT_RUNTIME_ARN.split("/").pop();

/**
 * Answer GET /health with the runtime's status.
 *
 * Only READY, deployed and taking invocations, counts as healthy. Every other status (CREATING, CREATE_FAILED,
 * UPDATING, UPDATE_FAILED, DELETING) gives a 503.
 *
 * @param {object} event - The API Gateway proxy event; not used.
 * @returns {Promise<object>} The API Gateway proxy response: 200 with status "healthy" when the runtime is READY,
 *     503 with "unhealthy" otherwise. A failed GetAgentRuntime gives a 503 with runtime "ERROR" and the error, and is
 *     logged as one [LTTM:HealthCheck] line.
 */
export async function handler(event) {
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  };

  try {
    const resp = await client.send(
      new GetAgentRuntimeCommand({ agentRuntimeId: runtimeId })
    );

    const status = resp.status;
    const healthy = status === "READY";

    return {
      statusCode: healthy ? 200 : 503,
      headers,
      body: JSON.stringify({
        status: healthy ? "healthy" : "unhealthy",
        runtime: status,
        name: resp.agentRuntimeName,
        timestamp: new Date().toISOString(),
      }),
    };
  } catch (err) {
    console.error("[LTTM:HealthCheck] GetAgentRuntime failed:", err.message);
    return {
      statusCode: 503,
      headers,
      body: JSON.stringify({
        status: "unhealthy",
        runtime: "ERROR",
        error: err.message,
        timestamp: new Date().toISOString(),
      }),
    };
  }
}
