# Tokenmaxxing 单平台实验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to execute this plan task by task in the current session. 不启动额外 agent 或后台任务。

**Goal:** 用一个短命 CLI 和可视化 HTML 报告，测出 sfkey-glm 是否能可靠承担一类简单任务。

**Architecture:** 实验 CLI 直接调用已登记平台，复用 Keychain 身份，在内存中完成认证。每个请求只接收单条输入，输出经过标准答案验证后写入 JSON/HTML；本阶段不接通生产队列。

**Tech Stack:** Python 3 标准库、pytest、静态 HTML；之后复用现有 FastAPI/SQLite 工作台。

---

## 当前证据与执行顺序

产品设计见 `../specs/2026-09-11-tokenmaxxing-design.md`。

已做的连接实验返回 401 Invalid token。当前可以完成离线开发、验收测试和 HTML 检查；正式平台请求须等 sfkey-glm 的有效凭证和权益信息就绪。此计划中的新脚本/测试/样本尚未写入产品代码。

本计划只实现可独立交付的单平台实验。实验通过后，另写「现有 3459 工作台接入」实施计划，避免把多平台控制面提前纳入本次。

## 文件职责

- Create `scripts/tokenmaxx_pilot.py`：平台调用、逐条验收、请求限额、JSON/HTML 报告。
- Create `tests/fixtures/tokenmaxx-notes.json`：10 条输入与固定答案。
- Create `tests/test_tokenmaxx_pilot.py`：输入限制、验收与报告转义。
- Runtime `~/taskrouter/experiments/<run-name>/results.json` 和 `report.html`：实验产物，目录不允许复用以避免覆盖证据。
- 保留现有生产文件与 provider 配置。修复 llm-hub 地址约定作为单独的小修复，不能让实验脚本和健康检查各维护一套相反的地址规则。

## Task 1：建立固定样本与验收规则

**Files:** Create `tests/fixtures/tokenmaxx-notes.json`。

- [ ] 新建以下完整样本；请求只发送 input，expected 留在本地。

```json
[
  {"id":"n01","input":"清单导出由林舟负责，截止日期为2026-09-15，目前还没完成。","expected":{"owner":"林舟","deadline":"2026-09-15","status":"pending"}},
  {"id":"n02","input":"清单导出原来由林舟负责，现在改由陈青负责。截止日期仍是2026-09-15，任务尚未完成。","expected":{"owner":"陈青","deadline":"2026-09-15","status":"pending"}},
  {"id":"n03","input":"陈青负责清单导出。原定2026-09-15交付，现改为2026-09-18，仍在进行。","expected":{"owner":"陈青","deadline":"2026-09-18","status":"pending"}},
  {"id":"n04","input":"陈青负责的清单导出任务已取消，原定截止日期2026-09-18已失效。","expected":{"owner":"陈青","deadline":null,"status":"cancelled"}},
  {"id":"n05","input":"清单导出已完成。负责人林舟，约定截止日期为2026-09-15。","expected":{"owner":"林舟","deadline":"2026-09-15","status":"done"}},
  {"id":"n06","input":"清单导出需要有人推进，负责人尚未确定，也没定交付日期。","expected":{"owner":null,"deadline":null,"status":"pending"}},
  {"id":"n07","input":"林舟负责清单导出，计划明天交付，工作仍未完成。记录没有提供当天日期。","expected":{"owner":"林舟","deadline":null,"status":"pending"}},
  {"id":"n08","input":"清单导出尚未完成，截止日期2026-09-15。林舟只是评审人，实际负责人为陈青。","expected":{"owner":"陈青","deadline":"2026-09-15","status":"pending"}},
  {"id":"n09","input":"昨天说清单导出已完成，今天验收后重新打开任务。林舟继续负责，尚未设置新的截止日期。","expected":{"owner":"林舟","deadline":null,"status":"pending"}},
  {"id":"n10","input":"清单导出任务已经取消；没有指定负责人，也没有截止日期。","expected":{"owner":null,"deadline":null,"status":"cancelled"}}
]
```

- [ ] 用 `python3 -m json.tool tests/fixtures/tokenmaxx-notes.json` 检查格式，确认恰好 10 个不同 ID。

## Task 2：先写离线行为测试

**Files:** Create `tests/test_tokenmaxx_pilot.py`。

- [ ] 先加入以下行为测试并运行，预期因模块尚不存在而失败；Task 3 完成后重跑应通过。

