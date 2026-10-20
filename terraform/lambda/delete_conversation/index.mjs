// Copyright (c) 2025-2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * The delete Lambda (lttm-delete-conversation): DELETE /conversations/{id}, for alexandra.sh --delete.
 *
 * It deletes one conversation's row from the DynamoDB conversations table. Only the row goes: the session's
 * AgentCore Memory is not touched, and expires by its own retention.
 *
 * Environment: CONVERSATIONS_TABLE (the conversations table), CONVERSATIONS_REGION (its region, var.project_region).
 */

import { DynamoDBClient, DeleteItemCommand } from "@aws-sdk/client-dynamodb";

const CONVERSATIONS_TABLE = process.env.CONVERSATIONS_TABLE;
const ddb = new DynamoDBClient({ region: process.env.CONVERSATIONS_REGION });

/**
 * Delete the row of the session in the path, and tell whether there was one.
 *
 * DeleteItem succeeds whether or not the row exists. With ReturnValues "ALL_OLD" it returns the row it deleted,
 * so an answer without attributes means there was no such session.
 *
 * @param {object} event - The API Gateway proxy event; pathParameters.id is the session ID.
 * @returns {Promise<object>} The API Gateway proxy response: 200 with {deleted, session_id} when the row was
 *     deleted, 404 when there was none, 400 without a session ID, and 500 when DynamoDB failed (logged as one
 *     [LTTM:DeleteConversation] line).
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
