---
description: Switch the routing strategy. Restarts CCR.
---

# /llm-hub-strategy

Usage: `/llm-hub-strategy [plan-then-payg|plan-only|payg-only]`

Strategies:
- `plan-then-payg` (default): use Plan providers when healthy, fall back to PayG on quota exhaustion
- `plan-only`: never fall back to PayG; if all Plan providers are down, fail
- `payg-only`: skip Plan entirely; use PayG providers cost-first

The skill updates `~/llm-hub/state.json#strategy` and calls `POST /api/restart-ccr`.