#!/usr/bin/env bash
# /llm-hub-add — wire a new provider into the hub.
#
# Usage:
#   add.sh <provider-id> <api-key> [tier] [base-url] [model] [type] [transformer]
#
# Defaults:
#   tier=payg   base-url=  model=default   type=openai   transformer=(auto)

set -eu
source "$(dirname "$0")/lib-common.sh"

[ $# -ge 2 ] || usage

PID=$1; shift
API_KEY=$1; shift
TIER=${1:-payg}; [ $# -gt 0 ] && shift
BASE_URL=${1:-}; [ $# -gt 0 ] && shift
MODEL=${1:-default}; [ $# -gt 0 ] && shift
PTYPE=${1:-openai}; [ $# -gt 0 ] && shift
TRANSFORMER=${1:-}; [ $# -gt 0 ] && shift

# Validate
[[ "$TIER" =~ ^(plan|payg)$ ]] || die "tier must be 'plan' or 'payg'"
[ -n "$API_KEY" ] || die "api key cannot be empty"
[ -n "$BASE_URL" ] || die "base-url required"

# Reject any obvious secrets in the wrong place
secret_scan "$PID"     && die "provider-id looks like a secret — abort"
secret_scan "$BASE_URL" && die "base-url contains a secret-looking pattern — abort"

require_hub
ensure_dashboard_reachable

# Build JSON payload
PAYLOAD=$(cat <<EOF
{
  "provider_id": "$PID",
  "api_key": "$API_KEY",
  "tier": "$TIER",
  "api_base_url": "$BASE_URL",
  "model": "$MODEL",
  "type": "$PTYPE",
  "transformer": "$TRANSFORMER"
}
EOF
)

# POST to hub. The hub stores the key in Keychain + appends to providers.json + restarts CCR.
RESP=$(api_call POST /api/add-provider -H 'Content-Type: application/json' -d "$PAYLOAD")

# Wipe API key from local shell history + memory ASAP
unset API_KEY

echo "$RESP"
echo
echo "✓ $PID added ($TIER tier, $PTYPE)"
echo "  regenerating env.sh + restarting CCR with new key..."
if "$HUB_HOME/sync-env.sh" --restart 2>&1 | tail -3; then
    echo "✓ CCR restarted, env var in place"
else
    echo "✗ sync/restart failed — check ~/llm-hub/logs/" >&2
fi
echo
echo "  dashboard: $HUB_API/dashboard"

# Secret guard: refuse to proceed if any string in our recent output matches a key pattern
if echo "$RESP" | grep -qE 'sk-[A-Za-z0-9_-]{16,}'; then
  echo "WARNING: response contained a key-shaped string; review manually" >&2
fi