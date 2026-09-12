# toolkits — Task Router（任务执行控制面）

本地优先的 Task Execution Control Plane。用户声明任务目标与 SLA（截止时间、正确性、预算、风险、数据敏感性），
Task Router 负责：选择执行器（模型 API / CLI Agent / 本地工具 / 搜索爬虫 / Computer Use）、
准备版本化 Context Pack、运行版本化 Harness、管理 Provider 额度与回退、验收结果、留存证据，
并在需要时请求人工决策。

**Task First**：Agent Session 只是一次 Attempt。

- Goal: `20260908-012204-task-router-52465`（已批准 2026-09-07）
- 对标：Inferable（MIT，durable workflows + HITL）；差异化：Provider 额度账本/熔断/回退、Context Pack 版本化、规则路由
- 复用资产：`~/.claude/skills/llm-hub`（Provider 路由/额度/Keychain）、`skill-hub`（管理 UI）、`autopilot`（异步执行）、`pm-guard`（副作用门禁）。

## 当前进度

### 2026-09-12：登录工作台与客户端 Subagent API

打开 http://127.0.0.1:3459/dashboard：10 个平台的登录入口、本人登录确认、WorkBuddy 连通测试、简单批次提交、结果与验收。网页登录确认和自动调用状态分开；只实现了 WorkBuddy 随包 CLI，其他候选需逐个授权、接入与验收。原 M2 路由记录保留，收费 API adapters 仍后置。

```bash
python3 scripts/subagent.py accounts
python3 scripts/subagent.py probe
python3 scripts/subagent.py submit --request-file /absolute/job/request.json
python3 scripts/subagent.py get t_example
python3 scripts/subagent.py review t_example --passed yes --note '已检查来源、格式和语义'
```

请求文件：`{"goal":"任务及验收要求","input_text":"必要材料","output_format":"json","expected_count":8,"data_sensitivity":"synthetic"}`。
CLI 使用本地 `/api/subagents`，写请求带 `X-TaskRouter-Local: 1`。查询返回 `verifying` 时结果已准备好，由调用者验收后才完成；取消用 `cancel`。单客户端串行，超时杀掉子进程；失败、取消、重启均不会自动重复扣额度或切换收费 API。这里只生成文本和代码草稿，不自动修改项目。相同请求重复提交会建立不同任务；保存首次返回的 ID，网络不确定时先查询列表，不盲目重提。

skill 源码放在本仓库 `skills/task-tiering`，本机 Codex 入口链接到它。先前工具包同步删掉过旧 skill，因此恢复入口不依赖已删除的旧路径。

#### 原有内核里程碑

- **M1 持久化内核（已交付）**：SQLite(WAL) 12 张表 + user_version 迁移；表驱动 FSM（事务 CAS + 事件时间线）；
  四类 SLA 模板；建 Task API/CLI（只填目标+SLA，不选 agent，AC1）；launchd 安装脚本；
  启动重启收割（running→retrying、verifying→queued，AC8）。
- **M2 规则路由（已交付）**：Capability/Provider 注册表（registry_seed.json，无密钥）；
  llm-hub 只读集成（/api/health、/api/budget，httpx trust_env=False 绕本机代理）；
  硬约束过滤（任务类型/expected_capabilities、local_only→排云端、风险与副作用权限、上下文窗口、
  时限 vs ETA、max_cost/allow_paid/Provider 日预算、可用性=enabled 且 key_present 且未熔断/未额度耗尽
  且 error_rate<0.5，语义对齐 llm-hub ccr-router.js）；软评分（cost/latency/quality/quota_headroom/health，
  权重读 SLA contract.weights）；确定性工具满足硬约束时排最前；force/ban 人工覆盖（force 不覆盖安全类硬约束）；
  route_decisions 落库（全部候选、排除原因、分项得分、chosen、有序 fallback_chain、rules_version、forced）；
  路由后任务挂 routing + wait_reason='awaiting-adapter'（M3 才有 adapter 执行）。AC2/10。
- M3：三 Adapter（deterministic 含 url_fetch / openai_api 读 Keychain / claude_cli）+ ContextPack 不可变 + Harness 版本（AC3/5）。
- M4：额度账本 + 429 回退链 + waiting-capacity + Verifier（AC4/6）。
- M5：看板三页 + 决策门禁 + 副作用幂等键 + CU stub（AC7/9）。

## 目录结构

