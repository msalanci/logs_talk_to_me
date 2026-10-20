#!/usr/bin/env bash
# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# alexandra.sh — CLI for LTTM (Logs Talk To Me) v3 — streaming endpoint
#
# Flow:
#   alexandra.sh → REST API Gateway (eu-central-1) → Lambda stream shim
#   → Bedrock AgentCore lttm_supervisor_stream (us-west-2)

set -euo pipefail

START_TIME=$(date +%s)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_FILE="$HOME/.lttm_session"
# A session ID is a UUID in lower case, as this script makes them; no other ID is saved, or sent with a question.
# Every other form breaks on the way: AgentCore Runtime takes 33 to 256 characters, and
# AgentCore Memory at most 100 letters, digits, '-' and '_'. Memory also finds a session's summary by its ID as a
# namespace prefix, next to the facts in "default": IDs of different lengths, or one starting with "default", would
# mix sessions' memory. One case only, because the same UUID in upper case is a different session everywhere.
# The stream shim and the runtime check the same rule. The hex digits are listed out because a range such as a-f
# can match other letters in some locales.
HEX='[0123456789abcdef]'
SESSION_ID_PATTERN="^${HEX}{8}-${HEX}{4}-${HEX}{4}-${HEX}{4}-${HEX}{12}\$"
TOKEN_FILE="$HOME/.lttm_token"
COGNITO_REGION="${COGNITO_REGION:-eu-central-1}"

# ~/.lttm_token holds the refresh token, so only this user may read what the script writes;
# a token file made before (default umask, 644) is closed too.
umask 077
if [[ -f "$TOKEN_FILE" ]]; then
  chmod 600 "$TOKEN_FILE"
fi

TEMP_HEADERS="/tmp/lttm_stream_headers_$$.txt"
CURL_ERR_LOG="/tmp/lttm_curl_err_$$.log"
PIPE_STATUS_FILE="/tmp/lttm_pipe_status_$$.txt"
GAME_STATUS_FILE="/tmp/lttm_game_status_$$.txt"
GAME_ANSWER_FILE="/tmp/lttm_game_answer_$$.txt"
DELETE_RESP="/tmp/lttm_delete_resp_$$.json"

# Delete the temporary files of this run. The EXIT trap calls it.
#
# Args:
#   None.
#
# Returns:
#   0. Removes the response headers, the curl error log, the pipe and game status files, the game
#   answer, the delete response and the file with the Authorization header.
cleanup() {
  rm -f \
    "$TEMP_HEADERS" \
    "$CURL_ERR_LOG" \
    "$PIPE_STATUS_FILE" \
    "$GAME_STATUS_FILE" \
    "$GAME_ANSWER_FILE" \
    "$DELETE_RESP" \
    "${AUTH_HEADER_FILE:-}"
}
trap cleanup EXIT

# Show the server-sent events on stdin: the status, guard and token lines, the answer, and errors.
# In game mode the status and the answer go to the game's files instead of the terminal.
#
# Args:
#   None. Reads LTTM_CLIENT_MODE ("normal" or "game"), LTTM_GAME_STATUS_FILE and
#   LTTM_GAME_ANSWER_FILE from the environment.
#
# Returns:
#   0, also after an error event: the error is printed, and an error event does not fail the
#   command when HTTP returned 200.
run_sse_parser() {
  python3 -u -c '
import sys
import os
import json

mode = os.environ.get("LTTM_CLIENT_MODE", "normal")
game_status_file = os.environ.get("LTTM_GAME_STATUS_FILE", "")
game_answer_file = os.environ.get("LTTM_GAME_ANSWER_FILE", "")

data_lines = []
saw_terminal_event = False


def write_file(path, text):
    """Replace a file with a text that ends with a newline.

    Args:
        path: The file to write; an empty path writes nothing.
        text: The text; None writes an empty line.

    Returns:
        None.
    """
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text or "")
        if not (text or "").endswith("\n"):
            f.write("\n")


def emit_status(prefix, message):
    """Show a status line: on the terminal, or in game mode in the game status file.

    Args:
        prefix: The emoji in front of the line.
        message: The status text; None shows the prefix alone.

    Returns:
        None.
    """
    message = message or ""
    if mode == "game":
        write_file(game_status_file, f"{prefix} {message}")
    else:
        print(f"{prefix} {message}", flush=True)


