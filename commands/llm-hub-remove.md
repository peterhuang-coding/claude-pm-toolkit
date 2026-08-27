---
description: Soft-delete a provider from llm-hub. Removes Keychain key, marks disabled, restarts CCR.
---

# /llm-hub-remove

Usage: `/llm-hub-remove <provider-id>`

The skill will:
1. Delete the API key from macOS Keychain
2. Mark the provider disabled in `~/llm-hub/providers.json`
3. Restart CCR

Metrics history in SQLite is preserved for 7 days (soft delete). To fully purge:
```
rm ~/llm-hub/metrics.db
```