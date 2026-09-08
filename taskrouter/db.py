"""SQLite persistence layer.

WAL mode, foreign keys on, one connection per call (short-lived; the service is
single-process with asyncio loops that do all DB work synchronously in small
transactions). Schema versioning uses PRAGMA user_version; bump SCHEMA_VERSION
and add a migration in _MIGRATIONS when the schema changes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config

SCHEMA_VERSION = 2

# --- DDL ---------------------------------------------------------------------
# All core objects from the PRD: Task, Context Pack, Harness, Attempt, Event,
# Artifact, Evaluation, Decision, Capability, Provider, Route Decision,
# Quota Ledger. Secrets NEVER live here — providers store credential_ref only.

_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id              TEXT PRIMARY KEY,
        parent_id       TEXT REFERENCES tasks(id),
        goal            TEXT NOT NULL,
        output_type     TEXT NOT NULL DEFAULT 'text',
        sla_template    TEXT NOT NULL,
        contract        TEXT NOT NULL,            -- JSON: full Task Contract
        status          TEXT NOT NULL DEFAULT 'draft',
        wait_reason     TEXT,                     -- e.g. 'awaiting-adapter' (M2 park marker)
        priority        INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        status_at       TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS context_packs (
        id          TEXT PRIMARY KEY,
        task_id     TEXT NOT NULL REFERENCES tasks(id),
        version     INTEGER NOT NULL,
        content     TEXT NOT NULL,                -- JSON snapshot
        local_only  INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        UNIQUE(task_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS harness_registry (
        id          TEXT PRIMARY KEY,            -- name@version
        name        TEXT NOT NULL,
        version     TEXT NOT NULL,
        task_type   TEXT NOT NULL,
        spec        TEXT NOT NULL,                -- JSON: tools/prompts/perms/timeouts/verifier
        enabled     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL,
        UNIQUE(name, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attempts (
        id               TEXT PRIMARY KEY,
        task_id          TEXT NOT NULL REFERENCES tasks(id),
        seq              INTEGER NOT NULL,
        harness_id       TEXT REFERENCES harness_registry(id),
        context_pack_id  TEXT REFERENCES context_packs(id),
        capability_id    TEXT,
        provider_id      TEXT,
        status           TEXT NOT NULL DEFAULT 'running',
                       -- running | succeeded | failed | timed_out | interrupted | cancelled
        pid              INTEGER,
        heartbeat_at     TEXT,
        started_at       TEXT NOT NULL,
        ended_at         TEXT,
        tokens_in        INTEGER,
        tokens_out       INTEGER,
        cost_usd         REAL,
        error            TEXT,
        stop_reason      TEXT,
        UNIQUE(task_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id     TEXT NOT NULL REFERENCES tasks(id),
        attempt_id  TEXT REFERENCES attempts(id),
        seq         INTEGER NOT NULL,             -- per-task increasing
        type        TEXT NOT NULL,
        from_status TEXT,
        to_status   TEXT,
        message     TEXT,
        data        TEXT,                         -- JSON, never contains secrets
        created_at  TEXT NOT NULL,
        UNIQUE(task_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id          TEXT PRIMARY KEY,
        task_id     TEXT NOT NULL REFERENCES tasks(id),
        attempt_id  TEXT REFERENCES attempts(id),
        kind        TEXT NOT NULL,                -- answer|file|commit|screenshot|report|log
        ref         TEXT,                         -- path / url / commit sha
        content_hash TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluations (
        id          TEXT PRIMARY KEY,
        task_id     TEXT NOT NULL REFERENCES tasks(id),
        attempt_id  TEXT REFERENCES attempts(id),
        verdict     TEXT NOT NULL,                -- pass | fail | pending
        method      TEXT,                         -- schema|rule|command|sources|model_review
        detail      TEXT,                         -- JSON
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id           TEXT PRIMARY KEY,
        task_id      TEXT NOT NULL REFERENCES tasks(id),
        kind         TEXT NOT NULL,               -- approve|degrade|upgrade|stop|publish|side_effect
        status       TEXT NOT NULL DEFAULT 'pending',  -- pending | resolved | dismissed
        request      TEXT NOT NULL,               -- JSON: what human is asked to decide
        resolution   TEXT,                        -- JSON: human answer
        decided_by   TEXT,
        idempotency_key TEXT,
        created_at   TEXT NOT NULL,
        resolved_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capabilities (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        kind        TEXT NOT NULL,                -- model_api|cli_agent|local_tool|search|crawler|computer_use|mcp
        adapter     TEXT NOT NULL,                -- adapter module key ('none' = registered, adapter lands in M3)
        task_types  TEXT NOT NULL DEFAULT '[]',   -- JSON: task/output types this capability can serve
        supports    TEXT NOT NULL DEFAULT '{}',   -- JSON: local_only/risk_levels/side_effects/deterministic/context_window
        provider_id TEXT REFERENCES providers(id),
        models      TEXT NOT NULL DEFAULT '[]',   -- JSON model ids (model_api capabilities)
        config      TEXT NOT NULL DEFAULT '{}',   -- JSON, non-secret only
        enabled     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS providers (
        id             TEXT PRIMARY KEY,
        name           TEXT NOT NULL UNIQUE,
        source         TEXT NOT NULL DEFAULT 'standalone', -- llm_hub | standalone
        tier           TEXT,                      -- plan | payg (synced from llm-hub when source=llm_hub)
        ptype          TEXT,                      -- openai | anthropic | ... (llm-hub 'type')
        base_url       TEXT,
        keychain_service TEXT,                    -- Keychain service reference ONLY, never the secret
        keychain_account TEXT,                    -- Keychain account reference ONLY, never the secret
        env_var        TEXT,                      -- env var name reference ONLY, never the secret
        credential_ref TEXT,                      -- human-readable credential pointer (non-secret)
        pricing        TEXT NOT NULL DEFAULT '{}',-- JSON per-model price
        quota          TEXT NOT NULL DEFAULT '{}',-- JSON: limits/balance/reset_at/rpm/tpm
        health         TEXT NOT NULL DEFAULT '{}',-- JSON health snapshot from llm-hub/local probe
        health_status  TEXT NOT NULL DEFAULT 'unknown', -- unknown|healthy|degraded|cooldown|exhausted
        key_present    INTEGER,                   -- bool snapshot; NULL = never probed
        last_synced_at TEXT,
        created_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS route_decisions (
        id              TEXT PRIMARY KEY,
        task_id         TEXT NOT NULL REFERENCES tasks(id),
        attempt_id      TEXT REFERENCES attempts(id),
        candidates      TEXT NOT NULL DEFAULT '[]',   -- JSON candidate capabilities (pass+fail, with reasons/scores)
        exclusions      TEXT NOT NULL DEFAULT '{}',   -- JSON capability -> exclude reason
        scores          TEXT NOT NULL DEFAULT '{}',   -- JSON capability -> score breakdown
        chosen_capability TEXT,
        chosen_provider   TEXT,
        fallback_chain  TEXT NOT NULL DEFAULT '[]',   -- JSON ordered [{capability,provider}]
        rules_version   TEXT,                         -- router_rules.json version used
        forced          TEXT NOT NULL DEFAULT '{}',   -- JSON {force:[...], ban:[...]} applied
        chosen_reason   TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quota_ledger (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id TEXT REFERENCES providers(id),
        task_id     TEXT REFERENCES tasks(id),
        attempt_id  TEXT REFERENCES attempts(id),
        kind        TEXT NOT NULL,                -- reserve|consume|release|reset
        amount      REAL NOT NULL,
        unit        TEXT NOT NULL DEFAULT 'usd',
        balance_after REAL,
        created_at  TEXT NOT NULL
    )
    """,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status)",
    "CREATE INDEX IF NOT EXISTS idx_quota_provider ON quota_ledger(provider_id)",
    "CREATE INDEX IF NOT EXISTS idx_route_decisions_task ON route_decisions(task_id, created_at)",
]