```
taskrouter/
  config.py            # 集中路径：运行数据在 ~/taskrouter/（TASKROUTER_HOME 可覆盖）
  db.py                # SQLite WAL，12 表 DDL，PRAGMA user_version 迁移
  main.py              # FastAPI app；lifespan：建表→重启收割→三个 asyncio 循环
  core/fsm.py          # 状态机：转移表 + transition()（单事务 CAS + events 行）
  core/service.py      # 建/列/详情任务逻辑（不选 agent/模型）
  core/recovery.py     # reap_stale() 重启收割
  core/loops.py        # scheduler：走到 routing→触发 router→挂 awaiting-adapter；runner/watchdog 占位
  core/llmhub.py       # llm-hub 只读客户端（httpx trust_env=False，20s 缓存，不请求任何密钥）
  core/registry.py     # 注册表 seed 幂等 upsert + llm-hub 健康/额度同步（Keychain 只查 exit code）
  core/router.py       # 规则路由：硬过滤→软评分→确定性优先→fallback 链；route_decisions 落库
  api/tasks.py         # /api/tasks、/api/tasks/{id}、/api/sla-templates、reroute、route-decision
  api/providers.py     # /api/providers、/api/capabilities、/api/llmhub/health|budget（只读代理）
  adapters/            # 空占位（M3；M2 注册的 capability 均 adapter_ready=false）
  harnesses/           # Harness JSON（M3，git 版本化）
  sla_templates.json   # 四模板：即时近似/即时可靠/异步经济/异步严谨
  router_rules.json    # 路由规则：硬约束开关、软评分字段、默认权重
  registry_seed.json   # provider/capability 注册种子（仅 Keychain/env 引用，无密钥）
  dashboard.html       # 看板占位（M5）
scripts/
  taskctl.py           # CLI（stdlib urllib）：templates/create/list/show/reroute
  install-launchd.sh   # launchd 安装/卸载/状态（install 不自动 load）
tests/                 # pytest：四模板建任务、FSM、重启收割、硬过滤/软评分/fallback/force-ban/零密钥
```

## 本地运行（开发）

```bash
cd /Volumes/SanDisk2TB/toolkits
/opt/anaconda3/bin/python3 -m uvicorn taskrouter.main:app --host 127.0.0.1 --port 3459
# 另一个终端：
/opt/anaconda3/bin/python3 scripts/taskctl.py templates
/opt/anaconda3/bin/python3 scripts/taskctl.py create "5 分钟内总结这篇文章要点" --sla instant-approx
/opt/anaconda3/bin/python3 scripts/taskctl.py create "隔夜调研竞品，要有可靠来源" --sla async-rigorous
/opt/anaconda3/bin/python3 scripts/taskctl.py list
/opt/anaconda3/bin/python3 scripts/taskctl.py show <task_id>   # 含 route decision：候选/排除原因/得分/fallback 链

# 能力与 Provider（M2）
curl -s --noproxy '*' http://127.0.0.1:3459/api/capabilities | python3 -m json.tool
curl -s --noproxy '*' http://127.0.0.1:3459/api/providers | python3 -m json.tool
curl -s --noproxy '*' http://127.0.0.1:3459/api/llmhub/health | python3 -m json.tool   # llm-hub 只读代理

# 人工强制/禁止（force 只覆盖可用性/经济类硬约束，安全类 local_only/风险/副作用不可覆盖）
/opt/anaconda3/bin/python3 scripts/taskctl.py reroute <task_id> --ban deepseek
/opt/anaconda3/bin/python3 scripts/taskctl.py reroute <task_id> --force llm-openrouter
```

运行数据（db/logs/artifacts）一律写 `~/taskrouter/`，不进代码仓。

## launchd 常驻

```bash
./scripts/install-launchd.sh install    # 同步代码副本到 ~/taskrouter/app + 生成 plist（不自动 load）
launchctl load ~/Library/LaunchAgents/com.user.taskrouter.plist
./scripts/install-launchd.sh status
./scripts/install-launchd.sh uninstall  # unload + 删 plist（保留数据）
```

plist 要点：WorkingDirectory=~/taskrouter，PYTHONPATH=~/taskrouter/app，KeepAlive+RunAtLoad，
NO_PROXY=127.0.0.1,localhost（本机代理环境必须绕过），日志 ~/taskrouter/logs/。

## 测试

```bash
/opt/anaconda3/bin/python3 -m pytest tests/ -q
```

## 安全约束（AC10）

- 数据库/任务内容/日志/API 响应中永不出现 API Key：providers 表只存 keychain_service/keychain_account/env_var
  名称与 credential_ref（如 `keychain:llm-hub/deepseek`）；代码不读取 Keychain 明文
  （M2 只通过 llm-hub `/api/health` 的 key_present 布尔或 `security ...` 退出码判断凭证是否存在）。
- llm-hub 集成全程只读（GET /api/health、/api/budget），服务端用 httpx trust_env=False 绕过本机代理；
  不修改 llm-hub 任何文件与状态。
- 代码仓不包含任何密钥；日志不打印请求体；零密钥由 tests/test_router.py 两个用例持续断言。
