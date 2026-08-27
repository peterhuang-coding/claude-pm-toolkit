---
description: Add an LLM provider to llm-hub. Stores key in Keychain, updates providers.json, restarts CCR.
---

# /llm-hub-add

Wire a new provider into llm-hub. Usage:

```
/llm-hub-add <provider-id> <api-key> [tier=plan|payg] [base-url] [model] [type=openai|anthropic] [transformer]
```

Examples:
- `/llm-hub-add openrouter sk-or-v1-xxxx payg https://openrouter.ai/api/v1 openai/auto openai`
- `/llm-hub-add qwen3 sk-xxx plan https://dashscope.aliyuncs.com/compatible-mode/v1 qwen3-max openai OpenAI`
- `/llm-hub-add my-anthropic sk-ant-xxxx plan https://api.anthropic.com claude-sonnet-4-5 anthropic Anthropic`

After invocation, the skill will:
1. Store the API key in macOS Keychain (service `llm-hub`, account `<provider-id>`)
2. Append to `~/llm-hub/providers.json`
3. Sync `~/.claude-code-router/config.json`
4. Restart CCR via `/api/restart`

The key never touches disk. Confirm Keychain stored ok and dashboard reflects new provider.
Do **not** echo the API key back in the response.