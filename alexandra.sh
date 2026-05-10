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
TEMP_HEADERS="/tmp/lttm_stream_headers.txt"
SESSION_FILE="$HOME/.lttm_session"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

TOKEN_FILE="$HOME/.lttm_token"
COGNITO_REGION="${COGNITO_REGION:-eu-central-1}"

if [[ -z "${COGNITO_USER_POOL_ID:-}" || -z "${COGNITO_CLIENT_ID:-}" ]]; then
  if [[ -d "$SCRIPT_DIR/terraform" ]]; then
    COGNITO_USER_POOL_ID=$(terraform -chdir="$SCRIPT_DIR/terraform" output -raw cognito_user_pool_id 2>/dev/null || true)
    COGNITO_CLIENT_ID=$(terraform -chdir="$SCRIPT_DIR/terraform" output -raw cognito_app_client_id 2>/dev/null || true)
  fi
  if [[ -z "${COGNITO_USER_POOL_ID:-}" || -z "${COGNITO_CLIENT_ID:-}" ]]; then
    echo "ERROR: COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID not set and could not be auto-discovered." >&2
    exit 1
  fi
fi

get_cached_token() {
  [[ -f "$TOKEN_FILE" ]] || { echo ""; return; }
  python3 -c "
import json, time, sys
try:
    d = json.load(open('$TOKEN_FILE'))
    if time.time() < d.get('expires_at', 0) - 60:
        print(d.get('id_token', ''))
    else:
        print('')
except: print('')
" 2>/dev/null
}

refresh_token() {
  [[ -f "$TOKEN_FILE" ]] || return 1
  local rt
  rt=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE')).get('refresh_token',''))" 2>/dev/null || echo "")
  [[ -n "$rt" ]] || return 1
  local res
  res=$(aws cognito-idp initiate-auth --client-id "$COGNITO_CLIENT_ID" \
    --auth-flow REFRESH_TOKEN_AUTH --auth-parameters "REFRESH_TOKEN=$rt" \
    --region "$COGNITO_REGION" 2>/dev/null) || return 1
  python3 -c "
import json, time, sys
res = json.loads('''$res''')
ar = res.get('AuthenticationResult', {})
tok = ar.get('IdToken', '')
if not tok: sys.exit(1)
with open('$TOKEN_FILE') as f: d = json.load(f)
d['id_token'] = tok
d['expires_at'] = int(time.time()) + ar.get('ExpiresIn', 3600)
with open('$TOKEN_FILE', 'w') as f: json.dump(d, f)
print(tok)
" 2>/dev/null || return 1
}

