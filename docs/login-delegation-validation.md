# 登录工作台与 Subagent API 验证（2026-09-12）

本地入口：`http://127.0.0.1:3459/dashboard`。基于 Task Router、SQLite 与 FastAPI，通过 macOS launchd 运行。以下是 2026-09-12 的实测记录，不能代替其他机器或后续版本的验证。

## 已实现与本轮实测

- 10 个平台登录卡片：Gemini、Kimi、Cursor、OpenCode、GitHub Copilot、Qoder、TRAE SOLO、Qwen、DeepSeek、豆包；另列本机 WorkBuddy。本人登录确认与客户端连通状态独立保存。
- 浏览器 DOM 与截图确认登录卡片、计数、表单和 WorkBuddy 连通状态正常。点击“一次打开”后页面报告发起 10 个窗口，但内置浏览器的标签列表未反映这 10 个窗口；不能宣称十个标签都已成功加载。逐页入口可用，外站自动导航曾多次超时；用户可从普通浏览器访问本地工作台完成登录。
- `/api/subagents` 提交/列表/详情、取消、验收；`scripts/subagent.py` 是 CLI 客户端。任务只生成文本或代码草稿，主模型负责审核和应用。
- WorkBuddy 的产品身份、关闭工具/MCP、stdin 最小输入、超时/取消清理、单客户端串行、失败不自动重试或切换付费 API。请求和输入包在同一事务保存；重启将尚未完成执行的任务停止，已返回的结果保留待验收。
- 本轮单元/集成测试 52 项通过，覆盖旧数据库 health 默认值、非急简单任务准入、机密/复杂/紧急输入拒绝、不支持字段拒绝、跨站写入、本人登录标记不解锁路由、返回后验收、取消清理、进程组超时终止、输入写失败不泄漏排队任务。
- 分级 skill 源码为本仓库 `skills/task-tiering`；本机 Codex symlink 指向该源码，quick_validate 通过。同日后续任务的技能清单已出现 task-tiering，文件存在与主代理发现分别得到核实。

## 真实批次

任务 `t_2myzjuhe`，经 CLI → 本地 API → 队列 → WorkBuddy → 结果 → 主模型验收。

- 8 条合成中文反馈；字段、数量、ID 顺序、标签/优先级与独立标准答案一致，证据均为对应输入连续原文。8/8 通过。
- 模型由 WorkBuddy 选择为 `glm-5.3`；11.12 秒。报告输入 3367、输出 365，平台原生 credit 0.74。报告美元为 0 不代表免费；本轮未测总体节省百分比。
- 在待验收状态重启服务后仍为 verifying，仅一个 attempt；随后调用 review 通过，任务 completed。
- 维护者本地证据位于运行目录的 `experiments/subagent-api-20260912/`：request.json、response.json、verification.json、final-response.json；产物位于 `artifacts/t_2myzjuhe/result.json`。这些运行文件未随代码公开。仓库另附 [4 条合成反馈示例](../examples/feedback-classification.json) 用于试跑，它与本次 8 条基准不是同一批数据。

## 后续边界

只有 WorkBuddy 是已实现并测通的执行器。其他平台尚待用户登录、官方客户端授权、权益核实和接入测试；网页 cookie 不会被提取成 API key。模型最优选择与额度路由需要多个可用选项和实际评测，目前仅沿用平台自动选择并记录实际模型。

新请求使用轮询交付，未增加消息通知。一次成功不代表持续可用；相同 HTTP 请求重复提交会建不同任务，网络结果不确定时应查询已提交任务，不能盲目重发。未完成通用代码编辑代理、自动代码应用或多平台选择。

## 官方入口依据

- [GitHub Copilot CLI 认证](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
- [Qoder CLI 登录](https://docs.qoder.com/cli/authentication)
- [Gemini CLI 配额](https://geminicli.com/docs/resources/quota-and-pricing/)
- [Kimi Code 会员额度](https://www.kimi.com/code/docs/en/kimi-code/membership.html)
- [Cursor CLI](https://prod.cursor.com/help/integrations/cli)
- [OpenCode Zen 模型及数据政策](https://opencode.ai/docs/zen/)
