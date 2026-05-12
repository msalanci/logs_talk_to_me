# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

#!/usr/bin/env bash
# alexandra.sh — CLI for LTTM (Logs Talk To Me) v3 — streaming endpoint
#
# Sends a natural language question to the LTTM streaming REST API endpoint and prints the plain-English answer from the supervisor agent.
#
# Flow: alexandra.sh → REST API GW (eu-central-1) → Lambda stream shim → AgentCore lttm_supervisor_stream (us-west-2)
#
# Connects to: aws_api_gateway_stage.stream_prod (agents-stream.tf) via LTTM_STREAM_API_URL

set -euo pipefail

START_TIME=$(date +%s)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_FILE="$HOME/.lttm_session"
TOKEN_FILE="$HOME/.lttm_token"
COGNITO_REGION="${COGNITO_REGION:-eu-central-1}"

TEMP_HEADERS="/tmp/lttm_stream_headers_$$.txt"
CURL_ERR_LOG="/tmp/lttm_curl_err_$$.log"
PIPE_STATUS_FILE="/tmp/lttm_pipe_status_$$.txt"
GAME_STATUS_FILE="/tmp/lttm_game_status_$$.txt"
GAME_ANSWER_FILE="/tmp/lttm_game_answer_$$.txt"
DELETE_RESP="/tmp/lttm_delete_resp_$$.json"

cleanup() {
  rm -f \
    "$TEMP_HEADERS" \
    "$CURL_ERR_LOG" \
    "$PIPE_STATUS_FILE" \
    "$GAME_STATUS_FILE" \
    "$GAME_ANSWER_FILE" \
    "$DELETE_RESP"
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# Inline SSE parser
# -----------------------------------------------------------------------------
run_sse_parser() {
  python3 -u -c '
"""
Reads SSE from stdin and prints user-friendly output.
Avoids the old FIFO + bash while-read parser, which could lose the final SSE frame on long streams or EOF without a trailing newline
"""

import sys
import os
import json

mode = os.environ.get("LTTM_CLIENT_MODE", "normal")
game_status_file = os.environ.get("LTTM_GAME_STATUS_FILE", "")
game_answer_file = os.environ.get("LTTM_GAME_ANSWER_FILE", "")

data_lines = []
saw_terminal_event = False


def write_file(path, text):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text or "")
        if not (text or "").endswith("\n"):
            f.write("\n")


def emit_status(prefix, message):
    message = message or ""
    if mode == "game":
        write_file(game_status_file, f"{prefix} {message}")
    else:
        print(f"{prefix} {message}", flush=True)


def emit_result(text):
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
    global saw_terminal_event
    saw_terminal_event = True
    text = text or ""
    if mode == "game":
        write_file(game_answer_file, f"❌ {text}")
        write_file(game_status_file, "GAME_OVER")
    else:
        print(f"❌ {text}", file=sys.stderr, flush=True)


def handle_event(payload):
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
        # Keep the CLI forward-compatible with future event types.
        return


def flush_event():
    global data_lines
    if not data_lines:
        return
    payload = "\n".join(data_lines).strip()
    data_lines = []
    handle_event(payload)


for raw_line in sys.stdin:
    line = raw_line.rstrip("\r\n")

    # Empty line terminates an SSE frame.
    if line == "":
        flush_event()
        continue

    # Ignore comments and other SSE fields for now.
    if line.startswith(":"):
        continue

    if line.startswith("data:"):
        data_lines.append(line[5:].lstrip(" "))

# Handles final frame if stream ends without blank line/newline.
flush_event()

if mode == "game" and not saw_terminal_event:
    write_file(game_status_file, "GAME_OVER")

# Preserve old behavior: SSE error events print visibly but do not necessarily
# make the shell command fail if HTTP status was 200.
sys.exit(0)
'
}

# -----------------------------------------------------------------------------
# API URL discovery
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Cognito discovery
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# JWT token management
# -----------------------------------------------------------------------------
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
  if ! res=$(aws cognito-idp initiate-auth \
    --client-id "$COGNITO_CLIENT_ID" \
    --auth-flow REFRESH_TOKEN_AUTH \
    --auth-parameters "REFRESH_TOKEN=$rt" \
    --region "$COGNITO_REGION" 2>/dev/null); then
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

