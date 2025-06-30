"""
lambda_query.py for LTTMv2

This Lambda function:
- Receives `user_question`, `intent`, and `slots` from `lambda-intent`.
- Generates a CloudTrail Lake SQL query using the Bedrock model.
- Executes the SQL query on CloudTrail Lake and fetches results.
- Forwards the results to the summarizer Lambda for a concise summary.
- Returns the summary back to the user or calling service.

Features:
- Structured CloudWatch logging for prompt size, single-line prompt previews, Bedrock responses, SQL generation, and execution timing.
- Modular prompt generation via `build_query_prompt`.
- Clean Bedrock output processing via `clean_bedrock_response`.
- Modular cross-Lambda invocation via `invoke_lambda_and_get_payload`.

Intended for:
- LTTMv2 conversational CloudTrail log exploration.
- Event-driven serverless architectures needing scalable AWS log summarization pipelines.

Environment variables:
- `BEDROCK_QUERY_MODEL_REGION`
- `BEDROCK_QUERY_MODEL_ID`
- `CLOUDTRAIL_EVENT_DATA_STORE_ID`
- `LAMBDA_SUMMARIZER_FUNCTION`

Exports:
- `lambda_handler` for AWS Lambda execution entry point.
"""

__all__ = ["lambda_handler"]

import json
import boto3
import os
import time
from lttm_utils.prompt_query import build_query_prompt
from lttm_utils.utils import (
    log_status,
    set_log_prefix,
    log_prompt_size,
    single_line_string_log,
    log_lambda_duration,
    clean_bedrock_response,
    invoke_lambda_and_get_payload
)

# VARIABLES
# Environment variables
BEDROCK_QUERY_MODEL_REGION = os.environ["BEDROCK_QUERY_MODEL_REGION"]
BEDROCK_QUERY_MODEL_ID = os.environ["BEDROCK_QUERY_MODEL_ID"]
CLOUDTRAIL_EVENT_DATA_STORE_ID = os.environ["CLOUDTRAIL_EVENT_DATA_STORE_ID"]
CLOUDTRAIL_EVENT_DATA_STORE_REGION = os.environ["CLOUDTRAIL_EVENT_DATA_STORE_REGION"]
LAMBDA_SUMMARIZER_FUNCTION = os.environ["LAMBDA_SUMMARIZER_FUNCTION"]

# AWS Clients
bedrock_runtime = boto3.client("bedrock-runtime", BEDROCK_QUERY_MODEL_REGION)
cloudtrail_client = boto3.client("cloudtrail", region_name=CLOUDTRAIL_EVENT_DATA_STORE_REGION)
lambda_client = boto3.client("lambda")

# Set CloudWatch prefix
set_log_prefix("[lambda-query]")


# HELPER FUNCTIONS
# Generate SQL using Bedrock model
def generate_sql_from_model(user_question: str, intent: str, slots: dict) -> str | None:
    """
    Generates a valid CloudTrail Lake SQL query using the Bedrock model
    based on user question, detected intent, and extracted slots.

    Args:
        user_question (str): The natural-language question from the user.
        intent (str): Detected intent.
        slots (dict): Extracted slots to guide SQL generation.

    Returns:
        str | None: SQL query string or None if generation fails.
    """
    prompt = build_query_prompt(user_question, intent, slots)
    log_status(f"Query: Prompting Bedrock model: {BEDROCK_QUERY_MODEL_ID} in region: {BEDROCK_QUERY_MODEL_REGION}")
    log_status(f"Query: Prompt sent to Bedrock: {single_line_string_log(prompt)}")
    log_prompt_size(prompt, prefix="[Prompt Size] ")

    try:
        response = bedrock_runtime.converse(
            modelId=BEDROCK_QUERY_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 400, "temperature": 0.2}
        )
        raw_sql_text = response["output"]["message"]["content"][0]["text"]
        sql_text = clean_bedrock_response(raw_sql_text)

        # Validate SQL structure
        if not sql_text.lower().startswith("select"):
            log_status("Model output did not start with SELECT.")
            return None

        # Replace placeholder with actual Event Data Store ID
        sql_text = sql_text.replace("{EVENT_DATA_STORE_ID}", CLOUDTRAIL_EVENT_DATA_STORE_ID)

        # Log Bedrock raw response for traceability
        log_status(f"Query: Bedrock full response: {json.dumps(response)}")

        return sql_text

    except Exception as e:
        log_status(f"Bedrock invocation failed: {e}")
        return None


