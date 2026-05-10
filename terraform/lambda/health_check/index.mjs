// Copyright (c) 2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
* Calls GetAgentRuntime on the AgentCore control plane API to check if the LTTM supervisor runtime is active
* Returns a JSON status object with 200 if READY or ACTIVE, 503 otherwise
 *
 * Connects to:
 *   - local.cli_stream_runtime_arn (agents.tf) — runtime to check
 *   - aws_api_gateway_rest_api.lttm_stream (apigw.tf) — GET /health
 *   - alexandra.sh --health flag — CLI consumer
 */

import {
  BedrockAgentCoreControlClient,
  GetAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore-control";

const client = new BedrockAgentCoreControlClient({ region: "us-west-2" });
const runtimeId = process.env.AGENT_RUNTIME_ARN.split("/").pop();

/**
 * Calls bedrock-agentcore-control:GetAgentRuntime on the runtime ID parsed from AGENT_RUNTIME_ARN 
 * and maps the response to:
 *   - 200 "healthy" when status === "READY"
 *   - 503 "unhealthy" for any other status (CREATING, UPDATING, READY_FAILED, …)
 *   - 503 "unhealthy" with error detail when the control-plane call throws
 *
 * No agent invocation, no LLM cost — a single-digit-ms control-plane read.
 *
 * @param {object} event - API Gateway AWS_PROXY event (unused; the handler reads runtimeId from env at cold start)
 * @returns {Promise<{statusCode: number, headers: object, body: string}>}
 *     API Gateway proxy response with JSON body: { status, runtime, name?, error?, timestamp }.
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
