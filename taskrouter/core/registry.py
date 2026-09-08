"""Capability/Provider registry: seed upsert + health sync (M2).

Seed data comes from the git-tracked registry_seed.json and contains NO secrets
— only Keychain references (service/account names) and env var names. At
startup (and on demand) seed rows are upserted idempotently; llm-hub provider
health snapshots are fetched read-only and merged into providers.health.

Providers registered in llm-hub get source='llm_hub'; anything llm-hub does not
know about stays 'standalone' with local probing. M2 probes local credential
presence by Keychain exit code ONLY (never prints the secret); local
capabilities (provider 'local') are always key_present=True.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

from .. import config
from . import fsm, llmhub

log = logging.getLogger("taskrouter.registry")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_seed() -> dict[str, Any]:
    with open(config.registry_seed_path(), "r", encoding="utf-8") as f:
        return json.load(f)


# --- key presence (exit code only; the secret is never printed) --------------

def keychain_key_present(service: str, account: str) -> Optional[bool]:
    """Return True/False if Keychain can be queried; None if the tool is
    unavailable. Uses exit code only — stdout/stderr are discarded."""
    if not shutil.which("security"):
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return None


# --- seed upsert -------------------------------------------------------------

def seed_registry(conn: sqlite3.Connection) -> dict[str, int]:
    """Idempotently upsert seed providers/capabilities. Returns counts."""
    seed = load_seed()
    now = utcnow()
    n_prov = n_cap = 0
    conn.execute("BEGIN")
    try:
        for p in seed["providers"]:
            exists = conn.execute(
                "SELECT 1 FROM providers WHERE id = ?", (p["id"],)
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE providers SET name=?, source=?, tier=?, ptype=?, base_url=?,
                        keychain_service=?, keychain_account=?, env_var=?,
                        credential_ref=?, pricing=?
                    WHERE id=?
                    """,
                    (
                        p["name"], p.get("source", "standalone"), p.get("tier"),
                        p.get("ptype"), p.get("base_url"),
                        p.get("keychain_service"), p.get("keychain_account"),
                        p.get("env_var"),
                        _credential_ref(p),
                        json.dumps(p.get("pricing", {}), ensure_ascii=False),
                        p["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO providers
                        (id, name, source, tier, ptype, base_url,
                         keychain_service, keychain_account, env_var, credential_ref,
                         pricing, quota, health, health_status, key_present, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?, '{}', '{}', 'unknown', NULL, ?)
                    """,
                    (
                        p["id"], p["name"], p.get("source", "standalone"),
                        p.get("tier"), p.get("ptype"), p.get("base_url"),
                        p.get("keychain_service"), p.get("keychain_account"),
                        p.get("env_var"), _credential_ref(p),
                        json.dumps(p.get("pricing", {}), ensure_ascii=False),
                        now,
                    ),
                )
            n_prov += 1

        for c in seed["capabilities"]:
            cfg = dict(c.get("config", {}))
            cfg["adapter_ready"] = bool(c.get("adapter_ready", False))
            cfg["tags"] = c.get("tags", [])
            cfg["est_latency_ms"] = c.get("est_latency_ms")
            cfg["quality"] = c.get("quality")
            exists = conn.execute(
                "SELECT 1 FROM capabilities WHERE id = ?", (c["id"],)
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE capabilities SET name=?, kind=?, adapter=?, task_types=?,
                        supports=?, provider_id=?, models=?, config=?, enabled=1
                    WHERE id=?
                    """,
                    (
                        c["name"], c["kind"], c["adapter"],
                        json.dumps(c.get("task_types", []), ensure_ascii=False),
                        json.dumps(c.get("supports", {}), ensure_ascii=False),
                        c.get("provider_id"),
                        json.dumps(c.get("models", []), ensure_ascii=False),
                        json.dumps(cfg, ensure_ascii=False),
                        c["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO capabilities
                        (id, name, kind, adapter, task_types, supports, provider_id,
                         models, config, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,1,?)
                    """,
                    (
                        c["id"], c["name"], c["kind"], c["adapter"],
                        json.dumps(c.get("task_types", []), ensure_ascii=False),
                        json.dumps(c.get("supports", {}), ensure_ascii=False),
                        c.get("provider_id"),
                        json.dumps(c.get("models", []), ensure_ascii=False),
                        json.dumps(cfg, ensure_ascii=False),
                        now,
                    ),
                )
            n_cap += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log.info("registry seed: %d providers, %d capabilities upserted", n_prov, n_cap)
    return {"providers": n_prov, "capabilities": n_cap}


def _credential_ref(p: dict[str, Any]) -> Optional[str]:
    """Human-readable non-secret pointer, e.g. 'keychain:llm-hub/deepseek'."""
    if p.get("keychain_service") and p.get("keychain_account"):
        return f"keychain:{p['keychain_service']}/{p['keychain_account']}"
    if p.get("env_var"):
        return f"env:{p['env_var']}"
    return None


# --- health sync -------------------------------------------------------------

def _health_status(h: dict[str, Any]) -> str:
    """Derive a summary status from an llm-hub health entry."""
    now_ts = datetime.now(timezone.utc).timestamp()
    if not h.get("enabled", False):
        return "disabled"
    if (h.get("quota_exhausted_until") or 0) > now_ts:
        return "exhausted"
    if (h.get("circuit_open_until") or 0) > now_ts:
        return "cooldown"
    if h.get("key_present") is False:
        return "no_key"
    if (h.get("recent_error_rate") or 0) >= 0.5:
        return "degraded"
    return "healthy"


async def sync_health(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fetch llm-hub health/budget (read-only) and merge into provider rows.

    llm_hub providers get the full snapshot + key_present from llm-hub; the
    standalone 'local' provider is always healthy/key_present. Other standalone
    providers are probed via Keychain exit code.
    """
    snap = await llmhub.fetch_snapshot()
    health = (snap.get("health") or {}).get("providers", {}) if snap.get("health") else {}
    budget = snap.get("budget") or {}
    now = utcnow()
    summary: dict[str, Any] = {"llmhub_online": snap.get("health") is not None, "providers": {}}

    conn.execute("BEGIN")
    try:
        rows = conn.execute("SELECT id, source, keychain_service, keychain_account FROM providers").fetchall()
        for row in rows:
            pid = row["id"]
            if row["source"] == "llm_hub":
                h = health.get(pid)
                if h is None:
                    # llm-hub offline or provider removed there: keep last snapshot, mark unknown.
                    conn.execute(
                        "UPDATE providers SET health_status='unknown', last_synced_at=? WHERE id=?",
                        (now, pid),
                    )
                    summary["providers"][pid] = "unknown"
                    continue
                status = _health_status(h)
                conn.execute(
                    "UPDATE providers SET health=?, health_status=?, key_present=?, "
                    "quota=?, tier=COALESCE(tier, ?), last_synced_at=? WHERE id=?",
                    (
                        json.dumps(h, ensure_ascii=False),
                        status,
                        1 if h.get("key_present") else 0,
                        json.dumps(_quota_view(pid, h, budget), ensure_ascii=False),
                        h.get("tier"),
                        now,
                        pid,
                    ),
                )
                summary["providers"][pid] = status
            elif pid == "local":
                conn.execute(
                    "UPDATE providers SET health=?, health_status='healthy', "
                    "key_present=1, last_synced_at=? WHERE id=?",
                    (json.dumps({"kind": "local", "available": True}), now, pid),
                )
                summary["providers"][pid] = "healthy"
            else:
                present = None
                if row["keychain_service"] and row["keychain_account"]:
                    present = keychain_key_present(row["keychain_service"], row["keychain_account"])
                conn.execute(
                    "UPDATE providers SET health_status=?, key_present=?, last_synced_at=? WHERE id=?",
                    (
                        "unknown" if present is None else ("healthy" if present else "no_key"),
                        None if present is None else (1 if present else 0),
                        now,
                        pid,
                    ),
                )
                summary["providers"][pid] = "unknown" if present is None else ("healthy" if present else "no_key")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return summary


def _quota_view(pid: str, h: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    """Non-secret quota/budget view for the providers.quota JSON column."""
    b = budget.get(pid, {})
    return {
        "daily_budget_usd": h.get("daily_budget_usd"),
        "daily_spend_usd": h.get("daily_spend_usd", 0.0),
        "remaining_usd": b.get("remaining_usd"),
        "quota_exhausted_until": h.get("quota_exhausted_until", 0),
        "rpm": None,
        "tpm": None,
    }


# --- read helpers ------------------------------------------------------------

def list_providers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM providers ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("pricing", "quota", "health"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k] = {}
        out.append(d)
    return out


def list_capabilities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM capabilities ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("task_types", "supports", "models", "config"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out