login_with_credentials() {
  echo "🔐 LTTM authentication required" >&2
  read -rp "Username: " LTTM_USER
  read -rsp "Password: " LTTM_PASS
  echo "" >&2
  local res
  res=$(aws cognito-idp initiate-auth --client-id "$COGNITO_CLIENT_ID" \
    --auth-flow USER_PASSWORD_AUTH \
    --auth-parameters "USERNAME=$LTTM_USER,PASSWORD=$LTTM_PASS" \
    --region "$COGNITO_REGION" 2>&1)
  if [[ $? -ne 0 ]]; then echo "ERROR: Auth failed — $res" >&2; exit 1; fi
  local challenge
  challenge=$(echo "$res" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ChallengeName',''))" 2>/dev/null || echo "")
  if [[ "$challenge" == "NEW_PASSWORD_REQUIRED" ]]; then
    echo "⚠️  Password change required (first login)." >&2
    read -rsp "New password: " NEW_PASS; echo ""
    local sess
    sess=$(echo "$res" | python3 -c "import json,sys; print(json.load(sys.stdin).get('Session',''))" 2>/dev/null)
    res=$(aws cognito-idp admin-respond-to-auth-challenge \
      --user-pool-id "$COGNITO_USER_POOL_ID" --client-id "$COGNITO_CLIENT_ID" \
      --challenge-name NEW_PASSWORD_REQUIRED \
      --challenge-responses "USERNAME=$LTTM_USER,NEW_PASSWORD=$NEW_PASS" \
      --session "$sess" --region "$COGNITO_REGION" 2>&1)
    if [[ $? -ne 0 ]]; then echo "ERROR: Password change failed — $res" >&2; exit 1; fi
  fi
  python3 -c "
import json, time, sys
res = json.loads(sys.stdin.read())
ar = res.get('AuthenticationResult', {})
tok = ar.get('IdToken', '')
if not tok: print('ERROR: No ID token in response', file=sys.stderr); sys.exit(1)
data = {'id_token': tok, 'refresh_token': ar.get('RefreshToken',''), 'expires_at': int(time.time()) + ar.get('ExpiresIn', 3600)}
with open('$TOKEN_FILE', 'w') as f: json.dump(data, f)
print(tok)
" <<< "$res" 2>/dev/null
  if [[ $? -ne 0 ]]; then echo "ERROR: Could not extract token." >&2; exit 1; fi
  echo "✅ Authenticated as $LTTM_USER" >&2
}

JWT_TOKEN=$(get_cached_token)
if [[ -z "$JWT_TOKEN" ]]; then JWT_TOKEN=$(refresh_token 2>/dev/null || echo ""); fi
if [[ -z "$JWT_TOKEN" ]]; then JWT_TOKEN=$(login_with_credentials); fi

generate_uuid() {
  python3 -c "import uuid; print(str(uuid.uuid4()))"
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
      NO_MEMORY=true ### If user calls --clean, no memory is used
      shift
      ;;
    --notboring)
      GAME_MODE=true ### If user calls --notboring, arcade game runs while waiting
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
      # Non-flag argument = the question
      if [[ -n "$QUESTION" ]]; then
        echo "ERROR: Multiple question arguments provided. Wrap your question in quotes." >&2
        exit 1
      fi
      QUESTION="$1"
      shift
      ;;
  esac
done

CONVERSATIONS_API_URL="${LTTM_STREAM_API_URL%/ask}/conversations"
MODE_COUNT=0
$SHOW_HISTORY && ((MODE_COUNT++))
$SHOW_HEALTH && ((MODE_COUNT++))
$SHOW_SERVICES && ((MODE_COUNT++))
[[ -n "$DELETE_SESSION" ]] && ((MODE_COUNT++))
if [[ $MODE_COUNT -gt 1 ]]; then
  echo "ERROR: Cannot combine --history, --health, --services, and --delete — pick one" >&2
  exit 1
fi

if $SHOW_HEALTH; then
  HEALTH_URL="${LTTM_STREAM_API_URL%/ask}/health"
  RESPONSE=$(curl -s -X GET "$HEALTH_URL" -H "Authorization: $JWT_TOKEN")
  STATUS=$(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")
  RUNTIME=$(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('runtime','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$STATUS" == "healthy" ]]; then
    echo "✅ Agent runtime is healthy ($RUNTIME)"
  else
    echo "❌ Agent runtime is unhealthy ($RUNTIME)"
  fi
  exit 0
fi

if $SHOW_SERVICES; then
  SERVICES_URL="${LTTM_STREAM_API_URL%/ask}/services"
  RESPONSE=$(curl -s -X GET "$SERVICES_URL" -H "Authorization: $JWT_TOKEN")
  echo "📋 Available services:"
  echo "$RESPONSE" | python3 -c "
import json, sys
try:
    services = json.load(sys.stdin)
    for i, svc in enumerate(services, 1):
        name = svc.get('name', '?')
        desc = svc.get('description', '')
        print(f'  {i}. {name} — {desc}')
except Exception as e:
    print(f'  Error: {e}', file=sys.stderr)
"
  exit 0
fi

if $SHOW_HISTORY; then
  echo "📋 Past conversations:"
  RESPONSE=$(curl -s -X GET "$CONVERSATIONS_API_URL" \
    -H "Authorization: $JWT_TOKEN" \
    -H "Content-Type: application/json")
  echo "$RESPONSE" | python3 -c "
import json, sys
from datetime import datetime, timezone
try:
    items = json.load(sys.stdin)
    if not items:
        print('  No conversations found.')
    else:
        for i, item in enumerate(items, 1):
            sid = item.get('session_id', '?')[:8]
            title = item.get('title', 'Untitled')
            count = item.get('question_count', 0)
            last = item.get('last_active', '')
            # Format relative time
            if last:
                try:
                    dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
                    delta = datetime.now(timezone.utc) - dt
                    if delta.days > 0:
                        ago = f'{delta.days}d ago'
                    elif delta.seconds >= 3600:
                        ago = f'{delta.seconds // 3600}h ago'
                    else:
                        ago = f'{delta.seconds // 60}m ago'
                except:
                    ago = last
            else:
                ago = 'unknown'
            print(f'  {i}. [{sid}] \"{title}\" — {count} questions, last active {ago}')
except Exception as e:
    print(f'  Error parsing response: {e}', file=sys.stderr)
"
  exit 0
fi

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
  HTTP_CODE=$(curl -s -o /tmp/lttm_delete_resp.json -w '%{http_code}' \
    -X DELETE "$CONVERSATIONS_API_URL/$DELETE_SESSION" \
    -H "Authorization: $JWT_TOKEN")
  if [[ "$HTTP_CODE" == "200" ]]; then
    echo "✅ Session ${DELETE_SESSION:0:8} deleted from history."
  elif [[ "$HTTP_CODE" == "404" ]]; then
    echo "⚠️  Session ${DELETE_SESSION:0:8} not found in history."
  else
    echo "❌ Delete failed (HTTP $HTTP_CODE)" >&2
    cat /tmp/lttm_delete_resp.json >&2 2>/dev/null
  fi
  rm -f /tmp/lttm_delete_resp.json
  exit 0
fi

if [[ -z "$QUESTION" ]]; then
  echo "Usage: $0 [--new] [--clean] [--session <id>] [--history] [--delete [session_id]] [--health] [--services] \"Your question about AWS logs\"" >&2
  echo "  Flags can appear in any position (before or after the question)." >&2
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
  PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'prompt': sys.argv[1], 'no_memory': True}))" "$QUESTION")
else
  PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'prompt': sys.argv[1]}))" "$QUESTION")
