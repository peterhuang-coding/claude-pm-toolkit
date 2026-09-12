# 运行与开发

产品定位和快速启动见 [README](../README.md)，接口和任务状态见 [任务目标与 API](task-contract.md)。当前运行版本是本地单用户原型，WorkBuddy 适配器在 macOS 验证。

## 代码结构

```text
taskrouter/
  main.py                 FastAPI 与启动 / 停止生命周期
  dashboard.html          登录候选、连通测试、任务提交和验收
  api/subagents.py        本地账号及客户端任务接口
  core/delegation.py      任务准入、输入包、串行执行、结果、验收与恢复
  adapters/workbuddy.py   WorkBuddy 产品核验与有界文本 CLI 调用
  db.py                   SQLite WAL、迁移及任务 / 尝试 / 产物 / 验收记录
  config.py               运行目录与本地服务配置
  core/fsm.py             表驱动状态机与事件记录
  core/service.py         通用任务创建、查询与详情
  core/loops.py           调度、执行和监视循环
  core/router.py          旧通用路由的硬约束过滤、评分和候选回退链
  core/registry.py         能力 / 提供方注册与健康同步
  core/llmhub.py           可选的 llm-hub 只读客户端
  core/recovery.py         旧通用任务恢复，不重放客户端派发任务
  api/tasks.py            原有 /api/tasks 接口
  api/providers.py        提供方、能力和 llm-hub 只读信息
  registry_seed.json      旧通用路由种子；不含凭证明文
  router_rules.json       通用规则与评分配置
  sla_templates.json      四类旧 SLA 模板
scripts/
  subagent.py             当前派发 CLI：accounts / probe / submit / get / review / cancel
  taskctl.py              旧通用任务 CLI：建任务、查询和查看路由
  install-launchd.sh      原维护环境的 macOS 常驻脚本
skills/task-tiering/      主代理任务分级与下游调用约定
examples/                 公开可运行的合成请求
tests/                    单元与集成测试
```

## 数据与配置

默认运行目录为 `~/taskrouter/`，数据库为 `taskrouter.db`，结果保存在 `artifacts/<task-id>/result.json`。任务原文和验收记录同样存于本机数据库，运行目录应放在代码仓外。

`TASKROUTER_HOME` 可覆盖数据目录。使用 uvicorn 时，实际监听地址和端口由 `--host`、`--port` 指定；CLI 换端口时使用 `--url`。不要仅修改配置变量而忽略启动参数。

`LLMHUB_BASE_URL` 默认指向本机 3457 端口。llm-hub 健康同步失败不阻止 WorkBuddy 链路；其请求为只读，且绕过系统 HTTP 代理。旧注册表只保存凭证位置引用或存在性信息，不向任务 API 返回凭证明文。

## 主代理接入

保持代码目录路径稳定，按 [task-tiering](../skills/task-tiering/SKILL.md) 的约定调用 `scripts/subagent.py`。Codex skill 的本机入口可链接到仓库源码；下面适用于入口尚不存在的情况，已有入口时先核对其目标：

```bash
mkdir -p "$HOME/.codex/skills"
ln -s "$PWD/skills/task-tiering" "$HOME/.codex/skills/task-tiering"
```

在新任务中检查技能是否出现在可用清单；也可显式使用 `$task-tiering`。文件存在、主代理发现技能、真实派发成功分别验证。安装技能并不会把本地 API 注册成 Codex 原生 subagent 工具。

## macOS 常驻

先用 README 中的前台方式验证。现有 `scripts/install-launchd.sh` 仍为原维护环境编写，Python 固定为 `/opt/anaconda3/bin/python3`，端口默认 3459；换机器前需核对或调整脚本中的 Python 路径及对应依赖。

```bash
./scripts/install-launchd.sh install
# 首次加载：安装脚本只写配置和运行副本，不自动启动。
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.user.taskrouter.plist"
./scripts/install-launchd.sh status
```

脚本将 `taskrouter/` 同步到 `~/taskrouter/app/`，由本机副本运行。代码更新后重新执行 install，并在服务已加载时用 `launchctl kickstart -k "gui/$(id -u)/com.user.taskrouter"` 重启。日志位于 `~/taskrouter/logs/`。`uninstall` 停止并移除 launchd 配置，保留运行数据。

重启前应处理已有任务：本链路仍排队或执行中的任务会停止，已返回结果保留。避免让前台与 launchd 两个实例同时运行在同一个数据目录。

## 验证

在 README 的 Python 环境中安装 pytest 后运行：

```bash
python -m pip install pytest
python -m pytest tests/ -q
```

测试覆盖任务状态、事务一致性、规则路由、客户端准入、格式 / 数量检查、验收记录、取消、超时和重启恢复。测试通过不能证明真实登录持续有效；真实客户端验证另见 [验证记录](login-delegation-validation.md)，其中的探针与批次会消耗平台额度。

本仓库尚未配置 GitHub Actions；本地测试通过不等同于远端 CI 已运行。

## 原通用任务内核

早期 M1 / M2 已实现 SQLite 持久化、SLA 模板、提供方注册、硬过滤和软评分，以及可解释的路由决策。可通过 `scripts/taskctl.py templates` 和 `list` 查看；`/api/tasks` 与当前 `/api/subagents` 的执行路径不同。

旧路由选出候选后停在 `routing`，原因为 `awaiting-adapter`，并不表示通用模型或工具已经执行。候选回退链只是已保存的路由结果，通用收费 API / 代码编辑适配器和跨平台预算执行尚未实现。

当前 WorkBuddy 链路复用任务、输入包、attempt、artifact、evaluation 记录，单独处理串行执行和重启行为。历史 [PRD](prd.md)、[试点计划](superpowers/plans/2026-09-11-tokenmaxxing-pilot.md) 和 [登录工作台计划](superpowers/plans/2026-09-12-login-delegation.md) 供追溯使用，不作为所有功能均已交付的声明。
