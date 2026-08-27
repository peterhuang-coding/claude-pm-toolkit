---
name: llm-hub
description: >
  Use when the user wants to add, remove, inspect, reconfigure, or check budget for an
  LLM API provider in llm-hub (Claude Code's local provider-routing hub on :3456 + :3457).
  Triggers: "add a new AI key", "configure provider", "check which API is working",
  "see today's spend", "switch routing strategy", "the API is broken, switch to another".
  Do NOT use for general Claude Code questions, project work, or non-provider tasks.
---

# llm-hub Skill

Local FastAPI hub that manages multiple LLM API providers behind a single Claude Code
installation. Routes requests through `claude-code-router` (CCR) on :3456 with a custom
JS router that prefers Plan providers and falls back to PayG when quota is exhausted.

**Endpoints**
- Dashboard:  `http://localhost:3457/dashboard`
- CCR router: `http://localhost:3456/v1/messages`
- Hub API:    `http://localhost:3457/api/{health,state,logs,budget}`

**Commands** (all available as `/llm-hub-<cmd>`):
- `/llm-hub-add <provider-id> <api-key> [tier=plan|payg] [base-url] [model]`
- `/llm-hub-remove <provider-id>`
- `/llm-hub-status`
- `/llm-hub-strategy [plan-then-payg|plan-only|payg-only]`
- `/llm-hub-budget`

**Tier semantics**
- **plan**: subscription-style; default preferred. Quota-exhausted → fall to PayG.
- **payg**: pay-as-you-go; cost-first within tier. Used when Plan is unavailable or
  user explicitly requests.

**Routing reset**: every hour boundary, all quota-exhaustion flags and PayG daily spend
are cleared automatically. Providers are re-probed.

**Key storage**: API keys live in macOS Keychain under service `llm-hub`, account
`<provider-id>`. They never touch disk.

**Dashboard auto-refreshes every 5 seconds.**

## When invoked

Resolve the hub tools first:
```bash
HUB_HOME=${LLM_HUB_HOME:-"$HOME/llm-hub"}
ADD=$HUB_HOME/scripts/add.sh
[ -x "$HOME/.claude/skills/llm-hub/scripts/add.sh" ] && ADD="$HOME/.claude/skills/llm-hub/scripts/add.sh"
```

Then dispatch on the verb:
- `add` / `register` / `configure` → invoke `$ADD "$ARGUMENTS"`
- `remove` / `delete` → invoke `remove.sh "$ARGUMENTS"`
- `status` / `check` / `health` → invoke `status.sh`
- `strategy` → invoke `strategy.sh "$ARGUMENTS"`
- `budget` / `spend` → invoke `budget.sh`

Never echo the API key back to the user in the conversation. Confirm it was stored
in Keychain (returns ok/error from `security add-generic-password`) and confirm
the dashboard reflects the new provider.

If a tool call fails, surface the stderr verbatim — do not retry silently.