def emit_result(text):
    """Show the answer, and note that the stream reached its end.

    Args:
        text: The answer; None shows an empty answer.

    Returns:
        None. In game mode it writes the answer to the game answer file, and GAME_OVER to the
        game status file.
    """
    global saw_terminal_event
    saw_terminal_event = True
    text = text or ""
    if mode == "game":
        write_file(game_answer_file, text)
        write_file(game_status_file, "GAME_OVER")
    else:
        print("", flush=True)
        print(text, flush=True)


def emit_error(text):
    """Show an error on stderr, and note that the stream reached its end.

    Args:
        text: The error message; None shows an empty one.

    Returns:
        None. In game mode it writes the error to the game answer file, and GAME_OVER to the
        game status file.
    """
    global saw_terminal_event
    saw_terminal_event = True
    text = text or ""
    if mode == "game":
        write_file(game_answer_file, f"❌ {text}")
        write_file(game_status_file, "GAME_OVER")
    else:
        print(f"❌ {text}", file=sys.stderr, flush=True)


def handle_event(payload):
    """Show one event by its type: status, guard, tokens, error, result or done.

    An event of any other type is ignored, so the CLI keeps working when the runtime sends new
    event types.

    Args:
        payload: The JSON data of one SSE frame; an empty payload is ignored.

    Returns:
        None. A payload that is not JSON prints a warning on stderr.
    """
    if not payload:
        return

    try:
        event = json.loads(payload)
    except Exception:
        print(f"⚠️ Could not parse SSE frame: {payload[:300]}", file=sys.stderr, flush=True)
        return

    event_type = event.get("type", "")
    message = event.get("message", "")

    if event_type == "status":
        emit_status("⏳", message)
    elif event_type == "guard":
        emit_status("🛡️", message)
    elif event_type == "tokens":
        emit_status("💰", message)
    elif event_type == "error":
        emit_error(message)
    elif event_type == "result":
        emit_result(event.get("result", ""))
    elif event_type == "done":
        if mode == "game":
            write_file(game_status_file, "GAME_OVER")
    else:
        return


def flush_event():
    """Show the frame whose data lines were collected so far, and start a new frame.

    Args:
        None.

    Returns:
        None.
    """
    global data_lines
    if not data_lines:
        return
    payload = "\n".join(data_lines).strip()
    data_lines = []
    handle_event(payload)


for raw_line in sys.stdin:
    line = raw_line.rstrip("\r\n")

    if line == "":
        flush_event()
        continue

    if line.startswith(":"):
        continue

    if line.startswith("data:"):
        data_lines.append(line[5:].lstrip(" "))

# Handles final frame if stream ends without blank line/newline.
flush_event()

if mode == "game" and not saw_terminal_event:
    write_file(game_status_file, "GAME_OVER")

sys.exit(0)
'
}

if [[ -z "${LTTM_STREAM_API_URL:-}" ]]; then
  if [[ -d "$SCRIPT_DIR/terraform" ]]; then
    LTTM_STREAM_API_URL=$(terraform -chdir="$SCRIPT_DIR/terraform" output -raw lttm_stream_api_url 2>/dev/null || true)
  fi

  if [[ -z "${LTTM_STREAM_API_URL:-}" ]]; then
    echo "ERROR: LTTM_STREAM_API_URL is not set and could not be auto-discovered." >&2
    echo "Run: export LTTM_STREAM_API_URL=\$(terraform -chdir=terraform output -raw lttm_stream_api_url)" >&2
    exit 1
  fi
fi

LTTM_STREAM_API_URL="${LTTM_STREAM_API_URL%/}"
CONVERSATIONS_API_URL="${LTTM_STREAM_API_URL%/ask}/conversations"

if [[ -z "${COGNITO_USER_POOL_ID:-}" || -z "${COGNITO_CLIENT_ID:-}" ]]; then
  if [[ -d "$SCRIPT_DIR/terraform" ]]; then
    COGNITO_USER_POOL_ID=$(terraform -chdir="$SCRIPT_DIR/terraform" output -raw cognito_user_pool_id 2>/dev/null || true)
    COGNITO_CLIENT_ID=$(terraform -chdir="$SCRIPT_DIR/terraform" output -raw cognito_app_client_id 2>/dev/null || true)
  fi

  if [[ -z "${COGNITO_USER_POOL_ID:-}" || -z "${COGNITO_CLIENT_ID:-}" ]]; then
    echo "ERROR: COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are not set and could not be auto-discovered." >&2
    exit 1
  fi