fi

if $NEW_SESSION; then
  echo "🆕 New session started: ${SESSION_ID:0:8}"
fi

echo "💬 Alexandra (stream) [session: ${SESSION_ID:0:8}] asking AgentCore: $QUESTION"

FIFO="/tmp/lttm_stream_fifo_$$"
TEMP_STATUS="/tmp/lttm_stream_status_$$.txt"
trap 'rm -f "$FIFO" "$TEMP_STATUS"' EXIT
mkfifo "$FIFO"

curl -s -N -D "$TEMP_HEADERS" -w '%{http_code}' -o "$FIFO" \
  -X POST "${LTTM_STREAM_API_URL%/}" \
  -H "Content-Type: application/json" \
  -H "Authorization: $JWT_TOKEN" \
  -H "x-amzn-bedrock-agentcore-session-id: ${SESSION_ID}" \
  -d "$PAYLOAD" > "$TEMP_STATUS" 2>/dev/null &
CURL_PID=$!

GAME_STATUS_FILE="/tmp/lttm_game_status_$$"
GAME_PID=""
if $GAME_MODE; then
  echo "" > "$GAME_STATUS_FILE"
  (
    GOT_RESULT_BG=false
    while IFS= read -r line; do
      if [[ "$line" == data:* ]]; then
        JSON="${line#data: }"
        TYPE=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('type',''))" 2>/dev/null || true)
        case "$TYPE" in
          status)
            MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
            echo "⏳ $MSG" > "$GAME_STATUS_FILE"
            ;;
          guard)
            MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
            echo "🛡️ $MSG" > "$GAME_STATUS_FILE"
            ;;
          tokens)
            MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
            echo "💰 $MSG" > "$GAME_STATUS_FILE"
            ;;
          result)
            ANSWER=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('result',''))" 2>/dev/null || true)
            echo "GAME_OVER" > "$GAME_STATUS_FILE"
            echo "$ANSWER" > "/tmp/lttm_game_answer_$$"
            GOT_RESULT_BG=true
            ;;
          error)
            MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
            echo "GAME_OVER" > "$GAME_STATUS_FILE"
            echo "❌ $MSG" > "/tmp/lttm_game_answer_$$"
            GOT_RESULT_BG=true
            ;;
        esac
      fi
    done < "$FIFO"
    if ! $GOT_RESULT_BG; then
      echo "GAME_OVER" > "$GAME_STATUS_FILE"
    fi
  ) &
  SSE_PARSER_PID=$!

  python3 "$SCRIPT_DIR/lttm_game.py" "$GAME_STATUS_FILE" 2>/dev/null || true

  wait "$SSE_PARSER_PID" 2>/dev/null || true
  wait "$CURL_PID" 2>/dev/null || true

  reset 2>/dev/null || true

  if [[ -f "/tmp/lttm_game_answer_$$" ]]; then
    echo ""
    cat "/tmp/lttm_game_answer_$$"
    GOT_RESULT=true
    rm -f "/tmp/lttm_game_answer_$$"
  fi
  rm -f "$GAME_STATUS_FILE"

else

GOT_RESULT=false
while IFS= read -r line; do
  if [[ "$line" == data:* ]]; then
    JSON="${line#data: }"
    TYPE=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('type',''))" 2>/dev/null || true)
    case "$TYPE" in
      status)
        MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
        echo "⏳ $MSG"
        ;;
      guard)
        MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
        echo "🛡️ $MSG"
        ;;
      tokens)
        MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
        echo "💰 $MSG"
        ;;
      result)
        ANSWER=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('result',''))" 2>/dev/null || true)
        echo -e "\n$ANSWER"
        GOT_RESULT=true
        ;;
      error)
        MSG=$(echo "$JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || true)
        echo "❌ $MSG" >&2
        ;;
    esac
  fi
done < "$FIFO"

fi

wait "$CURL_PID" || true

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

HTTP_STATUS=$(cat "$TEMP_STATUS" 2>/dev/null || echo "000")

if [[ "$HTTP_STATUS" == "200" ]]; then
  echo -e "\n(${ELAPSED}s)"
  exit 0
else
  if ! $GOT_RESULT; then
    echo "ERROR: HTTP $HTTP_STATUS" >&2
  fi
  echo -e "\n(${ELAPSED}s)"
  exit 1
fi
