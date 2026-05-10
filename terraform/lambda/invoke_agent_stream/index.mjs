// Copyright (c) 2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * Lambda streaming shim between API Gateway REST API and AgentCore Runtime.
 *
 * Connects to:
 *   - var.cli_stream_runtime_arn (terraform/variables.tf) — AgentCore streaming runtime this Lambda invokes; 
 *     ARN is baked into the Lambda env var AGENT_RUNTIME_ARN at deploy time (lambda.tf)
 *   - aws_api_gateway_rest_api.lttm_stream (apigw.tf) — POST /stream route through response-streaming-invocations integration
 *   - aws_dynamodb_table.conversations (dynamodb.tf) — fire-and-forget UpdateItem for session metadata (timestamps, question_count, title, user_id)
 *   - Cognito JWT (cognito.tf) — Authorization header is parsed for the submclaim, which becomes the DynamoDB user_id
 *   - alexandra.sh — CLI client that POSTs questions and consumes SSE chunks
 */

import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";

import {
  DynamoDBClient,
  UpdateItemCommand,
} from "@aws-sdk/client-dynamodb";

const AGENT_RUNTIME_ARN = process.env.AGENT_RUNTIME_ARN;
const REGION = process.env.AGENTCORE_REGION || "us-west-2";
const CONVERSATIONS_TABLE = process.env.CONVERSATIONS_TABLE;
const client = new BedrockAgentCoreClient({ region: REGION });
const ddb = new DynamoDBClient({ region: "eu-central-1" });

/**
 * Extract the Cognito user_id from a JWT Authorization header.
 *
 * Decodes the base64url-encoded payload segment (second dot-separated part), parses it as JSON
 * and returns the `sub` claim. Any parse/decode failure or missing header returns "anonymous" 
 * so downstream DynamoDB metadata writes always have a non-null user_id.
 *
 * @param {string|undefined} authHeader - Raw "Authorization" header value. May be undefined when no auth is present.
 * @returns {string} The JWT `sub` claim, or "anonymous" on any failure.
 */
function extractUserIdFromJwt(authHeader) {
  try {
    if (!authHeader) return "anonymous";
    const token = authHeader.replace(/^Bearer\s+/i, "");
    const payloadB64 = token.split(".")[1];
    if (!payloadB64) return "anonymous";
    const json = Buffer.from(payloadB64, "base64").toString("utf-8");
    const payload = JSON.parse(json);
    return payload.sub || "anonymous";
  } catch {
    return "anonymous";
  }
}

/**
 * Streaming handler invoked by API Gateway response-streaming integration.
 *
 * For each request: extracts the AgentCore session header, decodes the body (handling base64 events), invokes 
 * AgentCore InvokeAgentRuntime, and pipes the SSE response straight to the client via awslambda.HttpResponseStream
 * 
 * In parallel (fire-and-forget, never awaited), writes session metadata to DynamoDB and interleaves status 
 * SSE events — this is explicitly decoupled so a slow DynamoDB write cannot delay agent token streaming
 *
 * @param {object} event - API Gateway response-streaming event with headers, body (possibly base64), and isBase64Encoded flag
 * @param {NodeJS.WritableStream} responseStream - Lambda response stream provided by awslambda.streamifyResponse
 *                                                 written via HttpResponseStream.from() for HTTP metadata + SSE frames
 * @returns {Promise<void>}
 */
const streamHandler = async (event, responseStream) => {
  const headers = event.headers || {};
  const sessionId =
    headers["x-amzn-bedrock-agentcore-session-id"] ||
    headers["X-Amzn-Bedrock-Agentcore-Session-Id"] ||
    "";

  let body = event.body || "{}";
  if (event.isBase64Encoded) {
    body = Buffer.from(body, "base64").toString("utf-8");
  }

  const params = {
    agentRuntimeArn: AGENT_RUNTIME_ARN,
    payload: new TextEncoder().encode(body),
    qualifier: "DEFAULT",
  };
  if (sessionId) {
    params.runtimeSessionId = sessionId;
  }

  try {
    const command = new InvokeAgentRuntimeCommand(params);
    const response = await client.send(command);
    const agentContentType = response.contentType || "";
    let isSSE = agentContentType.includes("text/event-stream");

    if (!isSSE) {
      isSSE = true;
    }

    const httpStream = awslambda.HttpResponseStream.from(responseStream, {
      statusCode: 200,
      headers: { "Content-Type": "text/event-stream" },
    });

    if (CONVERSATIONS_TABLE) {
      const parsedBody = JSON.parse(body);
      const question = parsedBody.prompt || parsedBody.question || "";
      const now = new Date().toISOString();
      const ttl = Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60; // 30 days
      const userId = extractUserIdFromJwt(
        headers["authorization"] || headers["Authorization"]
      );

      httpStream.write(
        "data: " +
          JSON.stringify({ type: "status", message: "Connecting to session store..." }) +
          "\n\n"
      );

      ddb
        .send(
          new UpdateItemCommand({
            TableName: CONVERSATIONS_TABLE,
            Key: { session_id: { S: sessionId } },
            UpdateExpression:
              "SET last_active = :now, expires_at = :ttl, user_id = :uid, " +
              "created_at = if_not_exists(created_at, :now), " +
              "title = if_not_exists(title, :title) " +
              "ADD question_count :one",
            ExpressionAttributeValues: {
              ":now": { S: now },
              ":ttl": { N: String(ttl) },
              ":uid": { S: userId },
              ":one": { N: "1" },
              ":title": { S: question.substring(0, 100) },
            },
            ReturnValues: "ALL_NEW",
          })
        )
        .then((resp) => {
          const count = resp.Attributes?.question_count?.N || "?";
          httpStream.write(
            "data: " +
              JSON.stringify({
                type: "status",
                message: `Question #${count} of session ${sessionId.substring(0, 8)} saved.`,
              }) +
              "\n\n"
          );
        })
        .catch((err) => {
          console.error("[LTTM:Metadata] DynamoDB write failed:", err.message);
          httpStream.write(
            "data: " +
              JSON.stringify({
                type: "status",
                message: "Session metadata write failed (non-blocking)",
              }) +
              "\n\n"
          );
        });
    }

    if (response.response && typeof response.response[Symbol.asyncIterator] === "function") {
      for await (const chunk of response.response) {
        httpStream.write(chunk);
      }
    } else if (response.response && typeof response.response.transformToByteArray === "function") {
      const bytes = await response.response.transformToByteArray();
      httpStream.write(bytes);
    }

    httpStream.end();
  } catch (err) {
    let statusCode = 500;
    if (err.name === "ResourceNotFoundException") statusCode = 404;
    if (err.name === "AccessDeniedException") statusCode = 403;

    const errorEvent = JSON.stringify({
      type: "error",
      step: 1,
      source: "lambda",
      message: err.message || String(err),
    });

    const httpStream = awslambda.HttpResponseStream.from(responseStream, {
      statusCode,
      headers: { "Content-Type": "text/event-stream" },
    });
    httpStream.write(`data: ${errorEvent}\n\n`);
    httpStream.end();
  }
};

export const handler = awslambda.streamifyResponse(streamHandler);
