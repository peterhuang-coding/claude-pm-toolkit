"""Test fixtures: isolate runtime data in a temp TASKROUTER_HOME.

Must set the env var BEFORE taskrouter.config is imported (paths resolve at
import time for package config; runtime dirs are read per-call).
"""
import os
import tempfile

_TMP_HOME = tempfile.mkdtemp(prefix="taskrouter-test-")
os.environ["TASKROUTER_HOME"] = _TMP_HOME
os.environ.setdefault("TASKROUTER_PORT", "3459")

import pytest  # noqa: E402

from taskrouter import db  # noqa: E402


@pytest.fixture()
def conn():
    c = db.connect()
    db.init_db(c)
    # Start each test with an empty, migrated database. Children first
    # (capabilities reference providers; events/attempts reference tasks).
    for table in (
        "quota_ledger", "route_decisions", "capabilities", "providers",
        "decisions", "evaluations", "artifacts", "events", "attempts",
        "harness_registry", "context_packs", "tasks",
    ):
        c.execute(f"DELETE FROM {table}")
    c.execute("PRAGMA user_version")
    yield c
    c.close()
