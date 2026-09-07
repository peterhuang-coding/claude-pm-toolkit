# Task Router 任务路由与执行控制面 — PRD（用户原稿，2026-09-08）

> 来源：用户 2026-09-08 会话提供的需求原稿。Goal 批准摘要见 handoff `20260908-012204-task-router-52465/goal.md`。

## 需求摘要

Task Router 是一个本地优先的任务执行控制面。用户提交的是带 SLA 和约束的 Task，系统负责准备上下文、选择 Harness 和下游能力、管理 API 额度与失败回退、验证结果，并在需要时请求人工决策。Agent、模型、爬虫和 Computer Use 都是可替换执行器，不是核心业务对象。

## 产品目标

为持续运行或异步运行的 AI 任务提供统一的路由与执行控制面。系统以任务为核心，根据截止时间、正确性要求、预算、风险、数据敏感性和上下文需求，为任务选择合适的模型、API、爬虫、外部 Agent、本地工具或 Computer Use，并持续管理上下文、Harness、额度、执行状态、验收证据和人工接管。

Agent 不是产品核心，只是可替换的执行器。产品核心是：任务契约、上下文管理、Harness 管理、API/Provider 管理、路由与回退、结果验收和任务历史。

## 当前确认的核心诉求

- 查看当前排队、路由、运行、等待、阻塞、验证、失败和已完成的任务。
- 查看每个任务当前由哪个执行器处理、为什么选择它、使用了哪份上下文和哪套 Harness。
- 查看 Token 消耗、费用、预算使用率和消耗趋势。
- 查看任务执行进度、尝试历史、产物、验证结果、异常原因和回退路径。
- 从统一入口暂停、恢复、停止、重试、升级能力、降低要求或调整任务优先级。
- 只突出需要人工处理的任务，降低逐个检查后台会话的成本。

## 目标用户与核心场景

**目标用户**

- 同时运行多个 AI 任务的技术型产品经理、独立开发者和小团队负责人。
- 已经使用 Claude Code、Codex、模型 API、搜索 API 或浏览器 Agent，但缺少统一管理能力的人。
- 拥有 Mac mini 等常驻设备，并希望在 MBP 或浏览器中管理异步任务的人。

**核心场景**

- 用户提交"5 分钟内给出大致答案"的任务，系统优先调用低延迟免费或低价 API。
- 用户提交"隔夜完成、必须有可靠来源"的调研，系统调用异步 Research Agent、多轮搜索和 Verifier。
- 用户提交代码任务，系统准备仓库上下文和编码 Harness，分配给 Claude Code、Codex 或其他编码执行器。
- 免费 Provider 额度耗尽或限流时，任务按照预设策略等待、回退、切换付费能力或请求用户决定。
- 目标系统没有 API 时，系统通过 Computer Use 完成低频网页操作，并保存截图和操作证据。

## 产品目标与成功指标

- 用户只需声明任务目标和 SLA，不需要先选择具体 Agent 或模型。
- 至少 60% 的低风险任务由本地、免费或低价能力完成。
- Provider 限流、进程退出或设备离线后，任务状态不丢失，并能自动恢复或明确阻塞。
- 任何已完成任务都可以回答：谁执行、用了什么上下文、花了多少、经过什么验证、为什么可信。
- 用户能在 30 秒内理解异常任务的当前状态和唯一下一步操作。
- "系统声称完成但验收失败"的错误完成率低于 5%，高风险任务不得未经批准产生不可逆副作用。

## 核心原则

- **Task First**：Task 是主对象，Agent Session 只是一次 Attempt。
- **能力可替换**：同一 Task 可以由 API、CLI Agent、本地工具、爬虫或 Computer Use 执行。
- **上下文与执行分离**：Context Pack 独立生成、版本化并可复用，不绑定具体模型会话。
- **Harness 显式化**：每类任务通过版本化 Harness 定义工具、提示、权限、超时和验收方式。
- **先过滤再评分**：先排除不满足隐私、风险、能力和时限要求的执行器，再比较成本、质量和速度。
- **完成必须有证据**：执行器输出不等于任务完成，必须通过 Verifier。
- **免费能力不构成承诺**：免费额度是动态容量，必须支持同步、熔断、等待和回退。