```python
import pytest
from scripts import tokenmaxx_pilot as pilot
from scripts.tokenmaxx_pilot import render, validate_cases, verdict
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

EXPECTED = {'owner': None, 'deadline': None, 'status': 'pending'}
CASE = {'id': 'x', 'input': '等待分配。', 'expected': EXPECTED}

def test_verdict_rejects_invented_owner():
    assert not verdict('{"owner":"张三","deadline":null,"status":"pending"}', EXPECTED)
    assert verdict('{"owner":null,"deadline":null,"status":"pending"}', EXPECTED)

def test_verdict_rejects_extra_fields_and_invalid_json():
    assert not verdict('{"owner":null,"deadline":null,"status":"pending","extra":1}', EXPECTED)
    assert not verdict('```json\n{}\n```', EXPECTED)

def test_case_bounds():
    validate_cases([CASE], 3)
    with pytest.raises(ValueError):
        validate_cases([CASE, CASE], 1)
    with pytest.raises(ValueError):
        validate_cases([CASE], 4)
    with pytest.raises(ValueError):
        validate_cases([{**CASE, 'input': 'x' * 4001}], 1)

def test_report_escapes_model_text():
    page = render([{'error': '<script>alert(1)</script>'}])
    assert '<script>' not in page
    assert '&lt;script&gt;' in page
    assert '未测' in page


def prepare_cli(monkeypatch, tmp_path):
    home = tmp_path / 'home'
    hub = home / 'llm-hub'
    hub.mkdir(parents=True)
    (hub / 'providers.json').write_text(json.dumps({'providers': {'sfkey-glm': {
        'api_base_url': 'https://api.sfkey.cn/v1/chat/completions', 'models': ['test-model']}}}))
    cases = tmp_path / 'cases.json'
    cases.write_text(json.dumps([CASE, {**CASE, 'id': 'y'}]))
    out = tmp_path / 'result'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(pilot.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess([], 0, 'fake-private-value', ''))
    monkeypatch.setattr(sys, 'argv', ['pilot', '--cases', str(cases), '--out', str(out), '--limit', '2'])
    return out


@pytest.mark.parametrize('failure', [401, 429, 'timeout'])
def test_network_failure_stops_and_records(monkeypatch, tmp_path, failure):
    out = prepare_cli(monkeypatch, tmp_path)
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert 'expected' not in request.data.decode()
            if failure == 'timeout':
                raise TimeoutError()
            raise urllib.error.HTTPError(request.full_url, failure, 'failure', {}, None)
    monkeypatch.setattr(pilot.urllib.request, 'build_opener', lambda *a: Opener())
    assert pilot.main() == 1
    assert len(calls) == 1
    rows = json.loads((out / 'results.json').read_text())
    assert len(rows) == 1 and rows[0]['passed'] is False
    assert (out / 'report.html').exists()
    assert 'fake-private-value' not in (out / 'results.json').read_text()


def test_response_cannot_echo_secret_into_artifacts(monkeypatch, tmp_path):
    out = prepare_cli(monkeypatch, tmp_path)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            return json.dumps({'model': 'fake-private-value', 'choices': [{
                'message': {'content': 'fake-private-value'}, 'finish_reason': 'stop'}]}).encode()
    class Opener:
        def open(self, request, timeout): return Response()
    monkeypatch.setattr(pilot.urllib.request, 'build_opener', lambda *a: Opener())
    assert pilot.main() == 1
    assert 'fake-private-value' not in (out / 'results.json').read_text()
    assert 'fake-private-value' not in (out / 'report.html').read_text()
```

## Task 3：实现一次性 CLI

**Files:** Create `scripts/tokenmaxx_pilot.py`。

- [ ] 写入以下完整最小实现。它不修改 provider，不发起子任务，不发送金标准，不跟随携带认证头的重定向。

