---
description: Show PayG daily spend vs budget for each provider. Warns at 90%, hard-stops at 100%.
---

# /llm-hub-budget

For each PayG provider, prints:
- name
- $ spend today / $ daily budget
- progress bar
- remaining budget
- warning flag at ≥90%
- ⛔ flag at ≥100% (provider will be ejected from rotation)

Cost comes from the `budget_tracking` SQLite table; refreshed by `/api/add-provider`
call paths and any place tokens are accounted.

If you see ⛔, either raise the budget in `~/llm-hub/providers.json` (`daily_budget_usd`)
or wait until the daily reset (00:00 local).