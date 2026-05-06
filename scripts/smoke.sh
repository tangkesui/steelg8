#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_SWIFT=0
RUN_APP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --swift)
      RUN_SWIFT=1
      shift
      ;;
    --app)
      RUN_APP=1
      RUN_SWIFT=1
      shift
      ;;
    -h|--help)
      cat <<'HELP'
Usage: scripts/smoke.sh [--swift] [--app]

Default:
  - Python compileall
  - Python unit tests
  - Web JavaScript syntax

--swift:
  Also run Swift build with the Xcode.app toolchain when available.

--app:
  Also run scripts/app_smoke.sh --bundle. This starts the packaged app
  and checks runtime process/window/kernel behavior.
HELP
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

step() {
  printf '\n==> %s\n' "$1"
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'missing required command: %s\n' "$1" >&2
    exit 127
  fi
}

need python3

step "Python compileall"
python3 -m compileall -q Python

step "Python unit tests"
PYTHONPATH=Python python3 -m unittest discover -s Python/tests -v

if command -v node >/dev/null 2>&1; then
  step "Web JavaScript syntax"
  while IFS= read -r js_file; do
    node --check "$js_file"
  done < <(find Web/chat -maxdepth 1 -type f -name '*.js' | sort)
else
  printf 'skip Web JavaScript syntax: node not found\n'
fi

if [[ "$RUN_SWIFT" == "1" ]]; then
  step "Swift build"
  XCODE_SWIFT="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swift"
  if [[ -n "${STEELG8_SWIFT:-}" ]]; then
    SWIFT_BIN="$STEELG8_SWIFT"
  elif [[ -x "$XCODE_SWIFT" ]]; then
    export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
    SWIFT_BIN="$XCODE_SWIFT"
  else
    need swift
    SWIFT_BIN="$(command -v swift)"
  fi
  "$SWIFT_BIN" build
else
  printf '\nskip Swift build: pass --swift to enable it\n'
fi

if [[ "$RUN_APP" == "1" ]]; then
  step "Packaged app runtime smoke"
  scripts/app_smoke.sh --bundle
else
  printf '\nskip app runtime smoke: pass --app to enable it\n'
fi

printf '\nsmoke ok\n'
