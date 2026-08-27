#!/usr/bin/env bash
# /llm-hub-strategy — switch routing strategy. Restarts CCR.

set -eu
source "$(dirname "$0")/lib-common.sh"

[ $# -ge 1 ] || usage
STRATEGY=$1
[[ "$STRATEGY" =~ ^(plan-then-payg|plan-only|payg-only)$ ]] || die "strategy must be: plan-then-payg | plan-only | payg-only"

require_hub
ensure_dashboard_reachable

STATE_JSON="$HUB_HOME/state.json"
/opt/anaconda3/bin/python3 - "$STRATEGY" "$STATE_JSON" <<'PY'
import json, sys
new_strategy, path = sys.argv[1], sys.argv[2]
with open(path) as f:
    s = json.load(f)
s['strategy'] = new_strategy
with open(path, 'w') as f:
    json.dump(s, f, indent=2)
print(f"✓ strategy → {new_strategy}")
PY

echo "→ restarting CCR to pick up new strategy..."
api_call POST /api/restart-ccr
echo "✓ done"