fi

# Run `aws cognito-idp <operation>` with its JSON input read from a private temporary file. The
# input holds passwords and tokens, which never go on the command line: every user on the machine
# sees a command's arguments in `ps`, and the CLI's shorthand syntax breaks on a comma in a password.
#
# Args:
#   $1: The cognito-idp operation, such as initiate-auth. Its JSON input comes on stdin.
#
# Returns:
#   The exit status of the aws command. Prints its output, stdout and stderr together.
cognito_call() {
  local input res rc
  input=$(mktemp "${TMPDIR:-/tmp}/lttm-cognito.XXXXXX") || return 1   # mktemp creates it 600
  cat > "$input"
  rc=0
  res=$(aws cognito-idp "$1" --cli-input-json "file://$input" --region "$COGNITO_REGION" 2>&1) || rc=$?
  rm -f "$input"
  printf '%s\n' "$res"
  return "$rc"
}

# Read the cached ID token from ~/.lttm_token.
#
# Args:
#   None.
#
# Returns:
#   0. Prints the ID token, or an empty line when there is no token file, it can't be read, or
#   the token expires within the next 60 seconds.
get_cached_token() {
  [[ -f "$TOKEN_FILE" ]] || { echo ""; return; }

  TOKEN_FILE_PATH="$TOKEN_FILE" python3 -c '
import json, os, time
try:
    with open(os.environ["TOKEN_FILE_PATH"], "r", encoding="utf-8") as f:
        d = json.load(f)
    print(d.get("id_token", "") if time.time() < d.get("expires_at", 0) - 60 else "")
except Exception:
    print("")
' 2>/dev/null
}

