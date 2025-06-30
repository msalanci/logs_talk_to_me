# lttm_utils/prompt_summarizer.py (Lambda Layer for LTTMv2)

"""
Prompt builder for the lambda_summarizer Lambda in LTTMv2.

- Generates a structured, consistent summarization prompt for Bedrock.
- Includes clear instructions for summarization, error classification, grouping, and presentation.
- Ensures reusable, testable prompt generation across summarizer workflows.
"""

import json

def build_standard_summarizer_prompt(intent: str, user_question: str, events_json_string: str) -> str:
    """
    Constructs a structured prompt for Bedrock to generate a concise, witty summary
    of AWS CloudTrail events matching the given intent and user question.

    The prompt enforces:
    - Clear error classification rules (SUCCESSFUL vs FAILED).
    - Logical grouping and summarization by user, IP, and date.
    - Respect for user-requested limits on result counts.
    - Light-hearted, human-readable summaries with humor.

    Args:
        intent (str): The classified intent for the current user query.
        user_question (str): The natural language question from the user.
        events_json_string (str): Pre-joined JSON string of CloudTrail events to summarize.

    Returns:
        str: A fully structured prompt to send to Bedrock for summarization generation.
    """
    user_question_intro = f'User asked: "{user_question}"\n\n'

    core = (
        f"You are summarizing AWS CloudTrail events for the intent '{intent}'.\n"
        "Read the user question carefuly, and only include the events that match the user's question.\n\n"


        "IMPORTANT INSTRUCTIONS:\n"
        "- If errorMessage is present and not null → classify the login as FAILED.\n"
        "- If errorMessage is missing or null → classify the login as SUCCESSFUL.\n\n"

        "For each login event:\n"
        "- State whether it was SUCCESSFUL or FAILED based on the above rule.\n"
        "- Summarize by date and number of attempts.\n"
        "- Group multiple identical API calls unless timestamps differ.\n"
        "- Group events by user and IP address when summarizing.\n\n"

        "LIMITING RESULTS:\n"
        "- If the user requests a limited number (e.g., 'last 2', 'last 5'), limit the output to that many unique results based on what the user asked for.\n"
        "- The type of result to limit (API calls, IP addresses, logins, usernames, etc.) depends on the user's intent and question.\n"
        "- Infer the correct grouping or dimension from the user's request.\n"
        "- Do not assume any default grouping (such as API calls) unless the user's request makes it clear.\n"
        "- Apply the limit to logical groups or summarized items, not to raw event rows.\n\n"

        "PRESENTATION:\n"
        "Do this summarization as a professional AI summarizer. Turn the following AWS CloudTrail events into a short, but explanatory summary.\n"
        "Keep it friendly, explain as a human being, but make sure the user understands the summary and gets the important information from it - this is our main goal.\n"
        "Always include AWS account into the summary.\n"
        "You MUST not invent data, only interpret what you see in the  Here are the events:\n\n"

        "Give a concise, human-readable summary. Number the results (1., 2., 3., …).\n\n"
        "Events:\n"
    )

    return user_question_intro + core + events_json_string


def build_funny_summarizer_prompt(intent: str, user_question: str, events_json_string: str) -> str:
    """
    Constructs a structured prompt for Bedrock to generate a concise, witty summary
    of AWS CloudTrail events matching the given intent and user question.

    The prompt enforces:
    - Clear error classification rules (SUCCESSFUL vs FAILED).
    - Logical grouping and summarization by user, IP, and date.
    - Respect for user-requested limits on result counts.
    - Light-hearted, human-readable summaries with humor.

    Args:
        intent (str): The classified intent for the current user query.
        user_question (str): The natural language question from the user.
        events_json_string (str): Pre-joined JSON string of CloudTrail events to summarize.

    Returns:
        str: A fully structured prompt to send to Bedrock for summarization generation.
    """
    user_question_intro = f'User asked: "{user_question}"\n\n'

    core = (
        f"You are witty and funny AWS CloudTrail summarizer. You are summarizing AWS CloudTrail events for the intent '{intent}'.\n"
        "Read the user question carefuly, and only include the events that match the user's question.\n\n"


        "IMPORTANT INSTRUCTIONS:\n"
        "- If errorMessage is present and not null → classify the login as FAILED.\n"
        "- If errorMessage is missing or null → classify the login as SUCCESSFUL.\n\n"

        "For each login event:\n"
        "- State whether it was SUCCESSFUL or FAILED based on the above rule.\n"
        "- Summarize by date and number of attempts.\n"
        "- Group multiple identical API calls unless timestamps differ.\n"
        "- Group events by user and IP address when summarizing.\n\n"

        "LIMITING RESULTS:\n"
        "- If the user requests a limited number (e.g., 'last 2', 'last 5'), limit the output to that many unique results based on what the user asked for.\n"
        "- The type of result to limit (API calls, IP addresses, logins, usernames, etc.) depends on the user's intent and question.\n"
        "- Infer the correct grouping or dimension from the user's request.\n"
        "- Do not assume any default grouping (such as API calls) unless the user's request makes it clear.\n"
        "- Apply the limit to logical groups or summarized items, not to raw event rows.\n\n"

        "PRESENTATION:\n"
        "Do this summarization as a funny AI summarizer, play with the words, make the jokes - but not offensive. Turn the following AWS CloudTrail events into a short, humorous and funny but explanatory summary.\n"
        "Keep it friendly and funny, but make sure the user still understands the summary and gets the important information from it, even behind the joke.\n"
        "Always include AWS account into the summary.\n"
        "You MUST not invent data, only interpret what you see in the  Here are the events:\n\n"

        "Give a concise, human-readable summary. Number the results (1., 2., 3., …).\n\n"
        "Events:\n"
    )

    return user_question_intro + core + events_json_string


