#!/usr/bin/env bash
# /llm-hub-remove — soft-delete a provider. Key is removed from Keychain.

set -eu
source "$(dirname "$0")/lib-common.sh"

[ $# -ge 1 ] || usage
PID=$1

require_hub
ensure_dashboard_reachable

# 1. Delete key from Keychain
if security delete-generic-password -a "$PID" -s "$KEYCHAIN_SERVICE" 2>/dev/null; then
  echo "✓ Keychain entry for $PID removed"
else
  echo "(no Keychain entry for $PID — skipping)"
fi

# 2. Mark as disabled in providers.json (preserve metrics history for 7 days)
PROVIDERS_JSON="$HUB_HOME/providers.json"
if [ -f "$PROVIDERS_JSON" ]; then
  /opt/anaconda3/bin/python3 - "$PID" "$PROVIDERS_JSON" <<'PY'
import json, sys
pid, path = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
if pid in cfg.get('providers', {}):
    cfg['providers'][pid]['enabled'] = False
    cfg['providers'][pid]['notes'] = (cfg['providers'][pid].get('notes', '') + ' | removed ' + __import__('datetime').date.today().isoformat()).strip(' |')
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)
    print(f"✓ {pid} marked disabled in providers.json")
else:
    print(f"(no providers.json entry for {pid})")
PY
fi

# 3. Restart CCR so config reloads
echo "→ restarting CCR..."
api_call POST /api/restart-ccr

echo
echo "✓ $PID removed (key gone from Keychain, provider disabled, CCR restarted)"
echo "  to fully purge metrics history: rm $HUB_HOME/metrics.db (irreversible)"