# Migrations keyed by the version they upgrade FROM. v0 -> v1 is full DDL above.
_MIGRATIONS = {
    # v1 -> v2 (M2): provider/capability/route-decision/task columns for the
    # registry + rule router.
    1: (
        "ALTER TABLE tasks ADD COLUMN wait_reason TEXT",
        "ALTER TABLE providers ADD COLUMN source TEXT NOT NULL DEFAULT 'standalone'",
        "ALTER TABLE providers ADD COLUMN tier TEXT",
        "ALTER TABLE providers ADD COLUMN ptype TEXT",
        "ALTER TABLE providers ADD COLUMN keychain_service TEXT",
        "ALTER TABLE providers ADD COLUMN keychain_account TEXT",
        "ALTER TABLE providers ADD COLUMN env_var TEXT",
        # NOTE: v1 already has a `health` column (string); M2 reuses it to store
        # the JSON health snapshot and adds health_status for the summary.
        "ALTER TABLE providers ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE providers ADD COLUMN key_present INTEGER",
        "ALTER TABLE providers ADD COLUMN last_synced_at TEXT",
        "ALTER TABLE capabilities ADD COLUMN task_types TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE capabilities ADD COLUMN supports TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE capabilities ADD COLUMN provider_id TEXT REFERENCES providers(id)",
        "ALTER TABLE capabilities ADD COLUMN models TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE route_decisions ADD COLUMN rules_version TEXT",
        "ALTER TABLE route_decisions ADD COLUMN forced TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE route_decisions ADD COLUMN chosen_reason TEXT",
        "CREATE INDEX IF NOT EXISTS idx_route_decisions_task ON route_decisions(task_id, created_at)",
    ),
}


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection with WAL + foreign keys. Caller owns the transaction."""
    path = Path(db_path) if db_path else config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    """Create tables and run migrations. Safe on every startup."""
    own = conn is None
    conn = conn or connect()
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"taskrouter.db schema v{current} is newer than code v{SCHEMA_VERSION}; "
                "upgrade the code before starting"
            )
        conn.execute("BEGIN")
        for ddl in _TABLES:
            conn.execute(ddl)
        for idx in _INDEXES:
            conn.execute(idx)
        if current == 0:
            # Brand-new database: the DDL above is already the latest schema.
            current = SCHEMA_VERSION
        while current < SCHEMA_VERSION:
            for stmt in _MIGRATIONS.get(current, ()):
                conn.execute(stmt)
            current += 1
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        if own:
            conn.close()
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]
