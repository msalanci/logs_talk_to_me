// Copyright (c) 2025-2026 Michal Salanci
// SPDX-License-Identifier: MIT

/**
 * The list Lambda (lttm-list-conversations): GET /conversations, for alexandra.sh --history.
 *
 * It returns the conversations' rows from the DynamoDB conversations table, most recent first, without invoking
 * the agent. The stream Lambda writes one row per session: its timestamps, question count, title and user.
 *
 * Environment: CONVERSATIONS_TABLE (the conversations table), CONVERSATIONS_REGION (its region, var.project_region).
 */

import { DynamoDBClient, ScanCommand } from "@aws-sdk/client-dynamodb";

const CONVERSATIONS_TABLE = process.env.CONVERSATIONS_TABLE;
const ddb = new DynamoDBClient({ region: process.env.CONVERSATIONS_REGION });

/**
 * Turn a DynamoDB item from its attribute-value form ({ S: "text" }, { N: "3" }, …) into plain values.
 *
 * @param {object} item - The item as Scan returns it: each attribute's name mapped to its typed value.
 * @returns {object} Each attribute's name mapped to a string (S), a number (N), an array of strings (SS), a
 *     boolean (BOOL) or null (NULL). An attribute of any other type is left out.
 */
function unmarshallItem(item) {
  const result = {};
  for (const [key, attr] of Object.entries(item)) {
    if (attr.S !== undefined) {
      result[key] = attr.S;
    } else if (attr.N !== undefined) {
      result[key] = Number(attr.N);
    } else if (attr.SS !== undefined) {
      result[key] = attr.SS;
    } else if (attr.BOOL !== undefined) {
      result[key] = attr.BOOL;
    } else if (attr.NULL) {
      result[key] = null;
    }
  }
  return result;
}

/**
 * Answer GET /conversations with every conversation's row, the most recently active first.
 *
 * It scans the whole table page by page: one Scan returns at most 1 MB, so it follows LastEvaluatedKey to the last
 * page, and sorts only once it has every row. It lists the rows of every user, not only the caller's; a per-user list
 * would query the table's user_id-last_active-index instead.
 *
 * @param {object} event - The API Gateway proxy event; not used.
 * @returns {Promise<object>} The API Gateway proxy response: 200 with a JSON array of {session_id, created_at,
 *     last_active, title, question_count}, sorted by last_active, newest first (an empty array when the table is
 *     empty); 500 when a Scan failed, logged as one [LTTM:ListConversations] line.
 */
export async function handler(event) {
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  };

  try {
    const rows = [];
    let startKey;
    do {
      const page = await ddb.send(
        new ScanCommand({ TableName: CONVERSATIONS_TABLE, ExclusiveStartKey: startKey })
      );
      rows.push(...(page.Items || []));
      startKey = page.LastEvaluatedKey;
    } while (startKey);

    const items = rows.map((item) => {
      const plain = unmarshallItem(item);
      return {
        session_id: plain.session_id || "",
        created_at: plain.created_at || "",
        last_active: plain.last_active || "",
        title: plain.title || "",
        question_count: plain.question_count || 0,
      };
    });

    items.sort((a, b) => (b.last_active || "").localeCompare(a.last_active || ""));

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify(items),
    };
  } catch (err) {
    console.error("[LTTM:ListConversations] Scan failed:", err.message);
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: "Failed to list conversations" }),
    };
  }
}