# Get a new ID token from Cognito with the refresh token saved in ~/.lttm_token.
#
# Args:
#   None.
#
# Returns:
#   0, and prints the new ID token, which it also saves with its expiry time in ~/.lttm_token.
#   1 when there is no refresh token, or Cognito refuses it.
refresh_token() {
  [[ -f "$TOKEN_FILE" ]] || return 1

  local rt
  rt=$(TOKEN_FILE_PATH="$TOKEN_FILE" python3 -c '
import json, os
try:
    with open(os.environ["TOKEN_FILE_PATH"], "r", encoding="utf-8") as f:
        print(json.load(f).get("refresh_token", ""))
except Exception:
    print("")
' 2>/dev/null || true)

  [[ -n "$rt" ]] || return 1

  local res
  if ! res=$(CLIENT_ID="$COGNITO_CLIENT_ID" REFRESH_TOKEN="$rt" python3 -c '
import json, os
print(json.dumps({"ClientId": os.environ["CLIENT_ID"], "AuthFlow": "REFRESH_TOKEN_AUTH",
                  "AuthParameters": {"REFRESH_TOKEN": os.environ["REFRESH_TOKEN"]}}))' | cognito_call initiate-auth 2>/dev/null); then
    return 1
  fi

  RES_JSON="$res" TOKEN_FILE_PATH="$TOKEN_FILE" python3 -c '
import json, os, sys, time
try:
    res = json.loads(os.environ["RES_JSON"])
    auth = res.get("AuthenticationResult", {})
    tok = auth.get("IdToken", "")
    if not tok:
        sys.exit(1)
    with open(os.environ["TOKEN_FILE_PATH"], "r", encoding="utf-8") as f:
        d = json.load(f)
    d["id_token"] = tok
    d["expires_at"] = int(time.time()) + auth.get("ExpiresIn", 3600)
    with open(os.environ["TOKEN_FILE_PATH"], "w", encoding="utf-8") as f:
        json.dump(d, f)
    print(tok)
except Exception:
    sys.exit(1)
' 2>/dev/null || return 1
}

# Sign in to Cognito with a user name and password typed in the terminal. On a first login,
# Cognito asks for a new password, and so does this function.
#
# Args:
#   None. Reads the user name and the passwords from the terminal.
#
# Returns:
#   0, and prints the ID token, which it saves with the refresh token and the expiry time in
#   ~/.lttm_token. Prompts and messages go to stderr. Exits with 1 when Cognito refuses the
#   sign-in or the new password.
login_with_credentials() {
  echo "🔐 LTTM authentication required" >&2
  read -rp "Username: " LTTM_USER
  read -rsp "Password: " LTTM_PASS
  echo "" >&2

  local res
  if ! res=$(CLIENT_ID="$COGNITO_CLIENT_ID" USERNAME="$LTTM_USER" PASSWORD="$LTTM_PASS" python3 -c '
import json, os
print(json.dumps({"ClientId": os.environ["CLIENT_ID"], "AuthFlow": "USER_PASSWORD_AUTH",
                  "AuthParameters": {"USERNAME": os.environ["USERNAME"], "PASSWORD": os.environ["PASSWORD"]}}))' | cognito_call initiate-auth); then
    echo "ERROR: Auth failed — $res" >&2
    exit 1
  fi

  local challenge
  challenge=$(echo "$res" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ChallengeName", ""))' 2>/dev/null || echo "")

  if [[ "$challenge" == "NEW_PASSWORD_REQUIRED" ]]; then
    echo "⚠️  Password change required (first login)." >&2
    read -rsp "New password: " NEW_PASS
    echo "" >&2

    local sess
    sess=$(echo "$res" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Session", ""))' 2>/dev/null)

    # respond-to-auth-challenge, not the admin- variant: it needs no AWS credentials for the
    # user pool, only the session from initiate-auth
    if ! res=$(CLIENT_ID="$COGNITO_CLIENT_ID" USERNAME="$LTTM_USER" NEW_PASSWORD="$NEW_PASS" SESSION="$sess" python3 -c '
import json, os
print(json.dumps({"ClientId": os.environ["CLIENT_ID"], "ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": os.environ["SESSION"],
                  "ChallengeResponses": {"USERNAME": os.environ["USERNAME"], "NEW_PASSWORD": os.environ["NEW_PASSWORD"]}}))' | cognito_call respond-to-auth-challenge); then
      echo "ERROR: Password change failed — $res" >&2
      exit 1
    fi
  fi

  RES_JSON="$res" TOKEN_FILE_PATH="$TOKEN_FILE" python3 -c '
import json, os, sys, time
try:
    res = json.loads(os.environ["RES_JSON"])
    auth = res.get("AuthenticationResult", {})
    tok = auth.get("IdToken", "")
    if not tok:
        print("ERROR: No ID token in response", file=sys.stderr)
        sys.exit(1)
    data = {
        "id_token": tok,
        "refresh_token": auth.get("RefreshToken", ""),
        "expires_at": int(time.time()) + auth.get("ExpiresIn", 3600),
    }
    with open(os.environ["TOKEN_FILE_PATH"], "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(tok)
except Exception:
    sys.exit(1)
' 2>/dev/null

  echo "✅ Authenticated as $LTTM_USER" >&2
}

JWT_TOKEN=$(get_cached_token)
if [[ -z "$JWT_TOKEN" ]]; then
  JWT_TOKEN=$(refresh_token 2>/dev/null || echo "")
fi
if [[ -z "$JWT_TOKEN" ]]; then
  JWT_TOKEN=$(login_with_credentials)
fi

# curl reads the token from a private file (-H @file), because every user on the machine sees a
# command's arguments in `ps` for as long as it runs. printf is a shell builtin, so its arguments
# never show there.
AUTH_HEADER_FILE=$(mktemp "${TMPDIR:-/tmp}/lttm-auth.XXXXXX")
printf 'Authorization: %s\n' "$JWT_TOKEN" > "$AUTH_HEADER_FILE"

# Make a new session ID: a random UUID, in lower case.
#
# Args:
#   None.
#
# Returns:
#   0. Prints the UUID.
generate_uuid() {
  python3 -c 'import uuid; print(str(uuid.uuid4()))'
}

NEW_SESSION=false
NO_MEMORY=false
GAME_MODE=false
SESSION_ID=""
QUESTION=""
SHOW_HISTORY=false
SHOW_HEALTH=false
SHOW_SERVICES=false
DELETE_SESSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --new)
      NEW_SESSION=true
      shift
      ;;
    --clean)
      NO_MEMORY=true
      shift
      ;;
    --notboring)
      GAME_MODE=true
      shift
      ;;
    --history)
      SHOW_HISTORY=true
      shift
      ;;
    --health)
      SHOW_HEALTH=true
      shift
      ;;
    --services)
      SHOW_SERVICES=true
      shift
      ;;
    --delete)
      if [[ $# -ge 2 && "$2" != --* ]]; then
        DELETE_SESSION="$2"
        shift 2
      else
        DELETE_SESSION="__current__"
        shift
      fi
      ;;
    --session)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "ERROR: --session requires a non-empty session ID argument." >&2
        exit 1
      fi
      SESSION_ID="$2"
      shift 2
      ;;
    *)
      if [[ -n "$QUESTION" ]]; then
        echo "ERROR: Multiple question arguments provided. Wrap your question in quotes." >&2
        exit 1
      fi
      QUESTION="$1"
      shift
      ;;
  esac