## 核心对象

| 对象 | 说明 |
|---|---|
| Task | 用户目标及其 SLA、预算、风险、输入输出和验收标准 |
| Context Pack | 某次执行使用的上下文快照、摘要、文件和来源 |
| Harness | 面向一类任务的执行契约，定义工具、权限、提示、限制和验证器 |
| Capability | 可执行某类工作的能力，例如模型、搜索、爬虫、编码 Agent、浏览器或脚本 |
| Provider | 提供 Capability 的账号和服务端，包括额度、价格、健康度和凭证引用 |
| Route Decision | 候选能力、排除原因、评分、最终选择和回退链 |
| Attempt | 一次具体执行，记录输入、执行器、状态、成本、事件和输出 |
| Artifact | 任务产物，例如答案、文件、Git commit、截图、报告和日志摘要 |
| Evaluation | 对 Attempt 结果的规则、测试、来源或模型复核结果 |
| Decision | 用户对批准、降级、升级、停止、发布等动作的确认记录 |

**Task Contract** 每个 Task 至少包含：目标与预期输出类型；deadline 与 max_wait_time；correctness_target（近似可用/一般可靠/必须验证/人工批准）；max_cost 和是否允许付费；risk_level 与允许的副作用等级；数据敏感性和允许离开本机的数据范围；输入资源、项目、仓库和依赖任务；验收标准、验证预算和降级策略；交付方式（立即返回/先草稿后终稿/异步通知）。

## 产品定位

Task Execution Control Plane，不是传统项目管理工具，也不是某个 Agent 的管理器。Claude Code、Codex、模型 API、搜索 API、爬虫、外部 Agent 和 Computer Use 都通过 Adapter 成为下游能力。

一句话定义：用户声明任务的 SLA 和约束，Task Router 负责选择谁来执行、提供什么上下文、使用哪套 Harness、失败如何回退、结果如何验收，并保留完整证据。

**与个人 AI 助手的区别**：个人 Agent 以"人"为中心（记忆、偏好、会话、人格）；Task Router 以"任务"为中心（Task Contract、Context Pack、Harness、Attempt、Route Decision、Evaluation），执行器可在任务间或 Attempt 间替换。"省调用"不等于减少请求总数，而是减少昂贵、重复和无效调用：多次廉价调用做搜索/抽取/候选，一次高能力调用验收。

**与省 Token Skill 的关系**：省 Token Skill 是 Task Router 内部一类 Harness 优化器（优化单次执行：按需工具说明、上下文裁剪、提示词缓存复用）；Task Router 优化完整任务链。不得为省 Token 删除 Task Contract、最新决策、失败路径和验收标准；高正确性任务不得静默降级。

默认自动链路：Task 分类 → SLA 模板 → Capability 路由 → Context 优化 → Harness 执行 → Verifier → 质量与成本回写。

## MVP 页面结构

1. 任务总览：排队、运行、需决策、验证失败、今日 Token、今日费用、免费节省、预算风险。
2. 任务队列：任务、SLA、优先级、当前阶段、选中能力、截止时间、费用、最近事件。
3. 任务详情：Task Contract、执行时间线、Route Decision、Context Pack、Harness、Attempt、Artifact、Evaluation。
4. 能力与 Harness：能力注册、适用任务、输入输出、权限、超时、版本、历史成功率。
5. Provider 与额度中心：账号、模型/API、价格、免费额度、重置时间、健康度、限流、熔断状态。
6. 成本与质量中心：按任务类型/Harness/Capability/Provider/时间统计成本、延迟、成功率、质量。
7. 操作与决策中心：暂停、恢复、停止、重试、升级、降级、切换执行器、批准副作用。
8. 告警中心：超时、无进展、额度不足、重复调用、权限等待、验收失败、设备离线、进程失联。

