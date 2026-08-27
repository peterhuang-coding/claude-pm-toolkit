---

name: vision-publish
description: >
  用视觉 Computer Use 控制真实浏览器完成网页发布与操作。Use when the user wants to
  publish, upload, submit or post content to a website, control a browser with AI
  vision, or asks about "computer use", "视觉发布", "自动发布", "控制页面", "AI 发内容".
category: core
---

# Vision Publish — 视觉 Computer Use 发布

## 使命

让 AI 以视觉方式控制真实浏览器完成发布类任务：**看页面 → 理解 → 操作 → 验证**。
核心原则：动作由 AI 视觉理解决定（不是脚本化流程），执行走 CDP（不用 Playwright）。

## 工具层次（按场景选）

1. **会话内（首选）**：chrome-devtools MCP 工具（navigate / screenshot / click / fill / evaluate 等）——模型直接看截图和 DOM 做决策。
2. **无人值守**：`claude --chrome -p "<任务>"`——专用 Chrome 实例 + 打印模式，给夜班/后台任务用。
3. **视觉确认通道（备用）**：主模型不支持看图时，用 `/imageinput`（OpenRouter gemini）对截图做视觉分析，CDP 执行动作。三个层次形成回退链。

## 发布五步

1. **打开目标页**：navigate 到目标平台；登录态复用现有 Chrome profile，不要重复登录。
2. **截图理解**：screenshot 确认当前状态（登录了吗？入口在哪？表单什么样？）。
3. **操作**：click / type / select 逐步执行；每步关键操作后截图确认一次。
4. **校验**：截图前后对比——内容正确、无报错提示、发布按钮真实生效。
5. **记录**：发布 URL、截图证据路径、结果写进项目 handoff 或主线进度区。

## 硬规则

- **支付、删除、正式对外发布、涉及他人账号的操作：必须用户在场确认**，禁止无人值守执行。
- 凭证/密码只从系统 Keychain 读；绝不写进 prompt、handoff 或截图落盘。
- 发布内容必须来自已验证产物（文件或已审批文本），禁止临场编造。
- 遇到验证码 / 2FA：停下来告诉用户，不硬闯。
- 关键步骤留截图证据到任务目录。

## 与现有体系衔接

- 夜班（autopilot）做「发布前准备」：内容、截图、目标路径全部就绪，写入主线；
- 白班（你）用 `/publish` 一键完成正式发布——发布这最后一步永远在人在场时做。
