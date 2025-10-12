# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Michal Salanci

"""
lambda_summarizer.py for LTTMv2

This Lambda function:
- Receives a list of CloudTrail events, a detected intent, and the original user question.
- Builds a structured Bedrock prompt to generate a concise, human-friendly summary of the events.
- Calls Bedrock (via the runtime API) to generate the summary text.
- Returns the summary back to `lambda-query` or the calling service.

Features:
- Structured CloudWatch logging for prompt size, single-line prompt previews, Bedrock response timing, and execution timing.
- Modular prompt generation via `build_summarizer_prompt`.
- Clean Bedrock output processing via `clean_bedrock_response` for consistent summarization.
- Lightweight design for scalability within AWS Lambda pipelines.

Intended for:
- LTTMv2 log summarization pipelines following CloudTrail analysis.
- Serverless event-driven architectures requiring scalable AWS summarization layers.

Environment variables:
- `BEDROCK_SUMMARIZER_MODEL_ID`

Exports:
- `lambda_handler` for AWS Lambda execution entry point.
"""

__all__ = ["lambda_handler"]

import json
import os
import time
import boto3
# from lttm_utils.prompt_summarizer import build_summarizer_prompt
from lttm_utils import prompt_summarizer
from lttm_utils.utils import (
    log_status,
    set_log_prefix,
    log_prompt_size,
    single_line_string_log,
    log_lambda_duration,
    clean_bedrock_response
)

# VARIABLES
# Environment variables
BEDROCK_SUMMARIZER_MODEL_REGION = os.environ["BEDROCK_SUMMARIZER_MODEL_REGION"]
BEDROCK_SUMMARIZER_MODEL_ID = os.environ["BEDROCK_SUMMARIZER_MODEL_ID"]

# AWS Clients
bedrock_runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_SUMMARIZER_MODEL_REGION)

# Set CloudWatch prefix
set_log_prefix("[lambda-summarizer]")

# HELPER FUNCTION
def generate_summary(intent: str, user_question: str, events: list) -> str:
    """
    Generates a human-friendly summary from CloudTrail events using Bedrock.

    Args:
        intent (str): Detected intent.
        user_question (str): User's question for context.
        events (list): List of CloudTrail event dictionaries.

    Returns:
        str: Human-friendly summary text.
    """
    # Serialize events for the prompt
    events_json = "\n".join(json.dumps(e) for e in events)

    # Build prompt based on user question style
    prompt = prompt_summarizer.select_summarizer_prompt(intent, user_question, events_json)
    log_status(f"Intent: Prompting Bedrock model: {BEDROCK_SUMMARIZER_MODEL_ID} in region: {BEDROCK_SUMMARIZER_MODEL_REGION}")
    log_status(f"Summary: Prompt sent to Bedrock: {single_line_string_log(prompt)}")
    log_prompt_size(prompt, prefix="[Prompt Size] ")

    try:
        bedrock_start = time.time()
        response = bedrock_runtime.converse(
            modelId=BEDROCK_SUMMARIZER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 400, "temperature": 0.5}
        )
        bedrock_ms = (time.time() - bedrock_start) * 1000
        log_status(f"Bedrock inference time: {bedrock_ms:.2f} ms")

        text_chunks = response["output"]["message"]["content"]
        summary_raw = "".join(chunk["text"] for chunk in text_chunks)
        summary = clean_bedrock_response(summary_raw)

        log_status(f"Summary: Bedrock full response: {single_line_string_log(summary)}")
        return summary

    except Exception as e:
        log_status(f"Summarizer Bedrock invocation error: {e}")
        return f"Summarizer error: {str(e)}"


# LAMBDA HANDLER
def lambda_handler(event, context):
    """
    Lambda entry point for LTTMv2 Summarizer.

    Returns:
        dict: Dictionary containing the summary under the "summary" key.
    """
    start_time = time.time()

    # Extract payload
    events = event.get("events", [])
    intent = event.get("intent", "UnknownIntent")
    user_question = event.get("user_question", "")

    log_status(f"Received {len(events)} events for summarization. Intent={intent}")

    summary = generate_summary(intent, user_question, events)

    log_status("Handing summary back to lttm-v2-lambda-query for post-processing.")
    log_lambda_duration(start_time, label="lttm-v2-lambda-summarizer execution")

    return {"summary": summary}

