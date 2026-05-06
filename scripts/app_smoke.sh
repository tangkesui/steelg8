#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_BUNDLE=0
CHECK_FRONTMOST=1
REAL_APP_STREAM=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)
      RUN_BUNDLE=1
      shift
      ;;
    --skip-frontmost)
      CHECK_FRONTMOST=0
      shift
      ;;
    --real-app-stream)
      REAL_APP_STREAM=1
      shift
      ;;
    -h|--help)
      cat <<'HELP'
Usage: scripts/app_smoke.sh [--bundle] [--skip-frontmost] [--real-app-stream]

Checks the packaged macOS app runtime:
  - optional bundle/build/start
  - steelg8 app process
  - bundled Python kernel process
  - dynamic port listener
  - /health and protected /providers
  - auth rejects unauthenticated protected requests
  - no orphan CommandLineTools Swift compiler processes
  - app is frontmost/visible when a main window is open
  - /chat/stream via temporary mock kernel, no provider cost

--real-app-stream also calls the running app's /chat/stream and may hit a real provider.
HELP
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

step() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf 'app smoke failed: %s\n' "$1" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

json_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
path, key = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
value = data
for part in key.split("."):
    value = value[part]
print(value)
PY
}

free_port() {
  python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

need curl
need lsof
need osascript
need pgrep
need python3

if [[ "$RUN_BUNDLE" == "1" ]]; then
  step "Bundle and launch app"
  ./bundle.sh
fi

step "Check orphan Swift compiler processes"
swift_orphans="$(
  ps -o pid=,ppid=,etime=,stat=,%cpu=,command= -ax \
    | grep -E 'swift-driver|swift-frontend|swift-build' \
    | grep -v grep \
    | grep -E '/Library/Developer/CommandLineTools|/usr/bin/swift-build' || true
)"
if [[ -n "$swift_orphans" ]]; then
  printf '%s\n' "$swift_orphans" >&2
  fail "found orphan CommandLineTools Swift compiler processes"
fi

step "Locate app and kernel processes"
app_pid=""
kernel_pid=""
for _ in {1..80}; do
  app_pid="$(pgrep -f '\.build/steelg8\.app/Contents/MacOS/steelg8' | head -n1 || true)"
  kernel_pid="$(pgrep -f '\.build/steelg8\.app/Contents/Resources/Python/server.py' | head -n1 || true)"
  if [[ -n "$app_pid" && -n "$kernel_pid" ]]; then
    break
  fi
  sleep 0.25
done

[[ -n "$app_pid" ]] || fail "steelg8 app process not found; run scripts/app_smoke.sh --bundle"
[[ -n "$kernel_pid" ]] || fail "bundled Python kernel process not found after waiting"

kernel_cmd="$(ps -o command= -p "$kernel_pid")"
port="$(printf '%s\n' "$kernel_cmd" | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
[[ -n "$port" ]] || fail "could not parse kernel port from process command"

token="$(ps eww -p "$kernel_pid" | sed -n 's/.*STEELG8_AUTH_TOKEN=\([^ ]*\).*/\1/p')"
[[ -n "$token" ]] || fail "could not read launch auth token from kernel environment"

printf 'app_pid=%s kernel_pid=%s port=%s token_present=yes\n' "$app_pid" "$kernel_pid" "$port"

step "Check TCP listener"
lsof -nP -iTCP:"$port" -sTCP:LISTEN || true

step "Check health and protected API"
health_code=""
for _ in {1..40}; do
  health_code="$(
    curl -sS --max-time 2 -o /tmp/steelg8-health.json -w '%{http_code}' \
      "http://127.0.0.1:${port}/health" 2>/dev/null || true
  )"
  if [[ "$health_code" == "200" ]]; then
    break
  fi
  sleep 0.25
done
[[ "$health_code" == "200" ]] || fail "/health returned HTTP $health_code"

python3 - <<'PY' || fail "/health payload invalid"
import json
with open("/tmp/steelg8-health.json", "r", encoding="utf-8") as f:
    data = json.load(f)
assert data.get("ok") is True
assert data.get("authRequired") is True
assert data.get("authenticated") is False
PY

case "$kernel_cmd" in
  *".build/steelg8.app/Contents/Resources/Python/server.py"*) ;;
  *) fail "kernel is not running from bundled app Resources" ;;
esac

unauth_code="$(
  curl -sS --max-time 3 -o /tmp/steelg8-unauth.json -w '%{http_code}' \
    "http://127.0.0.1:${port}/providers"
)"
[[ "$unauth_code" == "401" ]] || fail "unauthenticated /providers returned HTTP $unauth_code, expected 401"

providers_code="$(
  curl -sS --max-time 3 \
    -H "Authorization: Bearer ${token}" \
    -o /tmp/steelg8-providers.json \
    -w '%{http_code}' \
    "http://127.0.0.1:${port}/providers"
)"
[[ "$providers_code" == "200" ]] || fail "authenticated /providers returned HTTP $providers_code"

