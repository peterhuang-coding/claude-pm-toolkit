"""Provider/capability API (M2).

- Local registry: GET /api/providers, /api/capabilities (from our SQLite).
- llm-hub read-only proxy: GET /api/llmhub/health, /api/llmhub/budget — the
  server makes the call (httpx trust_env=False so localhost bypasses the
  machine proxy); responses only ever contain key_present booleans, never keys.
- POST /api/providers/sync-health: pull the latest llm-hub snapshot into the
  local providers table.

AC10: no endpoint in this module can return a secret — the registry stores
Keychain references only, and llm-hub never exposes key material.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import db
from ..core import llmhub, registry

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
def list_providers() -> dict[str, Any]:
    conn = db.connect()
    try:
        providers = registry.list_providers(conn)
        return {"providers": providers, "count": len(providers)}
    finally:
        conn.close()


@router.get("/capabilities")
def list_capabilities() -> dict[str, Any]:
    conn = db.connect()
    try:
        caps = registry.list_capabilities(conn)
        return {"capabilities": caps, "count": len(caps)}
    finally:
        conn.close()


@router.post("/providers/sync-health")
async def sync_health() -> dict[str, Any]:
    conn = db.connect()
    try:
        summary = await registry.sync_health(conn)
        return {"synced": True, **summary}
    finally:
        conn.close()


@router.get("/llmhub/health")
async def llmhub_health() -> dict[str, Any]:
    """Read-only proxy for llm-hub /api/health (no secrets in payload)."""
    health = await llmhub.fetch_health()
    if health is None:
        raise HTTPException(status_code=502, detail="llm-hub unreachable")
    return health


@router.get("/llmhub/budget")
async def llmhub_budget() -> dict[str, Any]:
    """Read-only proxy for llm-hub /api/budget (payg providers only)."""
    budget = await llmhub.fetch_budget()
    if budget is None:
        raise HTTPException(status_code=502, detail="llm-hub unreachable")
    return budget