done

MODE_COUNT=0
if $SHOW_HISTORY; then MODE_COUNT=$((MODE_COUNT + 1)); fi
if $SHOW_HEALTH; then MODE_COUNT=$((MODE_COUNT + 1)); fi
if $SHOW_SERVICES; then MODE_COUNT=$((MODE_COUNT + 1)); fi
if [[ -n "$DELETE_SESSION" ]]; then MODE_COUNT=$((MODE_COUNT + 1)); fi

if [[ "$MODE_COUNT" -gt 1 ]]; then
  echo "ERROR: Cannot combine --history, --health, --services, and --delete — pick one." >&2
  exit 1
fi

if $SHOW_HEALTH; then
  HEALTH_URL="${LTTM_STREAM_API_URL%/ask}/health"
  RESPONSE=$(curl -s -X GET "$HEALTH_URL" -H @"$AUTH_HEADER_FILE")
  STATUS=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status", "unknown"))' 2>/dev/null || echo "unknown")
  RUNTIME=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("runtime", "unknown"))' 2>/dev/null || echo "unknown")

  if [[ "$STATUS" == "healthy" ]]; then
    echo "✅ Agent runtime is healthy ($RUNTIME)"
  else
    echo "❌ Agent runtime is unhealthy ($RUNTIME)"
  fi
  exit 0
fi

if $SHOW_SERVICES; then
  SERVICES_URL="${LTTM_STREAM_API_URL%/ask}/services"
  RESPONSE=$(curl -s -X GET "$SERVICES_URL" -H @"$AUTH_HEADER_FILE")
  echo "📋 Available services:"
  echo "$RESPONSE" | python3 -c '
import json, sys
try:
    services = json.load(sys.stdin)
    for i, svc in enumerate(services, 1):
        name = svc.get("name", "?")
        desc = svc.get("description", "")
        print(f"  {i}. {name} — {desc}")
except Exception as e:
    print(f"  Error: {e}", file=sys.stderr)
'
  exit 0
fi

if $SHOW_HISTORY; then
  echo "📋 Past conversations:"
  RESPONSE=$(curl -s -X GET "$CONVERSATIONS_API_URL" \
    -H @"$AUTH_HEADER_FILE" \
    -H "Content-Type: application/json")

  echo "$RESPONSE" | python3 -c '
import json, sys
from datetime import datetime, timezone
try:
    items = json.load(sys.stdin)
    if not items:
        print("  No conversations found.")
    else:
        for i, item in enumerate(items, 1):
            sid = item.get("session_id", "?")   # the full ID: --session and --delete need all of it
            title = item.get("title", "Untitled")
            count = item.get("question_count", 0)
            last = item.get("last_active", "")
            if last:
                try:
                    dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    delta = datetime.now(timezone.utc) - dt
                    if delta.days > 0:
                        ago = f"{delta.days}d ago"
                    elif delta.seconds >= 3600:
                        ago = f"{delta.seconds // 3600}h ago"
                    else:
                        ago = f"{delta.seconds // 60}m ago"
                except Exception:
                    ago = last
            else:
                ago = "unknown"
            print(f"  {i}. [{sid}] \"{title}\" — {count} questions, last active {ago}")
except Exception as e:
    print(f"  Error parsing response: {e}", file=sys.stderr)
'
  exit 0
fi

