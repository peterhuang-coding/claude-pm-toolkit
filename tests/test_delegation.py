import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from taskrouter.main import app
from taskrouter.core import delegation

HEADERS = {'X-TaskRouter-Local': '1'}


def test_login_does_not_unlock_and_requests_fail_closed(conn, monkeypatch):
    monkeypatch.setattr(delegation.workbuddy, 'available', lambda: True)
    client = TestClient(app, base_url='http://127.0.0.1')
    accounts = client.get('/api/accounts').json()['accounts']
    assert len([p for p in accounts if p['login_url']]) == 10
    r = client.post('/api/accounts/gemini/login', json={'logged_in': True}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()['login_reported'] is True and r.json()['routable'] is False
    # A database migrated from M1 retains the old non-JSON health default.
    conn.execute("UPDATE providers SET health='unknown' WHERE id='worker:gemini'")
    assert client.get('/api/accounts').status_code == 200
    request = {'goal': 'classify', 'input_text': 'synthetic input'}
    assert client.post('/api/subagents', json={**request, 'local_only':True}, headers=HEADERS).status_code == 422
    assert client.post('/api/subagents', json=request).status_code == 403
    assert client.post('/api/subagents', json=request, headers={**HEADERS, 'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/api/subagents', json=request, headers=HEADERS).status_code == 409
    for field, value in [('complexity', 'complex'), ('urgency', 'urgent'), ('data_sensitivity', 'confidential')]:
        response = client.post('/api/subagents', json={**request, field:value}, headers=HEADERS)
        assert response.status_code == 409


def test_execution_review_failure_and_no_repeat(conn, monkeypatch):
    monkeypatch.setattr(delegation.workbuddy, 'available', lambda: True)
    calls = []
    async def execute(prompt, timeout):
        calls.append(prompt)
        return '[{"id":"A"}]', {'elapsed_seconds':0.1, 'models':[], 'usage':{}}
    monkeypatch.setattr(delegation.workbuddy, 'run', execute)
    delegation.set_probe(conn, True)
    req = delegation.SubagentRequest(goal='classify', input_text='A', output_format='json', expected_count=1)
    task = delegation.submit(conn, req)
    asyncio.run(delegation.run_pending_once())
    detail = delegation.detail(conn, task['id'])
    assert detail['status'] == 'verifying'
    assert detail['result']['output'] == [{'id':'A'}]
    assert len(calls) == 1
    asyncio.run(delegation.run_pending_once())
    assert len(calls) == 1
    delegation.review(conn, task['id'], True, 'checked source')
    assert delegation.detail(conn, task['id'])['status'] == 'completed'
    failed = delegation.submit(conn, delegation.SubagentRequest(goal='classify', input_text='A', output_format='json', expected_count=2))
    asyncio.run(delegation.run_pending_once())
    assert delegation.detail(conn, failed['id'])['status'] == 'failed'
    assert len(calls) == 2


def test_cancel_and_restart_never_replay(conn, monkeypatch):
    monkeypatch.setattr(delegation.workbuddy, 'available', lambda: True)
    delegation.set_probe(conn, True)
    req = delegation.SubagentRequest(goal='draft', input_text='sample')
    task = delegation.submit(conn, req)
    delegation.cancel(conn, task['id'])
    assert delegation.detail(conn, task['id'])['status'] == 'cancelled'
    task = delegation.submit(conn, req)
    delegation.recover(conn)
    assert delegation.detail(conn, task['id'])['status'] == 'failed'


def test_running_cancel_cleans_child_and_keeps_worker_usable(conn, monkeypatch):
    monkeypatch.setattr(delegation.workbuddy, 'available', lambda: True)
    delegation.set_probe(conn, True)
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def slow(prompt, timeout):
            entered.set()
            try: await asyncio.Event().wait()
            finally: cleaned.set()
        monkeypatch.setattr(delegation.workbuddy, 'run', slow)
        task = delegation.submit(conn, delegation.SubagentRequest(goal='draft', input_text='sample'))
        runner = asyncio.create_task(delegation.run_pending_once())
        await entered.wait()
        delegation.cancel(conn, task['id'])
        await runner
        assert cleaned.is_set()
        assert not delegation.active
        assert delegation.detail(conn, task['id'])['status'] == 'cancelled'
    asyncio.run(scenario())


def test_adapter_timeout_kills_process_group(monkeypatch, tmp_path):
    import os
    import sys
    from taskrouter.adapters import workbuddy
    marker = tmp_path / 'pid'
    source = 'import os,time;from pathlib import Path;Path(' + repr(str(marker)) + ').write_text(str(os.getpid()));time.sleep(30)'
    monkeypatch.setattr(workbuddy, 'command', lambda:[sys.executable, '-c', source])
    with pytest.raises(asyncio.TimeoutError): asyncio.run(workbuddy.run('probe', 0.5))
    assert marker.exists()
    with pytest.raises(ProcessLookupError): os.kill(int(marker.read_text()), 0)


def test_failed_input_storage_never_exposes_queued_job(conn, monkeypatch):
    import sqlite3
    monkeypatch.setattr(delegation.workbuddy, 'available', lambda: True)
    delegation.set_probe(conn, True)
    conn.execute("CREATE TEMP TRIGGER reject_pack BEFORE INSERT ON context_packs BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        delegation.submit(conn, delegation.SubagentRequest(goal='draft', input_text='sample'))
    assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
