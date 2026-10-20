// Copyright (c) 2025-2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * The stream Lambda (lttm-invoke-agent-stream): POST /ask, between API Gateway and the AgentCore runtime.
 *
 * It checks the request, invokes the runtime with InvokeAgentRuntime, and pipes the runtime's Server-Sent
 * Events to the client as they arrive. It is written in Node.js because Lambda response streaming
 * (awslambda.streamifyResponse) exists only in the Node.js managed runtimes. Each question also updates the
 * conversation's row in DynamoDB (timestamps, question count, title, user) for alexandra.sh --history.
 *
 * Environment: AGENT_RUNTIME_ARN (the runtime to invoke), AGENTCORE_REGION (its region, var.agentcore_region),
 * CONVERSATIONS_TABLE (the conversations table; without it, no row is written), CONVERSATIONS_REGION (the table's
 * region, var.project_region).
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
const REGION = process.env.AGENTCORE_REGION;
const CONVERSATIONS_TABLE = process.env.CONVERSATIONS_TABLE;
const SESSION_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const client = new BedrockAgentCoreClient({ region: REGION });

const ddb = new DynamoDBClient({ region: process.env.CONVERSATIONS_REGION });

/**
 * Read the Cognito user ID, the JWT's "sub" claim, from an Authorization header.
 *
 * The token's signature is not checked here: API Gateway's Cognito authorizer has already verified it.
 *
 * @param {string | undefined} authHeader - The Authorization header, "Bearer <JWT>".
 * @returns {string} The sub claim; "anonymous" when the header is missing or can't be read, so the row is still
 *     written.
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
 * Send the one SSE error event the CLI shows as ❌, on a new response with the given status.
 *
 * @param {object} responseStream - The Lambda response stream, before any response was opened on it.
 * @param {number} statusCode - The HTTP status of the response.
 * @param {string} message - The error text the CLI shows.
 * @returns {void} Opens the response, writes the event and ends the response.
 */
function sendError(responseStream, statusCode, message) {
  const httpStream = awslambda.HttpResponseStream.from(responseStream, {
    statusCode,
    headers: { "Content-Type": "text/event-stream" },
  });
  httpStream.write(`data: ${JSON.stringify({ type: "error", step: 1, source: "lambda", message })}\n\n`);
  httpStream.end();
}

/**
 * Tell why a parsed request body can't be answered.
 *
 * The runtime answers only a JSON object whose "prompt" is non-empty text, which is what the CLI always sends.
 * A prompt of spaces only is refused too: there is no question in it.
 *
 * @param {unknown} parsedBody - The request body after JSON.parse.
 * @returns {string | null} The reason for the HTTP 400; null when the body can be sent to the runtime.
 */
function requestProblem(parsedBody) {
  if (parsedBody === null || typeof parsedBody !== "object" || Array.isArray(parsedBody)) {
    return "The request body must be a JSON object.";
  }
  if (typeof parsedBody.prompt !== "string" || parsedBody.prompt.trim() === "") {
    return 'The request body needs "prompt": the question as text.';
  }
  return null;
}

/**
 * Keep a value only when it is a string.
 *
 * API Gateway passes the body and the header values as strings. Any other value, which only a direct invoke of
 * this function can send, counts as missing, so nothing later can fail on its type.
 *
 * @param {unknown} value - A header value or the body, as the event holds it.
 * @returns {string} The value when it is a string; "" otherwise.
 */
function text(value) {
  return typeof value === "string" ? value : "";
}

/**
 * Make the conversation's title from the question.
 *
 * The title is the question's first 100 UTF-16 units, cut back to a whole character. A lone surrogate becomes
 * U+FFFD and every control character a space: --history prints every user's titles, half an emoji makes Python's
 * print fail and ends the listing at that row, and a control character such as ESC would reach the other users'
 * terminals as an escape sequence.
 *
 * @param {string} question - The user's question.
 * @returns {string} The title: at most 100 UTF-16 units of well-formed text, with no control characters.
 */
function titleOf(question) {
  let title = "";
  for (const ch of question) {
    if (title.length + ch.length > 100) break;
    title += ch;
  }
  return title.toWellFormed().replace(/[\u0000-\u001f\u007f-\u009f]/g, " ");
}

/**
 * Refuse a request: log one line with the reason, then send the client a 400 with it.
 *
 * The log line holds the reason, the session ID's first 8 characters and the body's length, never the body.
 *
 * @param {object} responseStream - The Lambda response stream, before any response was opened on it.
 * @param {string} reason - Why the request is refused; the CLI shows it.
 * @param {string} sessionId - The session ID header, or "".
 * @param {string} body - The request body, used for its length only.
 * @returns {void} Writes the [LTTM:Request] log line and the 400 response.
 */
function rejectRequest(responseStream, reason, sessionId, body) {
  console.warn("[LTTM:Request] rejected " + JSON.stringify({
    reason,
    session: sessionId.substring(0, 8),
    bodyChars: body.length,
  }));
  sendError(responseStream, 400, reason);
}

