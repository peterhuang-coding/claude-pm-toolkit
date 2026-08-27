---
description: 上下文工作台：查看所有进行中工作的树视图，或一键重启全部上下文
---

你是上下文工作台总控。必须使用 `pm-orchestrator` skill。

用户输入：

```text
$ARGUMENTS
```

```bash
WORKBENCH=${PM_WORKBENCH_TOOL:-"$HOME/.claude/skills/pm-orchestrator/scripts/pm-workbench.sh"}
[ -x .claude/skills/pm-orchestrator/scripts/pm-workbench.sh ] && WORKBENCH=.claude/skills/pm-orchestrator/scripts/pm-workbench.sh
```

## 模式

**树视图（默认）**：运行 `"$WORKBENCH"`，把输出转成树形展示：

```
项目 A
 ├─ Feature/task-1  running  下一步：…
 ├─ Feature/task-2  review   下一步：…
项目 B
 └─ handoff xxx    blocked   下一步：…
```

数据源只读 Feature 台账 + 活动 handoff；若脚本报 EPERM，提示用户先修复外置卷（diskutil repairVolume），不要硬读。

**一键重启（含 "spawn" / "重启" / "开窗口"）**：
1. 先跑树视图拿到清单。
2. 对每个进行中的事项，在对应项目目录启动一个独立后台会话（各自全新上下文，从 handoff/Feature 恢复）：

```bash
(cd <项目路径> && claude-yolo) &
```

每个事项一个会话；会话里第一句是 `/leader-resume <Task ID>` 或读取对应 Feature 的下一步。启动完成后用 `claude-yolo board` 汇报每个会话的状态。
3. 如果项目路径无法从 hub 解析（EPERM 或未注册），列出清单并询问用户每个事项对应的项目路径，不要猜。

## 规则

- 本命令只做"视图 + 启动"，不修改任何 Feature/handoff 数据。
- 事项数与窗口数必须一一对应；同一事项不重复启动。
- 输出保持一屏：每个事项一行（状态 + 唯一下一步 + 恢复命令）。
