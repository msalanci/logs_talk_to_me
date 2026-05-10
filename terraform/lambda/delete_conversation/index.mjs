// Copyright (c) 2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * Deletes a single conversation metadata row from the DynamoDB conversations table by session_id. 
 * Returns 200 on success, 404 if the session_id does not exist.
 *
 * Connects to:
 *   - aws_dynamodb_table.conversations (dynamodb.tf) — deletes metadata
 *   - aws_api_gateway_rest_api.lttm_stream (apigw.tf) — DELETE /conversations/{id}
 *   - alexandra.sh --delete flag — CLI consumer
 */

import { DynamoDBClient, DeleteItemCommand } from "@aws-sdk/client-dynamodb";

const CONVERSATIONS_TABLE = process.env.CONVERSATIONS_TABLE;
const ddb = new DynamoDBClient({ region: "eu-central-1" });

/**
 * Extracts session_id from `event.pathParameters.id`, performs a DynamoDB DeleteItem with 
 * ReturnValues: "ALL_OLD", and maps the outcome to:
 *   - 200 with { deleted: true, session_id } when the item existed
 *   - 404 when the item did not exist (Attributes empty)
 *   - 400 when session_id is missing from the path
 *   - 500 on DynamoDB errors
 *
 * @param {object} event - API Gateway AWS_PROXY event; event.pathParameters.id carries the session_id to delete
 * @returns {Promise<{statusCode: number, headers: object, body: string}>} API Gateway proxy response with JSON body.
 */
export async function handler(event) {
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  };

  const sessionId = event.pathParameters?.id;

  if (!sessionId) {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: "Missing session_id path parameter" }),
    };
  }

  try {
    const result = await ddb.send(
      new DeleteItemCommand({
        TableName: CONVERSATIONS_TABLE,
        Key: { session_id: { S: sessionId } },
        ReturnValues: "ALL_OLD",
      })
    );

    if (result.Attributes && Object.keys(result.Attributes).length > 0) {
      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({ deleted: true, session_id: sessionId }),
      };
    } else {
      return {
        statusCode: 404,
        headers,
        body: JSON.stringify({ error: "Session not found" }),
      };
    }
  } catch (err) {
    console.error("[LTTM:DeleteConversation] DeleteItem failed:", err.message);
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: "Failed to delete conversation" }),
    };
  }
}
