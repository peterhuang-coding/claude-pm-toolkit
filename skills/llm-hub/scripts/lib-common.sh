#!/usr/bin/env bash
# Shared functions for llm-hub skill scripts.
# Source this from each script:  source "$(dirname "$0")/lib-common.sh"

set -eu

HUB_HOME=${LLM_HUB_HOME:-"$HOME/llm-hub"}
HUB_API=${HUB_HUB_API:-"http://localhost:3457"}
KEYCHAIN_SERVICE="llm-hub"

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  cat >&2 <<EOF
usage: ${0##*/} <command> [args]

Common commands:
  add <provider-id> <api-key> [tier] [base-url] [model] [type] [transformer]
  remove <provider-id>
  status
  strategy [plan-then-payg|plan-only|payg-only]
  budget
EOF
  exit 1
}

# Scrub a string for any known API key pattern. Echo non-zero if a hit.
secret_scan() {
  local s=$1
  if echo "$s" | grep -qE '(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]+|AKIA[A-Z0-9]{16})'; then
    return 0  # found
  fi
  return 1
}

api_call() {
  local method=$1; shift
  local path=$1; shift
  curl -sS -X "$method" "$HUB_API$path" "$@"
}

now_human() { date '+%Y-%m-%d %H:%M:%S'; }
now_unix() { date '+%s'; }

require_hub() {
  [ -d "$HUB_HOME" ] || die "llm-hub not installed at $HUB_HOME"
}

ensure_dashboard_reachable() {
  if ! curl -sS -o /dev/null -w '%{http_code}' "$HUB_API/dashboard" 2>/dev/null | grep -q '200'; then
    die "llm-hub dashboard not reachable at $HUB_API — is the sidecar running?"
  fi
}