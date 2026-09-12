"""Task API: create (goal + SLA only, AC1), list, detail with event timeline."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..core import router as router_mod
from ..core import service

router = APIRouter(prefix="/api", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="One-sentence task goal")
    sla_template: str = Field(
        default="instant-approx", description="SLA template name; user never picks an agent"
    )
    output_type: str = "text"
    advanced: Optional[dict[str, Any]] = Field(
        default=None, description="Optional Task Contract overrides"
    )
    priority: int = 0
    parent_id: Optional[str] = None


class RerouteRequest(BaseModel):
    force: Optional[list[str]] = Field(
        default=None, description="Capability/provider ids to force (human override)"
    )
    ban: Optional[list[str]] = Field(
        default=None, description="Capability/provider ids to ban for this routing pass"
    )


@router.get("/health")
def health() -> dict[str, Any]:
    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        v = db.schema_version(conn)
    finally:
        conn.close()
    return {"status": "ok", "schema_version": v, "tasks_total": n}


@router.get("/sla-templates")
def sla_templates() -> dict[str, Any]:
    return service.load_sla_templates()


@router.post("/tasks")
def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    conn = db.connect()
    try:
        try:
            task = service.create_task(
                conn,
                goal=req.goal,
                sla_template=req.sla_template,
                output_type=req.output_type,
                advanced=req.advanced,
                parent_id=req.parent_id,
                priority=req.priority,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return task
    finally:
        conn.close()


@router.get("/tasks")
def list_tasks(status: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
    conn = db.connect()
    try:
        tasks = service.list_tasks(conn, status=status, limit=min(limit, 500))
        return {"tasks": tasks, "count": len(tasks)}
    finally:
        conn.close()


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    conn = db.connect()
    try:
        detail = service.get_task_detail(conn, task_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        return detail
    finally:
        conn.close()


@router.get("/tasks/{task_id}/route-decision")
def get_route_decision(task_id: str) -> dict[str, Any]:
    """Latest route decision (candidates, exclusions, scores, fallback chain)."""
    conn = db.connect()
    try:
        if service.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        decision = router_mod.get_latest_decision(conn, task_id)
        if decision is None:
            raise HTTPException(
                status_code=409, detail="task has not been routed yet"
            )
        return decision
    finally:
        conn.close()


@router.post("/tasks/{task_id}/reroute")
def reroute_task(task_id: str, req: RerouteRequest) -> dict[str, Any]:
    """Manual re-route with operator force/ban (M2: only while at 'routing').

    A fresh route_decisions row is written; the task stays parked at routing
    with wait_reason='awaiting-adapter' until adapters land in M3.
    """
    conn = db.connect()
    try:
        task = service.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        if task['contract'].get('strategy') == 'subscription-worker':
            raise HTTPException(409, detail='Subscription tasks use /api/subagents; no API-provider rerouting')
        if task["status"] != "routing":
            raise HTTPException(
                status_code=409,
                detail=f"reroute is only valid at 'routing', task is {task['status']!r}",
            )
        decision = router_mod.route_task(conn, task_id, force=req.force, ban=req.ban)
        router_mod.mark_awaiting_adapter(conn, task_id, decision)
        return decision
    finally:
        conn.close()
