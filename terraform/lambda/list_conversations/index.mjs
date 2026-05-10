/**
 * Returns a JSON array of conversation metadata from the DynamoDB conversations table, sorted by last_active descending (most recent first)
 *
 * Connects to:
 *   - aws_dynamodb_table.conversations (dynamodb.tf) — reads metadata
 *   - aws_api_gateway_rest_api.lttm_stream (apigw.tf) — GET /conversations
 *   - alexandra.sh --history flag — CLI consumer
 */

import { DynamoDBClient, ScanCommand } from "@aws-sdk/client-dynamodb";

const CONVERSATIONS_TABLE = process.env.CONVERSATIONS_TABLE;
const ddb = new DynamoDBClient({ region: "eu-central-1" });

/**
 * Unmarshall a DynamoDB AttributeValue map into a plain JS object.
 *
 * Handles the four scalar shapes the conversations table uses:
 *   - { S: "..." }    → string
 *   - { N: "..." }    → number (coerced via Number(), NaN on bad input)
 *   - { SS: [...] }   → string set (array of strings)
 *   - { BOOL: ... }   → boolean
 *   - { NULL: true }  → null
 * Attributes of any other type are silently dropped.
 *
 *
 * @param {Record<string, object>} item - Raw DynamoDB item from Scan/Query.
 * @returns {Record<string, any>} Plain JS object with unwrapped values.
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
 * Performs a DynamoDB Scan on the conversations table, unmarshalls each item into a plain object with a fixed shape 
 * (session_id, created_at, last_active, title, question_count, services_used), and sorts the result by last_active
 * descending (most recent first) via string compare on timestamps.
 *
 * Returns:
 *   - 200 with a JSON array of conversation objects on success
 *   - 500 with { error } when the DynamoDB call throws
 *
 * @param {object} event - API Gateway AWS_PROXY event (unused; the handler returns the full table regardless of query string or auth claims)
 * @returns {Promise<{statusCode: number, headers: object, body: string}>} API Gateway proxy response; 
 * body is a JSON array of conversation objects on success or an { error } object on failure.
 */
export async function handler(event) {
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  };

  try {
    const result = await ddb.send(
      new ScanCommand({ TableName: CONVERSATIONS_TABLE })
    );

    const items = (result.Items || []).map((item) => {
      const plain = unmarshallItem(item);
      return {
        session_id: plain.session_id || "",
        created_at: plain.created_at || "",
        last_active: plain.last_active || "",
        title: plain.title || "",
        question_count: plain.question_count || 0,
        services_used: plain.services_used || [],
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
