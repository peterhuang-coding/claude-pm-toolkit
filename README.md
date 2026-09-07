# toolkits — Task Router（任务执行控制面）

本地优先的 Task Execution Control Plane。用户声明任务目标与 SLA（截止时间、正确性、预算、风险、数据敏感性），
Task Router 负责：选择执行器（模型 API / CLI Agent / 本地工具 / 搜索爬虫 / Computer Use）、
准备版本化 Context Pack、运行版本化 Harness、管理 Provider 额度与回退、验收结果、留存证据，
并在需要时请求人工决策。

**Task First**：Agent Session 只是一次 Attempt。

- Goal: `20260908-012204-task-router-52465`（已批准 2026-09-07）
- 对标：Inferable（MIT，durable workflows + HITL）；差异化：Provider 额度账本/熔断/回退、Context Pack 版本化、规则路由
- P0：Task/Attempt/Event/Artifact/Decision 持久化；四类 SLA 模板与规则 Router；Context Pack / Harness Registry / Provider Registry；Claude Code、OpenAI-compatible API、确定性工具三个 Adapter；基础 Verifier、额度管理、回退与 Task 看板。
- 复用资产：`~/.claude/skills/llm-hub`（Provider 路由/额度/Keychain）、`skill-hub`（管理 UI）、`autopilot`（异步执行）、`pm-guard`（副作用门禁）。

状态：P0 未开工（goal 已批准，2026-09-08 完成项目注册，进入技术方案阶段）。
