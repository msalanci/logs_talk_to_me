#!/bin/bash

# Usage: ./asklex.sh "your question here"

BOT_ID="CAR1VQEV3M"      # Bot ID
ALIAS_ID="EYMNM83OWY"    # Bot Alias ID
LOCALE="en_US"
SESSION_ID="terminal-session"

QUESTION="$1"

if [ -z "$QUESTION" ]; then
    echo "Usage: $0 \"Your question here\""
    exit 1
fi

echo "Alexandra asking Lex: $QUESTION"

aws lexv2-runtime recognize-text \
  --bot-id "$BOT_ID" \
  --bot-alias-id "$ALIAS_ID" \
  --locale-id "$LOCALE" \
  --session-id "$SESSION_ID" \
  --text "$QUESTION" \
  --query 'messages[0].content' \
  --output text