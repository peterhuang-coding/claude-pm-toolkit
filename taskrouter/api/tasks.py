"""Task API: create (goal + SLA only, AC1), list, detail with event timeline."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
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
