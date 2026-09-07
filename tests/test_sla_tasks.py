"""AC1: tasks are created from goal + SLA template only — no agent/model choice."""
import pytest

from taskrouter.core import service

TEMPLATES = ["instant-approx", "instant-reliable", "async-economy", "async-rigorous"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_create_task_for_each_template(conn, template):
    task = service.create_task(conn, goal=f"demo goal for {template}", sla_template=template)
    assert task["status"] == "queued"
    assert task["sla_template"] == template
    assert task["id"].startswith("t_")
    # Contract carries the SLA defaults, not an agent/model selection.
    c = task["contract"]
    for field in (
        "deadline_seconds", "max_wait_seconds", "correctness_target",
        "max_cost_usd", "allow_paid", "risk_level", "delivery_mode",
        "degradation_policy", "weights",
    ):
        assert field in c, f"{field} missing from contract ({template})"
    assert "agent" not in c and "model" not in c

    events = service.get_events(conn, task["id"])
    assert [e["type"] for e in events] == ["created", "queued"]
    assert [e["seq"] for e in events] == [1, 2]


def test_unknown_template_rejected(conn):
    with pytest.raises(ValueError):
        service.create_task(conn, goal="x", sla_template="does-not-exist")


def test_advanced_overrides_merge(conn):
    task = service.create_task(
        conn,
        goal="cheap but a bit bigger budget",
        sla_template="async-rigorous",
        advanced={"max_cost_usd": 5.0, "allow_paid": False},
    )
    assert task["contract"]["max_cost_usd"] == 5.0
    assert task["contract"]["allow_paid"] is False
    # Non-overridden template value survives.
    assert task["contract"]["correctness_target"] == "verified"


def test_unknown_advanced_field_rejected(conn):
    with pytest.raises(ValueError):
        service.create_task(conn, goal="x", advanced={"favorite_model": "gpt-x"})


def test_empty_goal_rejected(conn):
    with pytest.raises(ValueError):
        service.create_task(conn, goal="   ")


def test_list_and_detail(conn):
    t1 = service.create_task(conn, goal="first", sla_template="instant-approx")
    t2 = service.create_task(conn, goal="second", sla_template="async-economy")
    listed = service.list_tasks(conn)
    assert {t["id"] for t in listed} == {t1["id"], t2["id"]}

    detail = service.get_task_detail(conn, t1["id"])
    assert detail["goal"] == "first"
    assert len(detail["events"]) == 2
    assert detail["attempts"] == []

    assert service.get_task_detail(conn, "t_nonexistent") is None


def test_api_smoke_create_list_show():
    """End-to-end through FastAPI TestClient against the temp TASKROUTER_HOME."""
    from fastapi.testclient import TestClient
    from taskrouter.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        r = client.get("/api/sla-templates")
        assert set(r.json()["templates"]) == set(TEMPLATES)

        r = client.post("/api/tasks", json={"goal": "api smoke", "sla_template": "instant-reliable"})
        assert r.status_code == 200, r.text
        tid = r.json()["id"]

        r = client.post("/api/tasks", json={"goal": "bad", "sla_template": "nope"})
        assert r.status_code == 400

        r = client.get(f"/api/tasks/{tid}")
        assert r.status_code == 200
        assert r.json()["goal"] == "api smoke"

        r = client.get("/api/tasks")
        assert r.json()["count"] >= 1