# Query the CloudTrail
def start_cloudtrail_query(sql: str) -> str | None:
    """
    Starts a CloudTrail Lake query with the provided SQL.

    Args:
        sql (str): SQL query to execute.

    Returns:
        str | None: Query ID if started successfully, else None.
    """
    try:
        return cloudtrail_client.start_query(QueryStatement=sql)["QueryId"]
    except Exception as e:
        log_status(f"Error starting query: {e}")
        return None


# Get query results
def get_query_results(query_id: str) -> list:
    """
    Polls CloudTrail Lake until the query finishes, fetching results.

    Args:
        query_id (str): Query ID to monitor.

    Returns:
        list: List of query result rows, empty if failed or cancelled.
    """
    attempts = 0
    while attempts < 15:
        resp = cloudtrail_client.get_query_results(QueryId=query_id)
        status = resp.get("QueryStatus")
        log_status(f"Query status: {status}")
        if status == "FINISHED":
            return resp.get("QueryResultRows", [])
        if status in ("FAILED", "CANCELLED"):
            return []
        attempts += 1
        time.sleep(2)
    return []

# Forward query to lambda_summarizer function using unified utility
def forward_to_summarizer(events: list, intent: str, user_question: str) -> str:
    """
    Sends fetched CloudTrail events to the summarizer Lambda for summarization.

    Args:
        events (list): List of CloudTrail events.
        intent (str): Intent for context.
        user_question (str): User's original question for context.

    Returns:
        str: Summary returned by the summarizer Lambda.
    """
    if not events:
        log_status("No matching events found to summarize.")
        return "No events found matching the query."

    payload = {
        "events": events,
        "intent": intent,
        "user_question": user_question
    }

    result = invoke_lambda_and_get_payload(lambda_client, LAMBDA_SUMMARIZER_FUNCTION, payload)
    return result.get("summary", "<no summary returned>")


# LAMBDA HANDLER
def lambda_handler(event, context):
    """
    Lambda entry point for LTTMv2 Query Lambda.

    This Lambda:
    - Accepts user_question, intent, and slots.
    - Generates a SQL query using Bedrock.
    - Executes the SQL query on CloudTrail Lake.
    - Forwards the results to the summarizer Lambda.
    - Returns the summary back to the user.

    Logs prompt sizes, query execution status, fetched event counts,
    and execution timing for clear CloudWatch observability.

    Args:
        event (dict): Incoming Lambda event.
        context: Lambda context (unused).

    Returns:
        dict: API Gateway-compatible structured response containing the summary.
    """
    start_ms = time.time()  # Track Lambda execution duration

    try:
        # Parse body or event for user_question, intent, and slots
        body = json.loads(event.get("body", json.dumps(event)))
        user_question = body.get("user_question", "")
        intent = body.get("intent")
        slots = body.get("slots", {})

        log_status(f"Intent={intent}  Slots={slots}")

        # Generate SQL using Bedrock
        sql_query = generate_sql_from_model(user_question, intent, slots)
        if not sql_query:
            log_lambda_duration(start_ms, label="lambda-query execution (SQL gen fail)")
            return {"statusCode": 400, "body": json.dumps({"message": "SQL generation failed"})}

        log_status(f"Generated SQL: {single_line_string_log(sql_query)}")

        # Execute SQL on CloudTrail Lake
        query_id = start_cloudtrail_query(sql_query)
        if not query_id:
            log_lambda_duration(start_ms, label="lambda-query execution (CloudTrail fail)")
            return {"statusCode": 500, "body": json.dumps({"message": "Failed to start CloudTrail query"})}

        # Retrieve query results
        events = get_query_results(query_id)
        log_status(f"Fetched {len(events)} events")

        # Forward results to summarizer Lambda and get summary
        summary = forward_to_summarizer(events, intent, user_question)

        # Log execution duration
        log_lambda_duration(start_ms, label="lttm-v2-lambda-query execution")

        # Return summary to user
        return {"message": summary}

    except Exception as e:
        log_status(f"Unhandled error: {e}")
        log_lambda_duration(start_ms, label="lambda-query execution (error)")
        return {"statusCode": 500, "body": json.dumps({"message": "Internal error"})}