def build_kids_summarizer_prompt(intent: str, user_question: str, events_json_string: str) -> str:
    """
    Constructs a structured prompt for Bedrock to generate a concise, witty summary
    of AWS CloudTrail events matching the given intent and user question.

    The prompt enforces:
    - Clear error classification rules (SUCCESSFUL vs FAILED).
    - Logical grouping and summarization by user, IP, and date.
    - Respect for user-requested limits on result counts.
    - Light-hearted, human-readable summaries with humor.

    Args:
        intent (str): The classified intent for the current user query.
        user_question (str): The natural language question from the user.
        events_json_string (str): Pre-joined JSON string of CloudTrail events to summarize.

    Returns:
        str: A fully structured prompt to send to Bedrock for summarization generation.
    """
    user_question_intro = f'User asked: "{user_question}"\n\n'

    core = (
        f"You are AWS CloudTrail summarizer for kids. You are summarizing AWS CloudTrail events for the intent '{intent}'.\n"
        "Read the user question carefuly, and only include the events that match the user's question.\n\n"


        "IMPORTANT INSTRUCTIONS:\n"
        "- If errorMessage is present and not null → classify the login as FAILED.\n"
        "- If errorMessage is missing or null → classify the login as SUCCESSFUL.\n\n"

        "For each login event:\n"
        "- State whether it was SUCCESSFUL or FAILED based on the above rule.\n"
        "- Summarize by date and number of attempts.\n"
        "- Group multiple identical API calls unless timestamps differ.\n"
        "- Group events by user and IP address when summarizing.\n\n"

        "LIMITING RESULTS:\n"
        "- If the user requests a limited number (e.g., 'last 2', 'last 5'), limit the output to that many unique results based on what the user asked for.\n"
        "- The type of result to limit (API calls, IP addresses, logins, usernames, etc.) depends on the user's intent and question.\n"
        "- Infer the correct grouping or dimension from the user's request.\n"
        "- Do not assume any default grouping (such as API calls) unless the user's request makes it clear.\n"
        "- Apply the limit to logical groups or summarized items, not to raw event rows.\n\n"

        "PRESENTATION:\n"
        "Do this summarization as kids are in the audiuence, play with the words, use stuff like lego bricks, toybox, toys, sweets, parents, teachers, friends, etc as an examples.\n"
        "Turn the following AWS CloudTrail events into a short, but kids friendly explanatory summary and make sure the user still understands it.\n"
        "Always include AWS account into the summary.\n"
        "You MUST not invent data, only interpret what you see in the  Here are the events:\n"
        "Number the results (1., 2., 3., …).\n\n"
        "Events:\n"
    )

    return user_question_intro + core + events_json_string


def select_summarizer_prompt(intent: str, user_question: str, events_json_string: str) -> str:
    """
    Selects and builds the appropriate summarizer prompt based on keywords in the user_question:
    - Uses funny summarizer if keywords match.
    - Uses kids summarizer if keywords match.
    - Defaults to standard summarizer otherwise.

    Args:
        intent (str): Detected intent.
        user_question (str): User's natural language question.
        events_json_string (str): JSON string of CloudTrail events.

    Returns:
        str: The constructed prompt.
    """
    user_question_lower = user_question.lower()
    funny_keywords = ["funny", "joke", "joker", "humor", "hilarious", "laugh"]
    kids_keywords = ["kid", "kids", "child", "children", "little", "baby", "toddler"]

    if any(keyword in user_question_lower for keyword in funny_keywords):
        return build_funny_summarizer_prompt(intent, user_question, events_json_string)
    elif any(keyword in user_question_lower for keyword in kids_keywords):
        return build_kids_summarizer_prompt(intent, user_question, events_json_string)
    else:
        return build_standard_summarizer_prompt(intent, user_question, events_json_string)
