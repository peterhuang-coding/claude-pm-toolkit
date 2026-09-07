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

- **M1 持久化内核（已交付）**：SQLite(WAL) 12 张表 + user_version 迁移；表驱动 FSM（事务 CAS + 事件时间线）；
  四类 SLA 模板；建 Task API/CLI（只填目标+SLA，不选 agent，AC1）；launchd 安装脚本；
  启动重启收割（running→retrying、verifying→queued，AC8）。
- M2：llm-hub 只读集成 + Capability/Provider 注册 + 硬过滤软评分 RouteDecision（AC2/10）。
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
  core/loops.py        # scheduler（M1 走到 routing 挂起）/ runner / watchdog 占位
  api/tasks.py         # /api/tasks、/api/tasks/{id}、/api/sla-templates、/api/health
  adapters/            # 空占位（M3）
  harnesses/           # Harness JSON（M3，git 版本化）
  sla_templates.json   # 四模板：即时近似/即时可靠/异步经济/异步严谨
  router_rules.json    # 路由规则占位（M2）
  dashboard.html       # 看板占位（M5）
scripts/
  taskctl.py           # CLI（stdlib urllib）：templates/create/list/show
  install-launchd.sh   # launchd 安装/卸载/状态（install 不自动 load）
tests/                 # pytest：四模板建任务、非法转移、事件 seq、重启收割
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
/opt/anaconda3/bin/python3 scripts/taskctl.py show <task_id>
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

## 安全约束

- 数据库/任务内容/日志/前端响应中永不存 API Key；providers 表只存 credential_ref（Keychain 引用，M2 起）。
- 代码仓不包含任何密钥；日志不打印请求体。