```python
import argparse
import html
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROMPT = (
    '从这条记录提取当前唯一任务。只输出 JSON 对象，恰好三个字段：'
    'owner（实际负责人或null）、deadline（仍有效的明确YYYY-MM-DD日期或null）、'
    'status（pending/done/cancelled）。以最新变更为准；评审人不算负责人；'
    '不推断相对日期；不要解释或使用Markdown。记录：\n'
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_cases(cases, repeats):
    if not isinstance(cases, list) or not cases or not 1 <= repeats <= 3:
        raise ValueError('invalid cases or repeat count')
    if len(cases) * repeats > 30:
        raise ValueError('maximum 30 requests')
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'id', 'input', 'expected'}:
            raise ValueError('invalid case shape')
        if not isinstance(case['id'], str) or case['id'] in ids:
            raise ValueError('invalid or duplicate id')
        ids.add(case['id'])
        if not isinstance(case['input'], str) or not 1 <= len(case['input']) <= 4000:
            raise ValueError('input must be 1..4000 characters')
        expected = case['expected']
        if not isinstance(expected, dict) or set(expected) != {'owner', 'deadline', 'status'}:
            raise ValueError('invalid expected shape')
        if expected['status'] not in {'pending', 'done', 'cancelled'}:
            raise ValueError('invalid expected status')


def verdict(output, expected):
    try:
        return json.loads(output) == expected
    except (ValueError, TypeError):
        return False


def render(rows):
    body = ''.join(
        '<tr>' + ''.join('<td>' + html.escape(str(row.get(k))) + '</td>'
                        for k in ('case_id', 'repeat', 'http_status', 'passed',
                                  'elapsed_ms', 'tokens_in', 'tokens_out', 'error')) + '</tr>'
        for row in rows
    )
    return ('<!doctype html><meta charset="utf-8"><title>Tokenmaxxing 实验</title>'
            '<style>body{font:16px system-ui;margin:32px}table{border-collapse:collapse}'
            'td,th{border:1px solid #ddd;padding:8px}th{background:#eee}</style>'
            '<h1>单平台实验结果</h1><p>费用、订阅剩余量与主力节省比例：未测。'
            '此处通过率只针对本批固定样本。</p><table><tr>'
            '<th>样本</th><th>轮次</th><th>HTTP</th><th>通过</th><th>毫秒</th>'
            '<th>输入token</th><th>输出token</th><th>错误</th></tr>' + body + '</table>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--limit', type=int, default=1)
    ap.add_argument('--repeat', type=int, default=1)
    args = ap.parse_args()
    if not 1 <= args.limit <= 10:
        ap.error('--limit must be 1..10')
    all_cases = json.loads(args.cases.read_text())
    validate_cases(all_cases, 1)
    cases = all_cases[:args.limit]
    validate_cases(cases, args.repeat)
    cfg = json.loads((Path.home() / 'llm-hub/providers.json').read_text())['providers']['sfkey-glm']
    endpoint = cfg['api_base_url'].rstrip('/')
    url = urllib.parse.urlsplit(endpoint)
    if (url.scheme != 'https' or url.hostname != 'api.sfkey.cn'
            or url.path != '/v1/chat/completions' or url.query or url.username):
        raise SystemExit('configured endpoint changed; verify before running')
    model = cfg['models'][0]
    key_result = subprocess.run(
        ['security', 'find-generic-password', '-s', 'llm-hub', '-a', 'sfkey-glm', '-w'],
        capture_output=True, text=True, timeout=10)
    if key_result.returncode or not key_result.stdout.strip():
        raise SystemExit('credential unavailable')
    key = key_result.stdout.strip()
    args.out.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    rows = []
    try:
        for repeat in range(1, args.repeat + 1):
            for case in cases:
                row = dict(case_id=case['id'], repeat=repeat, provider='sfkey-glm',
                           model_requested=model, http_status=None, passed=False,
                           tokens_in=None, tokens_out=None, cost_usd=None, error=None)
                payload = dict(model=model, max_tokens=512, stream=False,
                               messages=[dict(role='user', content=PROMPT + case['input'])])
                request = urllib.request.Request(
                    endpoint, data=json.dumps(payload).encode(), method='POST',
                    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                started = time.monotonic()
                try:
                    with opener.open(request, timeout=30) as response:
                        row['http_status'] = response.status
                        data = json.loads(response.read(1_000_000))
                    choice = data['choices'][0]
                    output = choice['message'].get('content') or ''
                    if not isinstance(output, str):
                        raise ValueError('non-text response')
                    output = output.replace(key, '[REDACTED]')
                    usage = data.get('usage') or {}
                    row.update(output=output, model_reported=str(data.get('model')).replace(key, '[REDACTED]'),
                               finish_reason=choice.get('finish_reason') if choice.get('finish_reason') in {'stop', 'length', 'content_filter', 'tool_calls'} else 'unknown',
                               passed=choice.get('finish_reason') == 'stop' and verdict(output, case['expected']))
                    for source, target in [('prompt_tokens', 'tokens_in'), ('completion_tokens', 'tokens_out')]:
                        value = usage.get(source)
                        row[target] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                    if not row['passed']:
                        row['error'] = 'verification_failed'
                except urllib.error.HTTPError as exc:
                    row.update(http_status=exc.code, error='http_' + str(exc.code))
                except KeyboardInterrupt:
                    row['error'] = 'interrupted'
                except Exception as exc:
                    row['error'] = type(exc).__name__
                row['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                rows.append(row)
                (args.out / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                if row['error'] and row['error'] != 'verification_failed':
                    return 1
    finally:
        (args.out / 'report.html').write_text(render(rows))
    print(json.dumps({'attempts': len(rows), 'passed': sum(r['passed'] for r in rows),
                      'results': str(args.out / 'results.json'), 'report': str(args.out / 'report.html')}))
    return 0 if all(r['passed'] for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
```

