"""Read-only llm-hub client (M2 integration).

Task Router never writes to llm-hub: it only GETs /api/health and /api/budget
to filter and score providers. All calls use httpx with trust_env=False so the
machine's HTTP(S)_PROXY env vars cannot intercept localhost traffic.

No secrets are ever requested or returned: /api/health exposes key_present
booleans, not keys.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from .. import config

log = logging.getLogger("taskrouter.llmhub")

#: Cache the health snapshot briefly; llm-hub itself refreshes every ~30s.
_CACHE_TTL_SEC = 20.0
_cache: dict[str, Any] = {"ts": 0.0, "health": None, "budget": None}


def _client() -> httpx.AsyncClient:
    # trust_env=False: ignore HTTP_PROXY/HTTPS_PROXY/NO_PROXY from the environment.
    return httpx.AsyncClient(base_url=config.LLMHUB_BASE_URL, trust_env=False,
                             timeout=config.LLMHUB_TIMEOUT_SEC)


async def fetch_health() -> Optional[dict[str, Any]]:
    """GET /api/health. Returns None when llm-hub is unreachable (router then
    treats llm_hub providers as availability=unknown, never as silently healthy)."""
    try:
        async with _client() as c:
            r = await c.get("/api/health")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("llm-hub /api/health unreachable: %s", e)
        return None


async def fetch_budget() -> Optional[dict[str, Any]]:
    """GET /api/budget (payg providers only). None on failure."""
    try:
        async with _client() as c:
            r = await c.get("/api/budget")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("llm-hub /api/budget unreachable: %s", e)
        return None


async def fetch_snapshot(force: bool = False) -> dict[str, Any]:
    """Cached combined snapshot: {ts, health: {...}|None, budget: {...}|None}."""
    now = time.time()
    if not force and _cache["health"] is not None and now - _cache["ts"] < _CACHE_TTL_SEC:
        return _cache
    health = await fetch_health()
    budget = await fetch_budget()
    snap = {"ts": now, "health": health, "budget": budget}
    if health is not None:
        _cache.update(snap)
    return snap


def reset_cache() -> None:
    _cache.update({"ts": 0.0, "health": None, "budget": None})
