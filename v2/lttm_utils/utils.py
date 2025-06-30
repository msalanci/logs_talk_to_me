# lttm_utils/utils.py (Lambda Layer for LTTMv2)

"""
Utility functions for structured logging, prompt analysis, and CloudWatch-friendly formatting
used across LTTMv2 Lambda functions.
"""

import json
import time
import re

# Global log prefix for consistent CloudWatch log filtering
PREFIX = "[lambda]"


def set_log_prefix(prefix: str) -> None:
    """
    Sets the global log prefix for CloudWatch logs.

    Args:
        prefix (str): Prefix string (e.g., '[lambda-query]') to prepend to log messages.
    """
    global PREFIX
    PREFIX = prefix


def log_status(message: str) -> None:
    """
    Logs a message to CloudWatch with the current global prefix.

    Args:
        message (str): Message to log.
    """
    print(f"{PREFIX} {message}")


def log_prompt_size(prompt: str, prefix: str = "") -> None:
    """
    Logs word count, character count, and approximate token estimate of a prompt for Bedrock model usage monitoring.

    Args:
        prompt (str): Prompt string to analyze.
        prefix (str): Optional additional prefix for clarity (e.g., '[Prompt Size] ').
    """
    word_count = len(prompt.split())
    char_count = len(prompt)
    token_estimate = int(word_count * 1.5)  # Rough token estimation: 1.5 tokens per word

    log_status(f"{prefix}Prompt length: ~{word_count} words (~{char_count} chars, ~{token_estimate} tokens)")


def single_line_json_log(json_string: str) -> str:
    """
    Converts a JSON string into a single-line string for cleaner CloudWatch logging.
    If parsing fails, flattens newlines without attempting to re-serialize.

    Args:
        json_string (str): JSON string to flatten.

    Returns:
        str: Single-line JSON string or fallback cleaned string.
    """
    try:
        parsed = json.loads(json_string)
        return json.dumps(parsed)
    except Exception as e:
        cleaned = json_string.replace("\n", " ").replace("\r", " ")
        return f"(non-JSON or parse error: {e}): {cleaned}"


def single_line_string_log(multiline_string: str) -> str:
    """
    Converts any multi-line string into a single-line string for CloudWatch log readability.

    Args:
        multiline_string (str): String potentially containing newlines.

    Returns:
        str: Single-line version of the input string.
    """
    return multiline_string.replace("\n", " ").replace("\r", " ")


def log_lambda_duration(start_ms: float, label: str = "Lambda execution") -> None:
    """
    Logs the elapsed execution time of a Lambda in milliseconds to CloudWatch, used for consistent runtime monitoring.

    Usage:
        start_ms = time.time()
        ...
        log_lambda_duration(start_ms)

    Args:
        start_ms (float): Start timestamp from `time.time()`.
        label (str): Optional label for clarity in logs (e.g., 'lambda-intent execution').
    """
    elapsed_ms = (time.time() - start_ms) * 1000
    log_status(f"{label} completed in {elapsed_ms:.2f} ms")


def clean_bedrock_response(text: str) -> str:
    """
    Cleans Bedrock model output by removing ```json and ``` wrappers,
    ensuring the content can be parsed safely as JSON or used cleanly.

    Args:
        text (str): Raw Bedrock model output.

    Returns:
        str: Cleaned output string.
    """
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    elif cleaned.startswith("```"):
        cleaned = re.sub(r"^```\s*", "", cleaned, flags=re.IGNORECASE)
    if cleaned.endswith("```"):
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def invoke_lambda_and_get_payload(lambda_client, function_name: str, payload: dict) -> dict:
    """
    Invokes another Lambda synchronously and returns the parsed JSON payload.

    Args:
        lambda_client: Boto3 Lambda client.
        function_name (str): Name or ARN of the target Lambda.
        payload (dict): Payload dictionary to send.

    Returns:
        dict: Parsed JSON response payload.
    """
    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8")
        )
        return json.load(response["Payload"])
    except Exception as e:
        log_status(f"Error invoking {function_name}: {e}")
        return {"error": str(e)}