if [[ -n "$DELETE_SESSION" ]]; then
  # `--session X --delete` deletes X, not the current session; `--delete Y` names its own (the owner, 2026-10-09)
  if [[ "$DELETE_SESSION" == "__current__" && -n "$SESSION_ID" ]]; then
    DELETE_SESSION="$SESSION_ID"
  fi
  if [[ "$DELETE_SESSION" == "__current__" ]]; then
    if [[ -f "$SESSION_FILE" ]]; then
      # LC_ALL=C: in a UTF-8 locale, macOS tr exits on a byte that is not UTF-8, and set -e would end the script
      DELETE_SESSION=$(cat "$SESSION_FILE" 2>/dev/null | LC_ALL=C tr -d '[:space:]')
    fi
    if [[ -z "$DELETE_SESSION" || "$DELETE_SESSION" == "__current__" ]]; then
      echo "ERROR: No current session found. Use --delete <session_id> to specify." >&2
      exit 1
    fi
  fi

  echo "🗑️  Deleting session ${DELETE_SESSION:0:8}..."
  HTTP_CODE=$(curl -s -o "$DELETE_RESP" -w '%{http_code}' \
    -X DELETE "$CONVERSATIONS_API_URL/$DELETE_SESSION" \
    -H @"$AUTH_HEADER_FILE")

  if [[ "$HTTP_CODE" == "200" ]]; then
    echo "✅ Session ${DELETE_SESSION:0:8} deleted from history."
  elif [[ "$HTTP_CODE" == "404" ]]; then
    echo "⚠️  Session ${DELETE_SESSION:0:8} not found in history."
  else
    echo "❌ Delete failed (HTTP $HTTP_CODE)" >&2
    cat "$DELETE_RESP" >&2 2>/dev/null || true
  fi
  exit 0
fi

if [[ -z "$QUESTION" ]]; then
  echo "Usage: $0 [--new] [--clean] [--session <id>] [--history] [--delete [session_id]] [--health] [--services] [--notboring] \"Your question about AWS logs\"" >&2
  echo "Flags can appear in any position." >&2
  exit 1
fi

if $NEW_SESSION; then
  SESSION_ID=$(generate_uuid)
  echo "$SESSION_ID" > "$SESSION_FILE"
elif [[ -n "$SESSION_ID" ]]; then
  # Refused before it is saved, so such an ID can never become the current session
  if ! [[ $SESSION_ID =~ $SESSION_ID_PATTERN ]]; then
    # what was received, escaped, so a pasted look-alike hyphen (U+2011) or a stray carriage return shows
    RECEIVED=$(python3 -c 'import sys; s = sys.argv[1]; print(f"{len(s)} characters: {ascii(s[:80])}")' "$SESSION_ID")
    echo "ERROR: --session needs a full session ID, as --history lists it: a UUID in lower case. Received $RECEIVED" >&2
    echo "Nothing was sent, and the current session is unchanged. Copy the ID from --history, or start a new session with --new." >&2
    exit 1
  fi
  echo "$SESSION_ID" > "$SESSION_FILE"
elif [[ -f "$SESSION_FILE" ]]; then
  if SESSION_ID=$(cat "$SESSION_FILE" 2>/dev/null); then
    # LC_ALL=C: in a UTF-8 locale, macOS tr exits on a byte that is not UTF-8, so a damaged file would end every question
    # before the check below could replace it
    SESSION_ID=$(echo "$SESSION_ID" | LC_ALL=C tr -d '[:space:]')
    if ! [[ $SESSION_ID =~ $SESSION_ID_PATTERN ]]; then   # empty, or an ID an older CLI saved
      echo "WARNING: ~/.lttm_session contains invalid content (a session ID is a UUID in lower case) — generating new session." >&2
      SESSION_ID=$(generate_uuid)
      echo "$SESSION_ID" > "$SESSION_FILE"
    fi
  else
    echo "WARNING: Could not read ~/.lttm_session — generating new session." >&2
    SESSION_ID=$(generate_uuid)
    echo "$SESSION_ID" > "$SESSION_FILE"
  fi
else
  SESSION_ID=$(generate_uuid)
  echo "$SESSION_ID" > "$SESSION_FILE"
fi

# A test (automated or by hand) sets LTTM_TEST_RUN=1, and the runtime then keeps
# nothing of the question in AgentCore Memory, so tests never become "facts" a demo question retrieves
if $NO_MEMORY; then
  PAYLOAD=$(python3 -c 'import json,os,sys; print(json.dumps({"prompt": sys.argv[1], "no_memory": True, **({"test_run": True} if os.environ.get("LTTM_TEST_RUN") == "1" else {})}))' "$QUESTION")
else
  PAYLOAD=$(python3 -c 'import json,os,sys; print(json.dumps({"prompt": sys.argv[1], **({"test_run": True} if os.environ.get("LTTM_TEST_RUN") == "1" else {})}))' "$QUESTION")
