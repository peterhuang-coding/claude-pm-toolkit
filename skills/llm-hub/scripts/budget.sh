#!/usr/bin/env bash
# /llm-hub-budget — show PayG daily spend + remaining budget.

set -eu
source "$(dirname "$0")/lib-common.sh"

require_hub
ensure_dashboard_reachable

curl -sS "$HUB_API/api/budget" | /opt/anaconda3/bin/python3 -c '
import json, sys
data = json.load(sys.stdin)
if not data:
    print("no PayG providers configured")
    sys.exit(0)
for pid, b in data.items():
    name = b["name"]
    spend = b["spend_usd"]
    budget = b["budget_usd"]
    remaining = b["remaining_usd"]
    pct = (spend / budget * 100) if budget else 0
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    flag = " ⚠️  near limit" if pct >= 90 else (" ⛔  over budget" if pct >= 100 else "")
    print(f"{name}")
    print(f"  ${spend:.4f} / ${budget:.2f}  ({pct:.1f}%){flag}")
    print(f"  [{bar}]  remaining: ${remaining:.4f}" if remaining is not None else f"  [{bar}]")
    print()
'