## 核心业务流程

1. 用户创建 Task，选择或使用默认 SLA 模板。
2. 系统标准化任务，必要时拆成相互依赖的子任务（MVP 自动拆解需用户确认）。
3. Context Manager 生成版本化 Context Pack。
4. Router 先按硬约束筛选 Capability，再按质量、时延、成本、额度、历史表现评分。
5. Harness Runner 创建 Attempt，执行并持续记录事件、成本和产物。
6. Verifier 按 Task Contract 验收；不通过则重试、切换能力或升级。
7. 达到人工门禁进入 needs-decision。
8. 通过验收后生成最终 Artifact，质量与成本反馈给 Router。

## 任务状态机

```
draft → queued → preparing-context → routing → running → verifying → completed
```

异常/人工分支：queued/routing → waiting-capacity；running → retrying；running/verifying → needs-decision；running → paused/cancelled；retrying → failed；verifying → routing（验收失败升级/换能力）。

## 功能需求（摘要）

- **任务创建与 SLA 模板**：一句话/API/CLI/外部 Issue 创建；四个默认模板"即时近似/即时可靠/异步经济/异步严谨"；高级设置可改截止、正确性、成本、隐私、验证、降级；创建前展示预计能力范围与最大成本。
- **任务拆解与依赖**：父任务拆子任务、依赖关系、独立 SLA/Context/Harness/执行器；父任务在必要子任务验收后才可完成；MVP 自动拆解需用户确认。
- **Context Manager**：统一管理输入/文件/仓库/网页快照/历史决策/前序产物/运行摘要；Attempt 绑定不可变 Context Pack 版本；按执行器上下文限制裁剪，优先保留任务约束、最新决策、失败路径、验收标准；敏感上下文标记 local-only 不得上云；长任务每轮生成结构化摘要。
- **Harness Registry**：按任务类型注册（quick-answer、deep-research、code-change、web-action、data-extract 等）；定义输入输出格式、工具、系统提示、权限、超时、最大轮次、终止条件、Verifier；版本化，Attempt 记录实际版本；支持启停/灰度/复制/回滚；与 Capability 解耦。
- **Capability 与 Provider 管理**：Capability=能做什么，Provider=谁提供/如何计费/是否可用；支持 OpenAI-compatible、Anthropic API、CLI Agent、本地脚本、MCP、A2A、搜索、爬虫、Computer Use Adapter；Provider 显示价格、额度单位、余额、重置时间、RPM/TPM/RPD、错误率、延迟；凭证只存安全引用；预算/并发/熔断/冷却/重试/禁用。
- **Rule Router**：第一阶段规则路由不训练；硬约束（任务类型、数据敏感性、风险、能力、上下文上限、截止、预算）→ 软评分（历史质量、预计完成时间、完整链路成本、额度余量、缓存命中、健康度）；每次路由保存候选、排除原因、得分、选择、回退链；支持强制指定/禁止 Provider/人工重路由。
- **Attempt 与执行控制**：多 Attempt、并发策略；心跳、阶段、工具调用摘要、Token、费用、产物、错误、停止原因；超时/暂停/取消/重试/切换执行器；副作用动作用幂等键；发送/发布/支付/删除/合并走风险门禁；Computer Use Attempt 保存关键截图、目标域名、动作摘要、人工批准点。
- **Verifier**：JSON Schema、规则、测试命令、来源数量、交叉验证、高能力模型复核；验收失败不得 completed；验证结果进入历史质量统计；高风险任务自动验证通过仍可要求人工终批。
- **看板与人工接管**：首页以 Task 为列表主项；详情可查 Context/Harness/路由理由/Attempt 时间线/验证/产物；用户可继续等待/降正确性/加预算/切执行器/批准/停止；每个异常任务给出唯一推荐下一步。

