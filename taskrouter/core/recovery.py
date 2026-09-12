"""Restart recovery (AC8).

On startup the process may have been killed (even kill -9) while tasks were
in-flight. SQLite transactions guarantee status/attempt rows are consistent,
but a 'running' task has no live executor anymore. Reaper rules:

  * tasks in 'running'  -> 'retrying'   (open attempts marked 'interrupted')
  * tasks in 'verifying' -> 'queued'     (verification died with the process;
                                          reschedule; context packs are reusable)

Every recovery action writes an event so the timeline shows exactly what
happened. Idempotent: running it twice changes nothing.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import fsm


def reap_stale(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Recover in-flight tasks/attempts left by a dead process.

    Returns a list of recovery records (for logs/tests).
    """
    recovered: list[dict[str, Any]] = []
    conn.execute("BEGIN")
    try:
        stale_tasks = conn.execute(
            "SELECT id, status FROM tasks WHERE status IN ('running', 'verifying') "
            "AND COALESCE(json_extract(contract,'$.strategy'),'') != 'subscription-worker'"
        ).fetchall()
        for row in stale_tasks:
            task_id, status = row["id"], row["status"]
            # Mark every still-open attempt of this task as interrupted.
            open_attempts = conn.execute(
                "SELECT id FROM attempts WHERE task_id = ? AND status = 'running'",
                (task_id,),
            ).fetchall()
            for a in open_attempts:
                conn.execute(
                    "UPDATE attempts SET status = 'interrupted', "
                    "stop_reason = 'process_restart', ended_at = ? "
                    "WHERE id = ? AND status = 'running'",
                    (fsm.utcnow(), a["id"]),
                )
                fsm.add_event(
                    conn,
                    task_id,
                    event_type="attempt-interrupted",
                    message=f"attempt {a['id']} interrupted by process restart",
                    attempt_id=a["id"],
                )
            if status == "running":
                seq = fsm.transition(
                    conn,
                    task_id,
                    "retrying",
                    event_type="reaped",
                    message="process restarted while task was running; "
                    "attempt marked interrupted, task queued for retry",
                    data={"reason": "process_restart", "from": "running"},
                )
                recovered.append({"task_id": task_id, "to": "retrying", "event_seq": seq})
            else:  # verifying
                seq = fsm.transition(
                    conn,
                    task_id,
                    "queued",
                    event_type="reaped",
                    message="process restarted during verification; "
                    "task rescheduled for re-route/re-verify",
                    data={"reason": "process_restart", "from": "verifying"},
                )
                recovered.append({"task_id": task_id, "to": "queued", "event_seq": seq})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return recovered
