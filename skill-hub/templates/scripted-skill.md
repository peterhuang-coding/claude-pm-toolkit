---
name: {{name}}
description: {{description}}
---

# {{name}}

{{description}}

## 脚本

本技能带 `scripts/` 目录。脚本必须幂等、可重复执行，失败时输出可读错误。

```bash
sh "$HOME/.claude/skills/{{name}}/scripts/example.sh"
```

## 规则

- 脚本是唯一事实源，SKILL.md 只写契约。
- 密钥不进脚本，只从环境变量或 Keychain 读。
