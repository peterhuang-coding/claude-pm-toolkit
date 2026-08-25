# claude-pm-toolkit

个人 Claude Code 研发总控的配套工具集（2026-08 建立）。包含四个组件：

| 组件 | 作用 | 一句话 |
|---|---|---|
| **skill-hub** | 本地网页（:3458）管理全部 skill | 浏览 / 搜索 / 新建 / 编辑 / 分类 / 挂载 / 归档 + 体检 |
| **autopilot** | 白班决策 + 夜班执行 | 每天 1 小时只做关键决策，其余时间代理围绕「主线」自主推进 |
| **vision-publish** | 视觉 Computer Use 发布 | AI 看页面、点页面，不用 Playwright 脚本化 |
| **pm-guard** | PreToolUse 防护 hook | 循环/重试风暴检测 + 夜班危险命令物理拦截 |

## 目录结构（1:1 对应安装位置）

```
skill-hub/                       → ~/skill-hub/（服务端）
  server.py dashboard.html serve.sh categories.json
  com.petermini.skillhub.plist   → ~/Library/LaunchAgents/
skills/skill-hub/SKILL.md        → ~/.claude/skills/skill-hub/SKILL.md
skills/autopilot/SKILL.md        → ~/.claude/skills/autopilot/SKILL.md
skills/autopilot/scripts/        → ~/.claude/skills/autopilot/scripts/
skills/vision-publish/SKILL.md   → ~/.claude/skills/vision-publish/SKILL.md
commands/autopilot.md            → ~/.claude/commands/autopilot.md
commands/publish.md              → ~/.claude/commands/publish.md
pm-guard/pm-guard.py             → ~/.claude/skills/pm-orchestrator/scripts/pm-guard.py
```

## 安装

```bash
# skill-hub 服务端
cp -R skill-hub ~/skill-hub
chmod +x ~/skill-hub/serve.sh

# skills 与 commands
cp -R skills/skill-hub ~/.claude/skills/
cp -R skills/autopilot ~/.claude/skills/
cp -R skills/vision-publish ~/.claude/skills/
cp commands/autopilot.md commands/publish.md ~/.claude/commands/

# 常驻服务（可选）
cp skill-hub/com.petermini.skillhub.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.petermini.skillhub.plist
```

## 核心概念

### skill-hub（http://127.0.0.1:3458）

本地 FastAPI 服务，统一盘点五个位置的技能：`~/.claude/skills/`（本目录 + 链接）、`~/.agents/skills/`（休眠）、插件缓存、`~/.claude/commands/`、`~/.claude/agents/`。

- 双维度分类：功能分类（8 类）+ 技能性质（纯提示词 / 本地工具 / 外部接口 / 混合）
- 体检：断链、重名、描述逐字重复、.bak、缺 SKILL.md、Agent 缺工具
- 沉淀：上游技能一键「复制为自己的」；归档不硬删

### autopilot（白班 / 夜班）

铁律：**聊天不是状态，主线文件才是**。每项目 `.claude/autopilot/mainline.md`（git 跟踪）：
目标+验收标准 / 已决策日志 / 唯一当前任务 / 已试路径 / 待决策队列 / 禁区 / 进度。

- 白班四段式：简报（5 分钟）→ 拍板 → 派活 → 交接，`/autopilot` 命令
- 夜班循环：`autopilot.sh`，每轮新会话，只做当前任务；git 无变化 = 停滞轮，3 轮熔断自停
- 硬护栏：轮次/截止时间/单轮超时（1800s）+ 注入标记扫描 + 夜班晨报飞书推送
- 决策队列：待决策项带选项/推荐/风险等级；低风险项可 auto-default

### vision-publish

AI 视觉决策 + CDP 执行（chrome-devtools MCP + `claude --chrome`）。五步：打开 → 截图理解 → 操作 → 校验 → 记录。支付 / 删除 / 正式对外发布必须人在场。像素级看图走 `/imageinput`（OpenRouter 视觉模型，注意区域可用性：本环境 qwen3-vl 可用，gemini/haiku 403）。

### pm-guard

PreToolUse hook，独立于权限模式（对 bypassPermissions 同样生效）：

- 常开：同一 (tool, input) 300 秒内第 6 次 → 拦截（重试风暴 / 循环）
- 夜班：检测到 `.claude/autopilot/.loop.lock` 时拦截 rm -rf / git push -f / git reset --hard / sudo / diskutil 等 15 类

注册方式（settings.json hooks）：

```json
"PreToolUse": [
  { "hooks": [ { "command": "python3 /Users/<you>/.claude/skills/pm-orchestrator/scripts/pm-guard.py",
                 "timeout": 3, "type": "command" } ] }
]
```

## 设计依据

详细审计与调研见 `docs/audit-2026-08.md`（73 技能盘点、P0–P3 十四项改进、36 次检索的方案合成）。

## 注意

- 密钥 / API Key / settings.json 不入仓库；凭证只放系统 Keychain。
- 飞书推送依赖 pm-orchestrator 的 `pm_feishu.py`（claude-code-pm-orchestrator 仓库）。
