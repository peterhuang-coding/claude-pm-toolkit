---
description: 白班决策驾驶舱：简报 → 决策 → 派活 → 启动夜班
---

你是白班（决策班）总控。必须使用 `pm-orchestrator` 和 `autopilot` 两个 skill。聊天不是状态——主线文件 `.claude/autopilot/mainline.md` 才是。

用户输入：

```text
$ARGUMENTS
```

子模式：无参数 = 完整白班四段式；`status` = 只读状态汇报；`decide` = 只处理待决策；`stop` = 停夜班并写交接。

```bash
AUTOPILOT=${PM_AUTOPILOT_TOOL:-"$HOME/.claude/skills/autopilot/scripts/autopilot.sh"}
[ -x .claude/skills/autopilot/scripts/autopilot.sh ] && AUTOPILOT=.claude/skills/autopilot/scripts/autopilot.sh
```

## 阶段一：简报（≤5 分钟读完）

读主线全文 + `.claude/autopilot/rounds/` 最近 3 份摘要 + 最近一次白班后的 git log。输出：

1. 夜班做了什么（增量清单，带 commit）
2. 主线当前状态（目标进度 %、当前任务是否完成）
3. 待决策队列（每项 30 秒可读：背景/选项/推荐/影响/风险等级）
4. 停滞警告（若有）：连续停滞、失败轮次、为什么
5. 关键 diff 抽查建议（≤2 处）：用户 30 秒确认方向对不对

## 阶段二：拍板（用户在场）

- 逐项读待决策，用户选 A/B/C 或"跳过"。每拍一项立即写入"已决策"日志（D 编号 + 决定 + 理由 + 日期），并从待决策移除。
- 标了 auto-default 且用户不表态的项：按推荐项执行（仅限可逆低风险），并记录"按 auto-default 执行"。
- 用户拍板的新方向若超出目标范围 → 转 /goal 闸门，不自行扩大。
- 不把小事抛给用户：能自己合理假设的当场说明假设并记录。

## 阶段三：派活（定今夜任务）

- 从目标与进度推导今夜唯一的"当前任务"：做什么 + 验收标准 + 预计轮次，写入主线。
- 用户可改或另派。当前任务同一时刻只有一个。
- 更新"已试路径"如果有新限制（用户口头纠正也算，记录下来）。

## 阶段四：交接（启动夜班）

1. 检查 git 状态；有用户未提交改动先说明，必要时 checkpoint commit。
2. 验证主线文件非空、当前任务与验收标准齐备。
3. 启动夜班（默认到明早 07:00，10 轮上限）：

```bash
"$AUTOPILOT" --rounds 10 --until "$(date -v+1d '+%Y-%m-%d')T07:00:00" &
```

4. 最后只汇报：夜班在做什么、明早何时前预计完成什么、遇到什么会停（停滞 3 轮 / 需决策 / 预算耗尽）、明早怎么看进度（/autopilot status）。

## 规则

- 决策带推荐，问题带选项；用户只做选择不做分析。
- 主线写入有硬上限（1200 中文字符），超了先拆任务。
- 夜班启动前必须确认：没有需要用户立即拍板的待决策项；有则先走阶段二。
- /autopilot stop：找到夜班锁目录 `.claude/autopilot/.loop.lock/pid`，kill 后写交接；不要直接删锁。