## MVP 非目标

不替代 Jira/Linear/GitHub Issues；不做复杂需求规划和完整 PM 工作流；不自建模型或通用 Agent Runtime；不做企业多租户和复杂 RBAC；不训练学习型 Router 或自动改路由规则；不把消费者网页账号当稳定免费 API 池；不做通用分布式工作流平台/K8s；不承诺第三方免费额度长期存在。

## 待明确（P1 决策，不阻塞 P0）

1. 首版编码 Harness 默认 Claude Code，第二个执行器是否 Codex。
2. 首版免费模型 Adapter 优先顺序：OpenRouter / Gemini / Groq / NVIDIA NIM。
3. 异步严谨任务：Tavily Research / Exa / OpenAI Deep Research / 自建 GPT Researcher。
4. MBP Worker ↔ Mac mini：私有网络长轮询 vs SSH 隧道。

## 执行边界

优先级：官方 API → CLI → MCP/A2A → Computer Use。Computer Use 仅用于无结构化接口的低频网页动作，不作高频模型调用或绕过付费/限流。低成本模型必须输出结构化结果并经验收。首版规则路由，先积累真实成本/质量/失败数据。

## 任务分级：时效 × 正确性双轴

- deadline：立即 / 分钟级 / 小时级 / 隔夜 / 无明确时限。
- correctness_target：近似可用 / 一般可靠 / 必须验证 / 关键结果人工批准。

四种路由策略：

| 时效 | 正确性 | 默认策略 |
|---|---|---|
| 高 | 低 | 低延迟免费/低价 API，限输出长度，不做多轮搜索 |
| 高 | 高 | 高能力模型直接执行，必要时并行两能力快速交叉校验 |
| 低 | 低 | 本地模型、批处理或等待免费额度恢复 |
| 低 | 高 | 异步 Research Agent/外部 Agent/多轮搜索；结果进 Verifier，必要时人工复核 |

路由规则还需：max_wait_time、verification_budget、delivery_mode（立即/草稿后终稿/异步通知）、degradation_policy（降正确性/切付费/保持等待并通知）。

## MVP 验收标准（10 条）

1. 用户可以不选择 Agent，仅通过目标和 SLA 创建 Task。
2. 系统能对四类默认 SLA 生成可解释的 Route Decision。
3. 至少支持确定性工具、一个模型 API、一个 CLI Agent 和一个搜索/爬虫能力。
4. Provider 限流或额度耗尽后，Task 可进入等待或按回退链继续，不丢失上下文和状态。
5. 每次 Attempt 绑定明确的 Context Pack 与 Harness 版本。
6. 验收失败时 Task 不得进入 completed，并能按策略重新路由。
7. 看板以 Task 为主项，能展示路由理由、成本、额度、验证和人工决策入口。
8. 服务进程重启后，未完成 Task、Attempt、事件和 Artifact 均可恢复。
9. Computer Use 的关键动作有截图与审计记录，高风险动作需要人工批准。
10. API Key 不写入数据库、任务内容、日志或前端响应。

## 分阶段范围

**P0：Task-first 控制面** — Task/Attempt/Event/Artifact/Decision 持久化；四类 SLA 模板与规则 Router；Context Pack、Harness Registry、Provider Registry；Claude Code、OpenAI-compatible API、确定性工具三个 Adapter；基础 Verifier、额度管理、回退和 Task 看板。

**P1：异步研究与多设备 Worker** — Tavily/Exa/Firecrawl/Crawl4AI Adapter；MBP 高能力 Worker 与 Mini 常驻调度；多来源验证、先草稿后终稿、异步通知；Computer Use Adapter 与截图证据。

**P2：策略学习与生态** — 基于历史质量/延迟/成本推荐路由规则；MCP/A2A Capability 自动发现；团队、多用户、权限、审计导出；Harness 模板生态和任务类型 Benchmark。
