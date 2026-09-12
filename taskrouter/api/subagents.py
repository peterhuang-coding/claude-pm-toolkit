import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..core import delegation as d
from ..core import fsm

router = APIRouter(prefix='/api', tags=['subagents'])


def local_write(request: Request):
    if request.headers.get('x-taskrouter-local') != '1':
        raise HTTPException(403, 'Local client header required')
    origin = request.headers.get('origin')
    if origin and (origin != str(request.base_url).rstrip('/') or urlsplit(origin).hostname not in {'localhost', '127.0.0.1'}):
        raise HTTPException(403, 'Cross-origin writes are disabled')


class LoginReport(BaseModel):
    logged_in: bool


class Review(BaseModel):
    passed: bool
    note: str = Field(default='', max_length=1000)


@router.get('/accounts')
def accounts():
    with_connection = db.connect()
    try: return {'accounts':d.accounts(with_connection)}
    finally: with_connection.close()


@router.post('/accounts/{pid}/login', dependencies=[Depends(local_write)])
def report_login(pid: str, req: LoginReport):
    if pid not in {p[0] for p in d.CATALOG}: raise HTTPException(404, 'Unknown platform')
    conn = db.connect()
    try: return d.update_account(conn, pid, login_reported=req.logged_in, login_reported_at=fsm.utcnow())
    finally: conn.close()


@router.post('/accounts/{pid}/probe', dependencies=[Depends(local_write)])
async def probe(pid: str):
    if pid != 'workbuddy': raise HTTPException(409, '此平台的自动调用入口尚未接入')
    if d.active or d.probe_lock.locked(): raise HTTPException(409, 'WorkBuddy 正在执行，请稍后测试')
    async with d.probe_lock:
        conn = db.connect()
        try:
            text, meta = await d.workbuddy.run('Reply ONLY with TASKROUTER_OK', 60)
            if text.strip() != 'TASKROUTER_OK': raise ValueError('Probe reply did not match')
            return d.set_probe(conn, True, meta)
        except (ValueError, OSError, asyncio.TimeoutError):
            d.set_probe(conn, False)
            raise HTTPException(502, 'WorkBuddy 测试未通过，请检查客户端登录与额度；未切换收费 API')
        finally: conn.close()


@router.post('/subagents', status_code=202, dependencies=[Depends(local_write)])
def submit(req: d.SubagentRequest):
    conn = db.connect()
    try:
        try: return d.submit(conn, req)
        except ValueError as e: raise HTTPException(409, str(e))
    finally: conn.close()


@router.get('/subagents')
def list_jobs():
    conn = db.connect()
    try:
        rows = conn.execute("SELECT id,goal,status,created_at FROM tasks WHERE json_extract(contract,'$.strategy')=? ORDER BY created_at DESC,id DESC LIMIT 50", (d.STRATEGY,)).fetchall()
        return {'tasks':[dict(r) for r in rows]}
    finally: conn.close()


@router.get('/subagents/{tid}')
def get_job(tid: str):
    conn = db.connect()
    try:
        result = d.detail(conn, tid)
        if result is None: raise HTTPException(404, 'Unknown subagent task')
        return result
    finally: conn.close()


@router.post('/subagents/{tid}/review', dependencies=[Depends(local_write)])
def review(tid: str, req: Review):
    conn = db.connect()
    try:
        try: d.review(conn, tid, req.passed, req.note)
        except ValueError as e: raise HTTPException(409, str(e))
        return d.detail(conn, tid)
    finally: conn.close()


@router.post('/subagents/{tid}/cancel', dependencies=[Depends(local_write)])
async def cancel(tid: str):
    conn = db.connect()
    try:
        try: d.cancel(conn, tid)
        except ValueError as e: raise HTTPException(409, str(e))
        return d.detail(conn, tid)
    finally: conn.close()