### 实现后的验证

- [ ] 在仓库根目录运行 `/opt/anaconda3/bin/python3 -m pytest tests/test_tokenmaxx_pilot.py -q`，预期 8 passed。
- [ ] 将 `render` 的结果保存为临时 HTML 并打开，确认中文、表头、失败状态可见，错误文本没有被当成 HTML 执行。
- [ ] 确认上面的 mock 测试覆盖 401、429、超时只调用一次，标准答案不外发，以及凭证回显不会进入产物；这些测试不发真实网络请求。

## Task 4：真实连接与批量实验

**Precondition:** 在本地现有凭证管理流程中更新有效的 sfkey-glm 凭证；确认该模型适用的订阅权益。无需把 key 放到聊天、源码或命令历史。

- [ ] 使用完整 endpoint 做 1 条试跑；若返回 401/403，停在身份问题；404 核实 endpoint/model；429/容量错误停止，不更换平台。

```bash
/opt/anaconda3/bin/python3 scripts/tokenmaxx_pilot.py --cases tests/fixtures/tokenmaxx-notes.json --out ~/taskrouter/experiments/glm-connection-01 --limit 1
```

- [ ] 连接成功后跑首轮 10 条；验收 9/10 或以上才进入稳定性实验。

```bash
/opt/anaconda3/bin/python3 scripts/tokenmaxx_pilot.py --cases tests/fixtures/tokenmaxx-notes.json --out ~/taskrouter/experiments/glm-first-10 --limit 10
```

- [ ] 首轮达标后再跑 10 条 × 3 次。30 次中至少 27 次通过。脚本退出码 1 表示有失败，仍需查看报告；9/10 和 27/30 是产品门槛，不要求脚本把部分失败伪装成全成功。

```bash
/opt/anaconda3/bin/python3 scripts/tokenmaxx_pilot.py --cases tests/fixtures/tokenmaxx-notes.json --out ~/taskrouter/experiments/glm-repeat-30 --limit 10 --repeat 3
```

- [ ] 在 Codex 中用相同 10 条 input 和同一说明做主力直接执行对照，保存同格式输出和可获得的任务级 usage。若宿主无任务级 usage，明确标记未测，不拿账号整体额度变化冒充因果证据。
- [ ] 对照首次通过率、返回上下文大小、耗时、外部 usage/费用和主力实际消耗；计入派发、校验、失败后返工。保存结论为实验目录的 `assessment.md`。
- [ ] 只有连接、质量、稳定性、收益门槛都满足，才进入现有 Task Router 工作台接入；否则报告失败类型和下一项最小改动。

## 后续工作台接入的边界

下一个实施计划限定为：新增单一 OpenAI-compatible adapter、不可变输入快照、单并发 runner、验收与 attempt 记账；扩展现有 taskctl；将 dashboard.html 改为可读任务/平台/实验三块信息的单页面。涉及文件为 `taskrouter/adapters/openai_api.py`、`core/loops.py`、`core/service.py`、`core/registry.py`、`core/router.py`、`api/tasks.py`、`scripts/taskctl.py`、`dashboard.html`。

启用 runner 前添加显式的实验任务选择条件，在隔离 TASKROUTER_HOME 中验证，避免旧队列自动开始远端请求。以新增适配器完成验证后的 ready 状态作为路由硬条件；暂停、取消、超时、中断和结果未知必须在状态机中有对应事件。

## 本次计划自检记录

2026-09-11：在仓库外的临时目录提取并编译本文两个 Python 代码块；10 条 JSON 样本解析成功，ID 均不重复；离线 mock 测试结果为 **8 passed**。没有把这些脚本安装到产品目录，没有执行批量模型请求。HTML 已有转义行为测试，实际页面目视检查列入实施步骤。

在线证据仅为本轮一次 sfkey-glm 连接检查：正确 endpoint 返回 HTTP 401 Invalid token，尚无可用模型输出、平台费用、剩余额度或节省比例数据。
