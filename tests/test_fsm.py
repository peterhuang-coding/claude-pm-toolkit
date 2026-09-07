"""FSM: legal transitions succeed, illegal ones raise, event seq is gapless."""
import pytest

from taskrouter.core import fsm, service


def _make_task(conn, sla="instant-approx"):
    return service.create_task(conn, goal="fsm probe", sla_template=sla)["id"]


def test_happy_path_walk(conn):
    tid = _make_task(conn)  # ends in queued
    assert service.get_task(conn, tid)["status"] == "queued"
    for nxt in ("preparing-context", "routing", "running", "verifying", "completed"):
        fsm.transition(conn, tid, nxt, event_type="phase")
    assert service.get_task(conn, tid)["status"] == "completed"

    seqs = [e["seq"] for e in service.get_events(conn, tid)]
    assert seqs == list(range(1, len(seqs) + 1))


def test_illegal_transitions_rejected(conn):
    tid = _make_task(conn)  # queued
    for bad in ("running", "verifying", "completed", "draft", "failed"):
        with pytest.raises(fsm.InvalidTransition):
            fsm.transition(conn, tid, bad)

    # Terminal states cannot move.
    tid2 = _make_task(conn)
    for nxt in ("preparing-context", "routing", "running", "verifying", "completed"):
        fsm.transition(conn, tid2, nxt)
    for bad in ("running", "queued", "routing"):
        with pytest.raises(fsm.InvalidTransition):
            fsm.transition(conn, tid2, bad)


def test_retry_and_failure_branch(conn):
    tid = _make_task(conn)
    for nxt in ("preparing-context", "routing", "running", "retrying", "routing",
                "running", "verifying", "completed"):
        fsm.transition(conn, tid, nxt)
    assert service.get_task(conn, tid)["status"] == "completed"

    tid2 = _make_task(conn)
    for nxt in ("preparing-context", "routing", "running", "retrying", "failed"):
        fsm.transition(conn, tid2, nxt)
    assert service.get_task(conn, tid2)["status"] == "failed"
    with pytest.raises(fsm.InvalidTransition):
        fsm.transition(conn, tid2, "routing")


def test_needs_decision_branch(conn):
    tid = _make_task(conn)
    for nxt in ("preparing-context", "routing", "running", "needs-decision", "running",
                "verifying", "completed"):
        fsm.transition(conn, tid, nxt)
    assert service.get_task(conn, tid)["status"] == "completed"


def test_event_seq_per_task_independent(conn):
    t1 = _make_task(conn)
    t2 = _make_task(conn)
    fsm.transition(conn, t1, "preparing-context")
    fsm.transition(conn, t2, "preparing-context")
    fsm.transition(conn, t1, "routing")
    s1 = [e["seq"] for e in service.get_events(conn, t1)]
    s2 = [e["seq"] for e in service.get_events(conn, t2)]
    assert s1 == [1, 2, 3, 4]
    assert s2 == [1, 2, 3]


def test_unknown_task_transition_raises(conn):
    with pytest.raises(fsm.InvalidTransition):
        fsm.transition(conn, "t_nope", "queued")