login_with_credentials() {
  echo "🔐 LTTM authentication required" >&2
  read -rp "Username: " LTTM_USER
  read -rsp "Password: " LTTM_PASS
  echo "" >&2

  local res
  if ! res=$(aws cognito-idp initiate-auth \
    --client-id "$COGNITO_CLIENT_ID" \
    --auth-flow USER_PASSWORD_AUTH \
    --auth-parameters "USERNAME=$LTTM_USER,PASSWORD=$LTTM_PASS" \
    --region "$COGNITO_REGION" 2>&1); then
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

    if ! res=$(aws cognito-idp admin-respond-to-auth-challenge \
      --user-pool-id "$COGNITO_USER_POOL_ID" \
      --client-id "$COGNITO_CLIENT_ID" \
      --challenge-name NEW_PASSWORD_REQUIRED \
      --challenge-responses "USERNAME=$LTTM_USER,NEW_PASSWORD=$NEW_PASS" \
      --session "$sess" \
      --region "$COGNITO_REGION" 2>&1); then
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

# -----------------------------------------------------------------------------
# Parse flags
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Mode: health
# -----------------------------------------------------------------------------
if $SHOW_HEALTH; then
  HEALTH_URL="${LTTM_STREAM_API_URL%/ask}/health"
  RESPONSE=$(curl -s -X GET "$HEALTH_URL" -H "Authorization: $JWT_TOKEN")
  STATUS=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status", "unknown"))' 2>/dev/null || echo "unknown")
  RUNTIME=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("runtime", "unknown"))' 2>/dev/null || echo "unknown")

  if [[ "$STATUS" == "healthy" ]]; then
    echo "✅ Agent runtime is healthy ($RUNTIME)"
  else
    echo "❌ Agent runtime is unhealthy ($RUNTIME)"
  fi
  exit 0
fi

# -----------------------------------------------------------------------------
# Mode: services
# -----------------------------------------------------------------------------
if $SHOW_SERVICES; then
  SERVICES_URL="${LTTM_STREAM_API_URL%/ask}/services"
  RESPONSE=$(curl -s -X GET "$SERVICES_URL" -H "Authorization: $JWT_TOKEN")
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

# -----------------------------------------------------------------------------
# Mode: history
# -----------------------------------------------------------------------------
if $SHOW_HISTORY; then
  echo "📋 Past conversations:"
  RESPONSE=$(curl -s -X GET "$CONVERSATIONS_API_URL" \
    -H "Authorization: $JWT_TOKEN" \
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
            sid = item.get("session_id", "?")[:8]
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

# -----------------------------------------------------------------------------
# Mode: delete
# -----------------------------------------------------------------------------
if [[ -n "$DELETE_SESSION" ]]; then
  if [[ "$DELETE_SESSION" == "__current__" ]]; then
    if [[ -f "$SESSION_FILE" ]]; then
      DELETE_SESSION=$(cat "$SESSION_FILE" 2>/dev/null | tr -d '[:space:]')
    fi
    if [[ -z "$DELETE_SESSION" || "$DELETE_SESSION" == "__current__" ]]; then
      echo "ERROR: No current session found. Use --delete <session_id> to specify." >&2
      exit 1
    fi
  fi

  echo "🗑️  Deleting session ${DELETE_SESSION:0:8}..."
  HTTP_CODE=$(curl -s -o "$DELETE_RESP" -w '%{http_code}' \
    -X DELETE "$CONVERSATIONS_API_URL/$DELETE_SESSION" \
    -H "Authorization: $JWT_TOKEN")

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

# -----------------------------------------------------------------------------
# Normal question mode
# -----------------------------------------------------------------------------
if [[ -z "$QUESTION" ]]; then
  echo "Usage: $0 [--new] [--clean] [--session <id>] [--history] [--delete [session_id]] [--health] [--services] [--notboring] \"Your question about AWS logs\"" >&2
  echo "Flags can appear in any position." >&2
  exit 1
fi

if $NEW_SESSION; then
  SESSION_ID=$(generate_uuid)
  echo "$SESSION_ID" > "$SESSION_FILE"
elif [[ -n "$SESSION_ID" ]]; then
  echo "$SESSION_ID" > "$SESSION_FILE"
elif [[ -f "$SESSION_FILE" ]]; then
  if SESSION_ID=$(cat "$SESSION_FILE" 2>/dev/null); then
    SESSION_ID=$(echo "$SESSION_ID" | tr -d '[:space:]')
    if [[ -z "$SESSION_ID" ]]; then
      echo "WARNING: ~/.lttm_session contains invalid content — generating new session." >&2
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

if $NO_MEMORY; then
  PAYLOAD=$(python3 -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1], "no_memory": True}))' "$QUESTION")
else
  PAYLOAD=$(python3 -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1]}))' "$QUESTION")
fi

if $NEW_SESSION; then
  echo "🆕 New session started: ${SESSION_ID:0:8}"
fi

echo "💬 Alexandra (stream) [session: ${SESSION_ID:0:8}] asking AgentCore: $QUESTION"

CURL_EXIT=0
PARSER_EXIT=0

# -----------------------------------------------------------------------------
# Streaming request
# -----------------------------------------------------------------------------
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
      -H "Authorization: $JWT_TOKEN" \
      -H "x-amzn-bedrock-agentcore-session-id: ${SESSION_ID}" \
      -d "$PAYLOAD" 2>"$CURL_ERR_LOG" \
    | LTTM_CLIENT_MODE=game \
      LTTM_GAME_STATUS_FILE="$GAME_STATUS_FILE" \
      LTTM_GAME_ANSWER_FILE="$GAME_ANSWER_FILE" \
      PYTHONUNBUFFERED=1 \
      run_sse_parser

    PIPE=("${PIPESTATUS[@]}")
    # echo "${PIPE[0]:-1} ${PIPE[1]:-1}" > "$PIPE_STATUS_FILE"
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
    -H "Authorization: $JWT_TOKEN" \
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