fi

if $NEW_SESSION; then
  echo "🆕 New session started: ${SESSION_ID:0:8}"
fi

echo "💬 Alexandra (stream) [session: ${SESSION_ID:0:8}] asking AgentCore: $QUESTION"

CURL_EXIT=0
PARSER_EXIT=0

if $GAME_MODE; then
  if [[ ! -f "$SCRIPT_DIR/lttm_game.py" ]]; then
    echo "ERROR: --notboring requested, but lttm_game.py was not found in $SCRIPT_DIR" >&2
    exit 1
  fi

  echo "" > "$GAME_STATUS_FILE"

  set +e
  (
    curl -s -N -D "$TEMP_HEADERS" \
      -X POST "$LTTM_STREAM_API_URL" \
      -H "Accept: text/event-stream" \
      -H "Content-Type: application/json" \
      -H @"$AUTH_HEADER_FILE" \
      -H "x-amzn-bedrock-agentcore-session-id: ${SESSION_ID}" \
      -d "$PAYLOAD" 2>"$CURL_ERR_LOG" \
    | LTTM_CLIENT_MODE=game \
      LTTM_GAME_STATUS_FILE="$GAME_STATUS_FILE" \
      LTTM_GAME_ANSWER_FILE="$GAME_ANSWER_FILE" \
      PYTHONUNBUFFERED=1 \
      run_sse_parser

    PIPE=("${PIPESTATUS[@]}")
    echo "${PIPE[0]:-99} ${PIPE[1]:-99}" > "$PIPE_STATUS_FILE"

    if ! grep -q '^GAME_OVER' "$GAME_STATUS_FILE" 2>/dev/null; then
      echo "GAME_OVER" > "$GAME_STATUS_FILE"
    fi
  ) &
  SSE_BG_PID=$!

  python3 "$SCRIPT_DIR/lttm_game.py" "$GAME_STATUS_FILE" 2>/dev/null || true

  wait "$SSE_BG_PID" || true
  set -e

  if [[ -f "$PIPE_STATUS_FILE" ]]; then
    read -r CURL_EXIT PARSER_EXIT < "$PIPE_STATUS_FILE" || true
  else
    CURL_EXIT=1
    PARSER_EXIT=0
  fi

  reset 2>/dev/null || true

  if [[ -s "$GAME_ANSWER_FILE" ]]; then
    echo ""
    cat "$GAME_ANSWER_FILE"
  fi
else
  set +e
  curl -s -N -D "$TEMP_HEADERS" \
    -X POST "$LTTM_STREAM_API_URL" \
    -H "Accept: text/event-stream" \
    -H "Content-Type: application/json" \
    -H @"$AUTH_HEADER_FILE" \
    -H "x-amzn-bedrock-agentcore-session-id: ${SESSION_ID}" \
    -d "$PAYLOAD" 2>"$CURL_ERR_LOG" \
  | LTTM_CLIENT_MODE=normal \
    PYTHONUNBUFFERED=1 \
    run_sse_parser

  PIPE=("${PIPESTATUS[@]}")
  CURL_EXIT="${PIPE[0]:-99}"
  PARSER_EXIT="${PIPE[1]:-99}"
  set -e
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

HTTP_STATUS=$(awk '/^HTTP\//{code=$2} END{gsub(/\r/,"",code); print code}' "$TEMP_HEADERS" 2>/dev/null)
[[ -z "$HTTP_STATUS" ]] && HTTP_STATUS="000"

if [[ "${CURL_EXIT:-0}" -ne 0 ]]; then
  echo "ERROR: curl failed with exit code $CURL_EXIT" >&2
  cat "$CURL_ERR_LOG" >&2 2>/dev/null || true
  echo -e "\n(${ELAPSED}s)"
  exit 1
fi

if [[ "${PARSER_EXIT:-0}" -ne 0 ]]; then
  echo "ERROR: SSE parser failed with exit code $PARSER_EXIT" >&2
  echo -e "\n(${ELAPSED}s)"
  exit 1
fi

if [[ "$HTTP_STATUS" == "200" ]]; then
  echo -e "\n(${ELAPSED}s)"
  exit 0
fi

echo "ERROR: HTTP $HTTP_STATUS" >&2
echo -e "\n(${ELAPSED}s)"
exit 1