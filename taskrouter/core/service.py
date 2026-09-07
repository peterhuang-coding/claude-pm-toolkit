"""Task service layer: create/list/detail. Pure DB logic, no HTTP.

Task creation intentionally takes only a goal + SLA template (or advanced
contract overrides). The user never picks an agent/model — that is the router's
job in M2 (AC1).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Optional

from .. import config
from . import fsm

_CONTRACT_FIELDS = (
    "deadline_seconds",
    "max_wait_seconds",
    "correctness_target",
    "max_cost_usd",
    "allow_paid",
    "risk_level",
    "side_effect_limit",
    "data_sensitivity",
    "local_only",
    "delivery_mode",
    "verification_budget",
    "degradation_policy",
    "weights",
    "expected_capabilities",
    "strategy",
)
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no ambiguous 0/o/1/l


def new_task_id() -> str:
    return "t_" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def load_sla_templates() -> dict[str, Any]:
    with open(config.sla_templates_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def get_template(name: str) -> dict[str, Any]:
    templates = load_sla_templates()["templates"]
    if name not in templates:
        raise ValueError(
            f"unknown SLA template {name!r}; available: {', '.join(sorted(templates))}"
        )
    return templates[name]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "contract" in d and isinstance(d["contract"], str):
        d["contract"] = json.loads(d["contract"])
    return d


def create_task(
    conn: sqlite3.Connection,
    goal: str,
    sla_template: str = "instant-approx",
    output_type: str = "text",
    advanced: Optional[dict[str, Any]] = None,
    parent_id: Optional[str] = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Insert a draft task and move it to queued. Returns the created task."""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("goal must not be empty")
    tpl = get_template(sla_template)
    contract: dict[str, Any] = {k: tpl[k] for k in _CONTRACT_FIELDS if k in tpl}
    if advanced:
        unknown = set(advanced) - set(_CONTRACT_FIELDS)
        if unknown:
            raise ValueError(f"unknown advanced contract fields: {sorted(unknown)}")
        contract.update(advanced)

    task_id = new_task_id()
    now = fsm.utcnow()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO tasks
                (id, parent_id, goal, output_type, sla_template, contract,
                 status, priority, created_at, updated_at, status_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                task_id,
                parent_id,
                goal,
                output_type,
                sla_template,
                json.dumps(contract, ensure_ascii=False),
                priority,
                now,
                now,
                now,
            ),
        )
        fsm.add_event(
            conn,
            task_id,
            event_type="created",
            message=f"task created from SLA template '{sla_template}'",
            data={"sla_template": sla_template, "output_type": output_type},
        )
        fsm.transition(
            conn,
            task_id,
            "queued",
            event_type="queued",
            message="task accepted into queue; no executor chosen by user",
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_task(conn, task_id)


def get_task(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tasks(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_events(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT seq, type, from_status, to_status, message, data, attempt_id, created_at "
        "FROM events WHERE task_id = ? ORDER BY seq",
        (task_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("data"):
            d["data"] = json.loads(d["data"])
        out.append(d)
    return out


def get_attempts(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM attempts WHERE task_id = ? ORDER BY seq", (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_task_detail(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    task = get_task(conn, task_id)
    if task is None:
        return None
    task["events"] = get_events(conn, task_id)
    task["attempts"] = get_attempts(conn, task_id)
    return task
