"""Background asyncio loops.

M2 scheduler walks queued tasks through preparing-context to routing, then
invokes the rule router (core.router): a route_decisions row is written with
full explainability (candidates, exclusion reasons, scores, fallback chain)
and the task parks at 'routing' with wait_reason='awaiting-adapter' — adapters
arrive in M3, so nothing executes yet. Provider health is synced from llm-hub
(read-only) once per tick (HTTP responses are cached for ~20s).

runner and watchdog remain placeholders for M3/M4.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from .. import db
from . import fsm, registry, router

log = logging.getLogger("taskrouter.loops")

SCHEDULER_INTERVAL_SEC = 2.0
RUNNER_INTERVAL_SEC = 5.0
WATCHDOG_INTERVAL_SEC = 10.0
#: Attempt heartbeat must be at least this old before the watchdog acts (M4).
HEARTBEAT_TIMEOUT_SEC = 300


def _has_decision(conn: sqlite3.Connection, task_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM route_decisions WHERE task_id = ? LIMIT 1", (task_id,)
    ).fetchone() is not None


def _route_if_needed(conn: sqlite3.Connection, task_id: str) -> None:
    """Run the router once for a task sitting at 'routing' without a decision."""
    if _has_decision(conn, task_id):
        return
    try:
        decision = router.route_task(conn, task_id)
        router.mark_awaiting_adapter(conn, task_id, decision)
        log.info(
            "task %s routed -> %s (%s); fallback depth %d",
            task_id,
            decision["chosen_capability"],
            decision["chosen_reason"],
            len(decision["fallback_chain"]),
        )
    except Exception:
        log.exception("routing failed for task %s", task_id)


def _advance_one(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    """One scheduler step per task. Each FSM transition is its own transaction."""
    try:
        if status == "queued":
            fsm.transition(
                conn,
                task_id,
                "preparing-context",
                event_type="phase",
                message="preparing context (M2 stub: immutable context packs land in M3)",
            )
            status = "preparing-context"
        if status == "preparing-context":
            fsm.transition(
                conn,
                task_id,
                "routing",
                event_type="phase",
                message="context prepared (M2 stub); entering rule router",
            )
            status = "routing"
        if status == "retrying":
            fsm.transition(
                conn,
                task_id,
                "routing",
                event_type="phase",
                message="retrying: re-entering routing after restart recovery",
            )
            status = "routing"
        if status == "routing":
            _route_if_needed(conn, task_id)
    except fsm.InvalidTransition:
        # Another loop iteration or the API moved it; skip this round.
        log.debug("scheduler skip for %s", task_id)


async def scheduler_loop() -> None:
    """Advance tasks to routing, run the rule router, park awaiting adapters."""
    while True:
        try:
            conn = db.connect()
            try:
                # Read-only llm-hub health sync (cached ~20s inside llmhub).
                try:
                    await registry.sync_health(conn)
                except Exception:
                    log.exception("provider health sync failed; router uses last snapshot")
                rows = conn.execute(
                    "SELECT status, id FROM tasks "
                    "WHERE status IN ('queued', 'preparing-context', 'retrying', 'routing') "
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
