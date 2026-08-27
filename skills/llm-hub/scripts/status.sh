#!/usr/bin/env bash
# /llm-hub-status — pretty-print all provider states.

set -eu
source "$(dirname "$0")/lib-common.sh"

require_hub
ensure_dashboard_reachable

curl -sS "$HUB_API/api/health" | /opt/anaconda3/bin/python3 -m json.tool