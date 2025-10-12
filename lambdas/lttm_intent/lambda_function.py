# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Michal Salanci

"""
lambda_intent.py for LTTMv2

This Lambda function:
- Receives a user's natural question via API Gateway or another directly from CLI.
- Builds a structured Bedrock prompt to classify the user's intent and extract slots.
- Calls Bedrock (via the runtime API) to retrieve the intent and slots.
- Invokes the downstream `lambda_query` Lambda synchronously with the extracted intent and slots.
- Receives a summarized result from `lambda_query` and returns it to the caller.

Features:
- Structured logging to CloudWatch for prompt size, single-line prompt previews, raw Bedrock responses, and execution timing.
- Clean prompt generation using modular utilities (`build_intent_prompt`).
- Cleaned Bedrock responses for reliable parsing.
- Modular utility functions (`invoke_lambda_and_get_payload`, `clean_bedrock_response`) to simplify cross-Lambda invocation and output cleaning.

Intended for use in:
- LTTMv2 log summarization pipelines.
- Automated CloudTrail analysis pipelines triggered by user queries.
- Serverless event-driven architectures requiring AWS Lambda scalability.

Returns:
- JSON response containing:
    - `user_question`: the original user question.
    - `intent`: classified intent from Bedrock.
    - `slots`: extracted slots from the user's question.
    - `summary`: summarized result returned from `lambda-query`.
    - Appropriate HTTP status codes for upstream handling.

Usage:
- Deploy with environment variables:
    - `BEDROCK_INTENT_MODEL_REGION`
    - `BEDROCK_INTENT_MODEL_ID`
    - `LAMBDA_QUERY`
    - `MAIN_AWS_ACCOUNT_ID`
- Integrated with AWS Lambda Layers containing `lttm_utils`.
"""

__all__ = ["lambda_handler"]

import json
import boto3
import os
import time
from lttm_utils.prompt_intent import build_intent_prompt
from lttm_utils.utils import (
    log_status,
    set_log_prefix,
    log_prompt_size,
    single_line_json_log,
    single_line_string_log,
    log_lambda_duration,
    clean_bedrock_response,
    invoke_lambda_and_get_payload
)

# VARIABLES
# Environment variables
BEDROCK_INTENT_MODEL_REGION = os.environ["BEDROCK_INTENT_MODEL_REGION"]
BEDROCK_INTENT_MODEL_ID = os.environ["BEDROCK_INTENT_MODEL_ID"]
LAMBDA_QUERY_FUNCTION = os.environ["LAMBDA_QUERY"]
MAIN_AWS_ACCOUNT_ID = os.environ["MAIN_AWS_ACCOUNT_ID"]

# AWS Clients
bedrock_runtime = boto3.client("bedrock-runtime", BEDROCK_INTENT_MODEL_REGION)
lambda_client = boto3.client("lambda")

# Set CloudWatch prefix
set_log_prefix("[lambda-intent]")

# LAMBDA HANDLER
def lambda_handler(event, context):
    """
    Lambda entry point for LTTMv2 Intent Detection.

    This Lambda:
    - Extracts the user's natural language question from the event.
    - Builds a Bedrock prompt to classify the intent and extract slots.
    - Calls Bedrock to retrieve the intent and slots.
    - Invokes the downstream `lambda-query` Lambda with detected intent and slots.
    - Returns the query summary to the user.

    Logs prompt size, single-line prompt, Bedrock response, and execution timing
    for observability in CloudWatch.

    Args:
        event (dict): Incoming Lambda event containing "user_question".
        context: Lambda context (unused).

    Returns:
        dict: Structured API Gateway-compatible response containing intent, slots, and summary.
    """
    start_time = time.time()  # Capture Lambda start time for duration measurement

    # Extract and validate user question - Support both Lambda direct invoke and API Gateway
    if "body" in event and isinstance(event["body"], str):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            body = {}
    else:
        body = event
    user_question = body.get("user_question", "").strip()
    # user_question = event.get("user_question", "").strip() ### for direct only. remove whole if body... + user_question = body... 
    
    if not user_question:
        log_lambda_duration(start_time, label="lambda-intent execution (early exit - no question)")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing user_question in request"})
        }

    # Build structured prompt for Bedrock
    prompt = build_intent_prompt(user_question, MAIN_AWS_ACCOUNT_ID)
    log_status(f"Intent: Prompting Bedrock model: {BEDROCK_INTENT_MODEL_ID} in region: {BEDROCK_INTENT_MODEL_REGION}")
    log_status(f"Intent: Prompt sent to Bedrock: {single_line_string_log(prompt)}")
    log_prompt_size(prompt, prefix="[Prompt Size] ")

    try:
        # Call Bedrock for intent and slot extraction
        bedrock_start = time.time()
        response = bedrock_runtime.converse(
            modelId=BEDROCK_INTENT_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 1000}
        )
        bedrock_elapsed = (time.time() - bedrock_start) * 1000
        log_status(f"{BEDROCK_INTENT_MODEL_ID} responded in {bedrock_elapsed:.2f} ms")

    except Exception as e:
        log_status(f"Bedrock error: {str(e)}")
        log_lambda_duration(start_time, label="lambda-intent execution (Bedrock error)")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }

    # Clean and parse Bedrock response
    model_response = response["output"]["message"]["content"][0]["text"]
    model_response_clean = clean_bedrock_response(model_response)

    try:
        # Log cleaned response in single-line JSON format
        log_status(f"Intent: Bedrock full response: {single_line_json_log(model_response_clean)}")

        # Extract intent and slots
        parsed = json.loads(model_response_clean)
        intent = parsed.get("intent", "NO_INTENT")
        slots = parsed.get("slots", {})

        # remove the style keywords from the slots, if present
        # STYLE_KEYWORDS = {"kids", "kid", "child", "children", "funny", "joke", "joker", "humor", "hilarious", "laugh"}

        # for slot_name, slot_value in parsed.get("slots", {}).items():
        #     if isinstance(slot_value, str) and slot_value.lower() in STYLE_KEYWORDS:
        #         parsed["slots"][slot_name] = None

        # Prepare payload for lambda_query invocation
        log_status("Preparing payload for lambda_query invocation")
        lambda_payload = {
            "user_question": user_question,
            "intent": intent,
            "slots": slots
        }
        log_status("Invoking lambda_query with extracted intent and slots.")

        # Synchronously invoke lambda-query using utility and parse returned summary
        query_result = invoke_lambda_and_get_payload(lambda_client, LAMBDA_QUERY_FUNCTION, lambda_payload)
        summary = query_result.get("message", "No summary returned.")

        # Log summary in single-line format for readability
        log_status(f"Summary received from lttm-v2-lambda-query: {single_line_string_log(summary)}")

        # Log execution duration for the Lambda
        log_lambda_duration(start_time, label="lttm-v2-lambda-intent execution")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "user_question": user_question,
                "intent": intent,
                "slots": slots,
                "summary": summary
            })
        }

    except Exception as e:
        log_status(f"Error parsing response or invoking lambda_query: {str(e)}")
        log_lambda_duration(start_time, label="lttm-v2-lambda-intent execution (error)")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "intent": "NO_INTENT",
                "user_question": user_question,
                "raw": model_response,
                "error": str(e)
            })
        }
