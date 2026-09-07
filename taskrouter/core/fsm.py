"""Task finite state machine.

Table-driven transitions (PRD 状态机):

    draft -> queued -> preparing-context -> routing -> running -> verifying -> completed

Anomaly / human branches:
    queued|routing      -> waiting-capacity
    running             -> retrying
    running|verifying   -> needs-decision
    running             -> paused | cancelled
    retrying            -> routing | failed
    verifying           -> routing   (verification failed: re-route / upgrade)
    recovery-only edge: verifying -> queued  (process died mid-verification;
        re-schedule from scratch; context packs are versioned/reusable)

Every transition is one SQLite transaction: a compare-and-set on tasks.status
plus an events row with a per-task increasing seq. Illegal transitions raise
InvalidTransition and roll back.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

STATES = {
    "draft",
    "queued",
    "preparing-context",
    "routing",
    "running",
    "verifying",
    "completed",
    "waiting-capacity",
    "retrying",
    "needs-decision",
    "paused",
    "cancelled",
    "failed",
}

TERMINAL_STATES = {"completed", "cancelled", "failed"}

#: Allowed transitions. Recovery edges (restart reaper) are commented.
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"queued", "cancelled"},
    "queued": {
        "preparing-context",
        "waiting-capacity",
        "paused",
        "cancelled",
    },
    "preparing-context": {"routing", "paused", "cancelled", "failed"},
    "routing": {
        "running",
        "waiting-capacity",
        "needs-decision",
        "paused",
        "cancelled",
        "failed",
    },
    "running": {
        "verifying",
        "retrying",
        "needs-decision",
        "paused",
        "cancelled",
        "failed",
    },
    "verifying": {
        "completed",
        "routing",            # verification failed -> re-route / upgrade capability
        "needs-decision",
        "queued",             # RECOVERY ONLY: restart while verifying -> reschedule
        "failed",
    },
    "waiting-capacity": {"queued", "routing", "cancelled", "failed"},
    "retrying": {"routing", "failed", "cancelled", "paused"},
    "needs-decision": {"running", "routing", "queued", "paused", "cancelled", "failed"},
    "paused": {"running", "queued", "cancelled"},
    "completed": set(),
    "cancelled": set(),
    "failed": set(),
}


class InvalidTransition(Exception):
    """Raised when a state transition is not allowed by the FSM."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


def _next_seq(conn: sqlite3.Connection, task_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return int(row["next"])


def add_event(
    conn: sqlite3.Connection,
    task_id: str,
    event_type: str,
    message: str = "",
    data: Optional[dict[str, Any]] = None,
    attempt_id: Optional[str] = None,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
) -> int:
    """Append an event row. Must be called inside an open transaction."""
    seq = _next_seq(conn, task_id)
    conn.execute(
        """
        INSERT INTO events
            (task_id, attempt_id, seq, type, from_status, to_status, message, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            attempt_id,
            seq,
            event_type,
            from_status,
            to_status,
            message,
            json.dumps(data, ensure_ascii=False) if data is not None else None,
            utcnow(),
        ),
    )
    return seq


def transition(
    conn: sqlite3.Connection,
    task_id: str,
    to_status: str,
    event_type: str = "transition",
    message: str = "",
    data: Optional[dict[str, Any]] = None,
    attempt_id: Optional[str] = None,
) -> int:
    """CAS transition + event in one transaction.

    Opens a transaction if the caller has not. Returns the new event seq.
    Raises InvalidTransition; the row is never partially updated.
    """
    if to_status not in STATES:
        raise InvalidTransition(f"unknown target state: {to_status!r}")
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN")
    try:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise InvalidTransition(f"task not found: {task_id}")
        from_status = row["status"]
        if not can_transition(from_status, to_status):
            raise InvalidTransition(
                f"task {task_id}: illegal transition {from_status!r} -> {to_status!r}"
            )
        now = utcnow()
        cur = conn.execute(
            "UPDATE tasks SET status = ?, status_at = ?, updated_at = ? "
            "WHERE id = ? AND status = ?",
            (to_status, now, now, task_id, from_status),
        )
        if cur.rowcount != 1:
            # Concurrent mover changed status first; fail the transition.
            raise InvalidTransition(
                f"task {task_id}: status changed concurrently (expected {from_status!r})"
            )
        seq = add_event(
            conn,
            task_id,
            event_type=event_type,
            message=message,
            data=data,
            attempt_id=attempt_id,
            from_status=from_status,
            to_status=to_status,
        )
        if own_tx:
            conn.execute("COMMIT")
        return seq
    except Exception:
        if own_tx:
            conn.execute("ROLLBACK")
        raise
