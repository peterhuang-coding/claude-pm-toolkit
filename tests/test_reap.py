"""AC8: restart reaping of in-flight tasks and attempts."""
from taskrouter.core import fsm, recovery, service


def _add_running_attempt(conn, task_id):
    aid = "a_" + task_id[2:]
    conn.execute(
        "INSERT INTO attempts (id, task_id, seq, status, started_at, heartbeat_at) "
        "VALUES (?, ?, 1, 'running', ?, ?)",
        (aid, task_id, fsm.utcnow(), fsm.utcnow()),
    )
    return aid


def test_reap_running_task_goes_retrying(conn):
    task = service.create_task(conn, goal="mid-flight when killed", sla_template="async-rigorous")
    tid = task["id"]
    for nxt in ("preparing-context", "routing", "running"):
        fsm.transition(conn, tid, nxt)
    aid = _add_running_attempt(conn, tid)

    # Simulate process restart: fresh connection, run the reaper.
    recovered = recovery.reap_stale(conn)

    assert [(r["task_id"], r["to"]) for r in recovered] == [(tid, "retrying")]
    assert service.get_task(conn, tid)["status"] == "retrying"

    attempt = conn.execute("SELECT * FROM attempts WHERE id = ?", (aid,)).fetchone()
    assert attempt["status"] == "interrupted"
    assert attempt["stop_reason"] == "process_restart"
    assert attempt["ended_at"] is not None

    events = service.get_events(conn, tid)
    assert events[-1]["type"] == "reaped"
    assert events[-1]["from_status"] == "running"
    assert events[-1]["to_status"] == "retrying"
    assert any(e["type"] == "attempt-interrupted" for e in events)
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))


def test_reap_verifying_task_goes_queued(conn):
    task = service.create_task(conn, goal="verifying when killed", sla_template="instant-reliable")
    tid = task["id"]
    for nxt in ("preparing-context", "routing", "running", "verifying"):
        fsm.transition(conn, tid, nxt)
    _add_running_attempt(conn, tid)

    recovered = recovery.reap_stale(conn)
    assert [(r["task_id"], r["to"]) for r in recovered] == [(tid, "queued")]
    assert service.get_task(conn, tid)["status"] == "queued"
    last = service.get_events(conn, tid)[-1]
    assert last["type"] == "reaped" and last["to_status"] == "queued"


def test_reap_is_idempotent(conn):
    task = service.create_task(conn, goal="zombie", sla_template="instant-approx")
    tid = task["id"]
    for nxt in ("preparing-context", "routing", "running"):
        fsm.transition(conn, tid, nxt)
    _add_running_attempt(conn, tid)

    assert len(recovery.reap_stale(conn)) == 1
    # Second run finds nothing in-flight.
    assert recovery.reap_stale(conn) == []
    assert service.get_task(conn, tid)["status"] == "retrying"


def test_reap_touches_only_inflight(conn):
    queued = service.create_task(conn, goal="waiting", sla_template="instant-approx")
    done = service.create_task(conn, goal="finished", sla_template="instant-approx")
    for nxt in ("preparing-context", "routing", "running", "verifying", "completed"):
        fsm.transition(conn, done["id"], nxt)

    assert recovery.reap_stale(conn) == []
    assert service.get_task(conn, queued["id"])["status"] == "queued"
    assert service.get_task(conn, done["id"])["status"] == "completed"