/**
 * Answer one POST /ask: check the request, invoke the runtime, and stream its answer to the client.
 *
 * The order matters. The body is parsed and checked, and the session ID checked, before anything is written: a
 * refused request gets a 400, and the runtime is never called. The 200 response opens only once the runtime has
 * answered, so an error after that point goes on the same stream. The conversation's row is written alongside
 * the stream, not before it, and awaited before the stream ends, after a failure too: Node.js 24 Lambdas don't
 * wait for promises still running when the handler returns.
 *
 * A session ID must be a UUID in lower case, as the CLI makes them; the CLI and the runtime check the same
 * pattern. AgentCore Memory finds a session's summary by its ID as a namespace prefix, so an ID of another length
 * could mix sessions' memory, and the same UUID in upper case would be another session. A request without a
 * session ID gets no row, because DynamoDB refuses an empty key.
 *
 * @param {object} event - The API Gateway event: its headers (the session ID, the Authorization JWT) and its body.
 * @param {object} responseStream - The Lambda response stream that API Gateway relays to the client.
 * @returns {Promise<void>} Resolves when the response has ended. A failed runtime call is logged as one
 *     [LTTM:Stream] line, and reaches the client as an SSE error event (with 404, 403 or 500 when no response had
 *     started yet).
 */
const streamHandler = async (event, responseStream) => {
  const headers = event?.headers || {};
  const sessionId =
    text(headers["x-amzn-bedrock-agentcore-session-id"]) ||
    text(headers["X-Amzn-Bedrock-Agentcore-Session-Id"]);

  const body = event?.isBase64Encoded
    ? Buffer.from(text(event.body), "base64").toString("utf-8")
    : text(event?.body);

  let parsedBody;
  try {
    parsedBody = JSON.parse(body || "{}");
  } catch {
    rejectRequest(responseStream, "The request body is not valid JSON.", sessionId, body);
    return;
  }

  const problem = requestProblem(parsedBody);
  if (problem) {
    rejectRequest(responseStream, problem, sessionId, body);
    return;
  }

  if (sessionId && !SESSION_ID_PATTERN.test(sessionId)) {
    rejectRequest(responseStream, "The session ID must be a UUID in lower case, as the CLI makes them.", sessionId, body);
    return;
  }

  const params = {
    agentRuntimeArn: AGENT_RUNTIME_ARN,
    payload: new TextEncoder().encode(body),
    qualifier: "DEFAULT",
  };
  if (sessionId) {
    params.runtimeSessionId = sessionId;
  }

  let httpStream = null;
  let metadataWrite = null;
  const startedAt = Date.now();
  let agentRequestId = null;
  let chunksForwarded = 0;
  let bytesForwarded = 0;
  let lastChunkAt = null;
  try {
    const command = new InvokeAgentRuntimeCommand(params);
    const response = await client.send(command);
    agentRequestId = response.$metadata?.requestId || null;

    httpStream = awslambda.HttpResponseStream.from(responseStream, {
      statusCode: 200,
      headers: { "Content-Type": "text/event-stream" },
    });

    if (CONVERSATIONS_TABLE && sessionId) {
      const question = parsedBody.prompt;
      const now = new Date().toISOString();
      const ttl = Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60;
      const userId = extractUserIdFromJwt(
        headers["authorization"] || headers["Authorization"]
      );

      httpStream.write(
        "data: " +
        JSON.stringify({ type: "status", message: "Connecting to session store..." }) +
        "\n\n"
      );

      metadataWrite = ddb
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
              ":title": { S: titleOf(question) },
            },
            ReturnValues: "ALL_NEW",
          })
        )
        .then((resp) => {
          if (httpStream.writableEnded) return;
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
          if (httpStream.writableEnded) return;
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
        chunksForwarded += 1;
        bytesForwarded += chunk.length;
        lastChunkAt = Date.now();
        httpStream.write(chunk);
      }
    } else if (response.response && typeof response.response.transformToByteArray === "function") {
      const bytes = await response.response.transformToByteArray();
      chunksForwarded += 1;
      bytesForwarded += bytes.length;
      lastChunkAt = Date.now();
      httpStream.write(bytes);
    }

    await metadataWrite;
    httpStream.end();
  } catch (err) {
    let statusCode = 500;
    if (err.name === "ResourceNotFoundException") statusCode = 404;
    if (err.name === "AccessDeniedException") statusCode = 403;
    const message = err.message || String(err);

    console.error("[LTTM:Stream] AgentCore call failed " + JSON.stringify({
      phase: httpStream ? "streaming" : "request",
      name: err.name,
      code: err.code,
      message,
      httpStatus: err.$metadata?.httpStatusCode,
      requestId: err.$metadata?.requestId || agentRequestId,
      session: sessionId.substring(0, 8),
      elapsedMs: Date.now() - startedAt,
      chunks: chunksForwarded,
      bytes: bytesForwarded,
      sinceLastChunkMs: lastChunkAt === null ? null : Date.now() - lastChunkAt,
    }));

    await metadataWrite;

    if (!httpStream) {
      sendError(responseStream, statusCode, message);
    } else if (!httpStream.writableEnded) {
      httpStream.write(`data: ${JSON.stringify({ type: "error", step: 1, source: "lambda", message })}\n\n`);
      httpStream.end();
    }
  }
};

/**
 * The Lambda entry point: streamHandler, wrapped for Lambda response streaming.
 *
 * @param {object} event - As for streamHandler.
 * @param {object} responseStream - As for streamHandler; the Lambda runtime provides it.
 * @returns {Promise<void>} As for streamHandler.
 */
export const handler = awslambda.streamifyResponse(streamHandler);