printf 'health=200 providers_unauth=401 providers_auth=200 default_model=%s\n' \
  "$(json_field /tmp/steelg8-providers.json defaultModel 2>/dev/null || printf '-')"

if [[ "$CHECK_FRONTMOST" == "1" ]]; then
  step "Check frontmost app ownership"
  open /Applications/steelg8.app
  sleep 1
  frontmost="$(osascript -e 'tell application "System Events" to name of first application process whose frontmost is true' || true)"
  steel_state="$(osascript -e 'tell application "System Events" to get {frontmost, visible} of application process "steelg8"' || true)"
  printf 'frontmost=%s steelg8_state=%s\n' "$frontmost" "$steel_state"
  [[ "$frontmost" == "steelg8" ]] || fail "steelg8 is not frontmost"
  [[ "$steel_state" == "true, true" ]] || fail "steelg8 is not {frontmost=true, visible=true}"
fi

step "Check close and reopen main window"
osascript -e 'tell application "System Events" to keystroke "w" using command down' >/dev/null 2>&1 || true
sleep 1
closed_count="$(osascript -e 'tell application "System Events" to count windows of process "steelg8"' || printf 'unknown')"
open /Applications/steelg8.app
sleep 1
reopened_count="$(osascript -e 'tell application "System Events" to count windows of process "steelg8"' || printf 'unknown')"
printf 'windows_after_close=%s windows_after_reopen=%s\n' "$closed_count" "$reopened_count"
if [[ "$closed_count" == "unknown" || "$reopened_count" == "unknown" ]]; then
  printf 'warning: skip strict close/reopen window count; osascript lacks Accessibility permission in this host\n' >&2
else
  [[ "$reopened_count" =~ ^[1-9][0-9]*$ ]] || fail "main window did not reopen"
fi

if [[ "$REAL_APP_STREAM" == "1" ]]; then
  step "Check running app /chat/stream (may hit real provider)"
  curl -sS -N --max-time 20 \
    -H "Authorization: Bearer ${token}" \
    -H 'Content-Type: application/json' \
    -H 'Accept: text/event-stream' \
    -d '{"message":"ping","stream":true}' \
    "http://127.0.0.1:${port}/chat/stream" \
    > /tmp/steelg8-real-stream.sse
  grep -q '"type": "done"' /tmp/steelg8-real-stream.sse \
    || fail "running app /chat/stream did not emit done"
fi

step "Check /chat/stream with temporary mock kernel"
tmpdir="$(mktemp -d)"
temp_port="$(free_port)"
temp_token="app-smoke-$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
temp_providers="$tmpdir/providers.json"
cat > "$temp_providers" <<'JSON'
{
  "default_model": "",
  "providers": {}
}
JSON

temp_pid=""
cleanup() {
  if [[ -n "${temp_pid:-}" ]]; then
    kill "$temp_pid" >/dev/null 2>&1 || true
    wait "$temp_pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$tmpdir"
}
trap cleanup EXIT

python_bin=".venv/bin/python3"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi

STEELG8_AUTH_TOKEN="$temp_token" \
STEELG8_PORT="$temp_port" \
STEELG8_APP_ROOT="$ROOT" \
STEELG8_SOUL_PATH="${HOME}/.steelg8/soul.md" \
STEELG8_PROVIDERS_PATH="$temp_providers" \
"$python_bin" Python/server.py --port "$temp_port" \
  > "$tmpdir/kernel.log" 2>&1 &
temp_pid="$!"

for _ in {1..40}; do
  if curl -sS --max-time 1 "http://127.0.0.1:${temp_port}/health" >/tmp/steelg8-temp-health.json 2>/dev/null; then
    break
  fi
  sleep 0.25
done

curl -sS -N --max-time 15 \
  -H "Authorization: Bearer ${temp_token}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"message":"ping","stream":true}' \
  "http://127.0.0.1:${temp_port}/chat/stream" \
  > /tmp/steelg8-mock-stream.sse \
  || {
    cat "$tmpdir/kernel.log" >&2
    fail "temporary mock /chat/stream request failed"
  }

grep -q '"type": "meta"' /tmp/steelg8-mock-stream.sse \
  || fail "mock stream did not emit meta"
grep -q '"type": "done"' /tmp/steelg8-mock-stream.sse \
  || {
    cat "$tmpdir/kernel.log" >&2
    fail "mock stream did not emit done"
  }
grep -q '"source": "mock-fallback"' /tmp/steelg8-mock-stream.sse \
  || fail "mock stream did not use mock-fallback"

printf 'temp_kernel_port=%s mock_stream=ok\n' "$temp_port"

printf '\napp smoke ok\n'
