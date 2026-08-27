---
description: Show current state of all providers — health, p50/p95, error rate, last check.
---

# /llm-hub-status

Prints the JSON output of `GET /api/health` for all configured providers. Each provider shows:
- name, tier (plan|payg), type (openai|anthropic)
- enabled flag + key presence
- recent p50 / p95 latency (5-min window)
- 5min error rate
- last check timestamp + last error
- quota_exhausted_until / circuit_open_until flags
- daily spend (PayG only)

Alternative: open the dashboard at `http://localhost:3457/dashboard`.