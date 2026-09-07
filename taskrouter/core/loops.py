"""Background asyncio loops.

M1: only the scheduler does real (if minimal) work — it walks queued tasks
through preparing-context to routing and parks them there, because no adapters
exist yet (M2/M3). runner and watchdog are placeholders filled in M3/M4.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from .. import db
from . import fsm

log = logging.getLogger("taskrouter.loops")

SCHEDULER_INTERVAL_SEC = 2.0
RUNNER_INTERVAL_SEC = 5.0
WATCHDOG_INTERVAL_SEC = 10.0
#: Attempt heartbeat must be at least this old before the watchdog acts (M4).
HEARTBEAT_TIMEOUT_SEC = 300

_PARKED_MESSAGE = (
    "parked at routing: no capability/adapter registered yet "
    "(router + adapters arrive in M2/M3)"
)


def _parked_event(conn: sqlite3.Connection, task_id: str) -> None:
    """Record a 'parked' event once per task (idempotent)."""
    already = conn.execute(
        "SELECT 1 FROM events WHERE task_id = ? AND type = 'parked' LIMIT 1",
        (task_id,),
    ).fetchone()
    if already:
        return
    conn.execute("BEGIN")
    try:
        fsm.add_event(
            conn,
            task_id,
            event_type="parked",
            message=_PARKED_MESSAGE,
            data={"waiting_for": "M2-router/M3-adapters"},
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _advance_one(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    """One scheduler step per task. Each FSM transition is its own transaction."""
    try:
        if status == "queued":
            fsm.transition(
                conn,
                task_id,
                "preparing-context",
                event_type="phase",
                message="preparing context (M1 stub: immutable context packs land in M3)",
            )
            status = "preparing-context"
        if status == "preparing-context":
            fsm.transition(
                conn,
                task_id,
                "routing",
                event_type="phase",
                message="context prepared (M1 stub)",
            )
            status = "routing"
        if status == "routing":
            _parked_event(conn, task_id)
        if status == "retrying":
            fsm.transition(
                conn,
                task_id,
                "routing",
                event_type="phase",
                message="retrying: re-entering routing after restart recovery",
            )
            _parked_event(conn, task_id)
    except fsm.InvalidTransition:
        # Another loop iteration or the API moved it; skip this round.
        log.debug("scheduler skip for %s", task_id)


async def scheduler_loop() -> None:
    """Advance queued/retrying tasks up to 'routing', then park (no adapters in M1)."""
    while True:
        try:
            conn = db.connect()
            try:
                rows = conn.execute(
                    "SELECT status, id FROM tasks "
                    "WHERE status IN ('queued', 'preparing-context', 'retrying') "
                    "ORDER BY priority DESC, created_at ASC"
                ).fetchall()
                for row in rows:
                    _advance_one(conn, row["id"], row["status"])
            finally:
                conn.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler loop error")
        await asyncio.sleep(SCHEDULER_INTERVAL_SEC)


async def runner_loop() -> None:
    """PLACEHOLDER (M3): pick routed tasks, create attempts, drive adapters."""
    while True:
        await asyncio.sleep(RUNNER_INTERVAL_SEC)


async def watchdog_loop() -> None:
    """PLACEHOLDER (M4): heartbeat timeout -> retry/waiting-capacity; quota alerts."""
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
