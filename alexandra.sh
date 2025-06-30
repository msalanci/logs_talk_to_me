#!/bin/bash

# Usage: ./asklex.sh "your question here"

# BOT_ID="MYBOTID123"     # Put your own, from the console - Lex > Bots > Bot: YourBot
# ALIAS_ID="MYALIASID"    # Put your own, from the console- Lex > Bots > Bot: YourBot > Aliases > Alias: PROD
BOT_ID="A182YUKUPH"     # Put your own, from the console - Lex > Bots > Bot: YourBot
ALIAS_ID="Y038ULUP9Y"    # Put your own, from the console- Lex > Bots > Bot: YourBot > Aliases > Alias: PROD
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