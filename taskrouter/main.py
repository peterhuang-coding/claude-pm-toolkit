"""Task Router FastAPI entrypoint.

Run:  /opt/anaconda3/bin/python3 -m uvicorn taskrouter.main:app --host 127.0.0.1 --port 3459

Startup: create schema/migrate -> restart reaping (AC8) -> seed registry ->
llm-hub health sync -> start asyncio loops.
M2 loops: scheduler walks queued tasks to routing, runs the rule router and
parks routed tasks with wait_reason='awaiting-adapter'; runner and watchdog
are placeholders for M3/M4.
"""
from __future__ import annotations

import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from . import config, db
from .api.providers import router as providers_router
from .api.tasks import router as tasks_router
from .api.subagents import router as subagents_router
from .core import delegation
from .core import recovery, registry
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
        delegation.recover(conn)
        # Returned outputs remain awaiting review; the legacy reaper must not replay them.
        recovered = recovery.reap_stale(conn)
        # M2: seed capability/provider registry (idempotent, no secrets).
        seeded = registry.seed_registry(conn)
    finally:
        conn.close()
    if recovered:
        log.warning("restart recovery reaped %d in-flight task(s): %s",
                    len(recovered), recovered)
    else:
        log.info("startup: no in-flight tasks to recover")
    log.info("registry seeded: %d providers, %d capabilities",
             seeded["providers"], seeded["capabilities"])
    # Initial llm-hub health sync (read-only; failure is non-fatal).
    try:
        conn = db.connect()
        try:
            summary = await registry.sync_health(conn)
        finally:
            conn.close()
        log.info("provider health sync: %s", summary)
    except Exception:
        log.exception("initial provider health sync failed; router will retry in loop")
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
        await asyncio.gather(*_background_tasks, return_exceptions=True)
        _background_tasks.clear()


app = FastAPI(title="taskrouter", version="0.2.0", lifespan=lifespan)
app.include_router(tasks_router)
app.include_router(providers_router)
app.include_router(subagents_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def dashboard() -> FileResponse:
    return FileResponse(config.dashboard_path())
