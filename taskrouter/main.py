"""Task Router FastAPI entrypoint.

Run:  /opt/anaconda3/bin/python3 -m uvicorn taskrouter.main:app --host 127.0.0.1 --port 3459

Startup: create schema/migrate -> restart reaping (AC8) -> start asyncio loops.
M1 loops: scheduler walks queued tasks to routing and parks them; runner and
watchdog are placeholders for M3/M4.
"""
from __future__ import annotations

import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from . import config, db
from .api.tasks import router as tasks_router
from .core import recovery
from .core.loops import runner_loop, scheduler_loop, watchdog_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("taskrouter")

_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    conn = db.connect()
    try:
        db.init_db(conn)
        recovered = recovery.reap_stale(conn)
    finally:
        conn.close()
    if recovered:
        log.warning("restart recovery reaped %d in-flight task(s): %s",
                    len(recovered), recovered)
    else:
        log.info("startup: no in-flight tasks to recover")
    _background_tasks.add(asyncio.create_task(scheduler_loop(), name="scheduler"))
    _background_tasks.add(asyncio.create_task(runner_loop(), name="runner"))
    _background_tasks.add(asyncio.create_task(watchdog_loop(), name="watchdog"))
    log.info("taskrouter started on %s:%s (data dir: %s)",
             config.HOST, config.PORT, config.home())
    try:
        yield
    finally:
        for t in _background_tasks:
            t.cancel()


app = FastAPI(title="taskrouter", version="0.1.0", lifespan=lifespan)
app.include_router(tasks_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def dashboard() -> FileResponse:
    return FileResponse(config.dashboard_path())
