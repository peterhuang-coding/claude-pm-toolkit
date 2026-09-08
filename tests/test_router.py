"""M2 rule router tests: hard filters, soft scoring, fallback, force/ban,
route_decisions persistence, and zero-secret guarantees (AC2/AC10).

Health is set explicitly per test (no dependency on live llm-hub).
"""
from __future__ import annotations

import json
import os
import re

import pytest

from taskrouter.core import registry, router, service

TEMPLATES = ["instant-approx", "instant-reliable", "async-economy", "async-rigorous"]

FAKE_KEY = "sk-fake-ZEROSECRET0000ABCD1234"
#: Secrets must never match this pattern anywhere in rows/responses/logs.
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{16,}")


def _seed(conn) -> None:
    registry.seed_registry(conn)


def _set_provider_health(conn, pid: str, status: str = "healthy", **overrides) -> None:
    h = {
        "enabled": True, "key_present": True, "recent_error_rate": 0.0,
        "recent_p50_ms": None, "recent_p95_ms": None,
        "quota_exhausted_until": 0, "circuit_open_until": 0,
        "daily_budget_usd": None, "daily_spend_usd": 0.0,
    }
    h.update(overrides)
    budget = h.get("daily_budget_usd")
    quota = {
        "daily_budget_usd": budget,
        "daily_spend_usd": h.get("daily_spend_usd", 0.0),
        "remaining_usd": (budget - h.get("daily_spend_usd", 0.0)) if budget is not None else None,
        "quota_exhausted_until": h.get("quota_exhausted_until", 0),
    }
    conn.execute(
        "UPDATE providers SET health=?, health_status=?, key_present=?, quota=? WHERE id=?",
        (json.dumps(h), status, 1 if h["key_present"] else 0, json.dumps(quota), pid),
    )


def _all_healthy(conn) -> None:
    _set_provider_health(conn, "deepseek", "healthy", recent_p50_ms=900)
    _set_provider_health(conn, "openrouter", "healthy", recent_p50_ms=2200)
    _set_provider_health(conn, "local", "healthy")


def _route(conn, goal="demo", template="instant-approx", **advanced):
    task = service.create_task(
        conn, goal=goal, sla_template=template,
        output_type=advanced.pop("output_type", "text"),
        advanced=advanced or None,
    )
    # Walk to routing like the scheduler would (context phase is an M2 stub).
    from taskrouter.core import fsm
    fsm.transition(conn, task["id"], "preparing-context", event_type="phase")
    fsm.transition(conn, task["id"], "routing", event_type="phase")
    decision = router.route_task(conn, task["id"])
    router.mark_awaiting_adapter(conn, task["id"], decision)
    return task, decision


def _cap(decision, cap_id):
    return next(c for c in decision["candidates"] if c["capability_id"] == cap_id)


# --- registry seed -----------------------------------------------------------

def test_seed_registry_idempotent_and_referenc_only(conn):
    n1 = registry.seed_registry(conn)
    n2 = registry.seed_registry(conn)
    assert n1 == n2 == {"providers": 3, "capabilities": 4}
    caps = registry.list_capabilities(conn)
    assert {c["id"] for c in caps} == {
        "llm-deepseek", "llm-openrouter", "claude-code", "url-fetch"
    }
    # llm-hub providers registered, openrouter present (source syncs from seed).
    provs = {p["id"]: p for p in registry.list_providers(conn)}
    assert provs["deepseek"]["source"] == "llm_hub"
    assert provs["openrouter"]["source"] == "llm_hub"
    assert provs["deepseek"]["keychain_account"] == "deepseek"
    # No secret material, only references.
    blob = json.dumps(registry.list_providers(conn) + caps, ensure_ascii=False)
    assert not SECRET_PATTERN.search(blob)
    for p in provs.values():
        assert "api_key" not in json.dumps(p)


# --- four SLA templates produce explainable decisions (AC2) -------------------

@pytest.mark.parametrize("template", TEMPLATES)
def test_route_four_templates_explainable(conn, template):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, goal=f"demo for {template}", template=template)

    assert len(d["candidates"]) == 4
    assert d["rules_version"].startswith("router_rules@v")
    assert d["forced"] == {"force": [], "ban": []}
    for c in d["candidates"]:
        assert set(c) >= {
            "capability_id", "capability_name", "kind", "provider_id",
            "hard_pass", "excluded_reasons", "scores", "est",
            "availability", "adapter_ready", "deterministic",
        }
        assert set(c["scores"]) == {
            "cost", "latency", "quality", "quota_headroom", "health", "total"
        }
        assert 0.0 <= c["scores"]["total"] <= 1.0
    assert d["chosen_capability"] is not None
    assert d["chosen_reason"] in {
        "highest_soft_score", "deterministic_tool_preferred", "forced_by_operator"
    }
    # Chosen heads the fallback chain; chain is score-ordered.
    assert d["fallback_chain"][0]["capability_id"] == d["chosen_capability"]
    totals = [f["total"] for f in d["fallback_chain"]]
    assert totals == sorted(totals, reverse=True)
    # Every hard-pass candidate is in the chain; excluded ones are not.
    chain_ids = {f["capability_id"] for f in d["fallback_chain"]}
    for c in d["candidates"]:
        assert c["hard_pass"] == (c["capability_id"] in chain_ids)


def test_decision_persisted_and_served_via_detail(conn):
    _seed(conn)
    _all_healthy(conn)
    task, d = _route(conn, template="instant-reliable")
    again = router.get_latest_decision(conn, task["id"])
    assert again["id"] == d["id"]
    detail = service.get_task_detail(conn, task["id"])
    assert detail["route_decision"]["id"] == d["id"]
    assert detail["wait_reason"] == router.AWAITING_ADAPTER
    event_types = [e["type"] for e in detail["events"]]
    assert "routed" in event_types
    routed = next(e for e in detail["events"] if e["type"] == "routed")
    assert routed["data"]["decision_id"] == d["id"]
    assert routed["data"]["wait_reason"] == "awaiting-adapter"


# --- hard filters ------------------------------------------------------------

def test_local_only_excludes_cloud(conn):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, template="instant-reliable", local_only=True)
    ds = _cap(d, "llm-deepseek")
    or_ = _cap(d, "llm-openrouter")
    assert ds["hard_pass"] is False
    assert "local_only_excludes_cloud" in ds["excluded_reasons"]
    assert "local_only_excludes_cloud" in or_["excluded_reasons"]
    # Local capabilities survive and the chosen one is not cloud.
    assert d["chosen_capability"] in {"claude-code", "url-fetch"}
    chain_providers = {f["provider_id"] for f in d["fallback_chain"]}
    assert "deepseek" not in chain_providers and "openrouter" not in chain_providers


def test_paid_not_allowed_excludes_cloud_llm(conn):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, template="instant-approx")  # allow_paid=false
    for cid in ("llm-deepseek", "llm-openrouter"):
        c = _cap(d, cid)
        assert c["hard_pass"] is False
        assert "paid_not_allowed" in c["excluded_reasons"]
    # Free local capability wins; whole chain is zero-cost.
    assert d["chosen_capability"] == "claude-code"
    for f in d["fallback_chain"]:
        assert _cap(d, f["capability_id"])["est"]["cost_usd"] == 0.0


def test_eta_exceeds_max_wait_excludes_slow_capability(conn):
    _seed(conn)
    _all_healthy(conn)
    # 1s max wait: claude-code (~30s eta) cannot make it; fast cloud can.
    _, d = _route(conn, template="instant-reliable",
                  allow_paid=True, max_wait_seconds=1, deadline_seconds=10)
    cc = _cap(d, "claude-code")
    assert "eta_exceeds_max_wait" in cc["excluded_reasons"]
    assert cc["hard_pass"] is False
    assert _cap(d, "llm-deepseek")["hard_pass"] is True


def test_circuit_open_excludes_provider(conn):
    _seed(conn)
    _all_healthy(conn)
    import time
    _set_provider_health(conn, "openrouter", "cooldown",
                         circuit_open_until=int(time.time()) + 600)
    _, d = _route(conn, template="instant-reliable", allow_paid=True)
    or_ = _cap(d, "llm-openrouter")
    assert or_["hard_pass"] is False
    assert "provider_circuit_open" in or_["excluded_reasons"]


def test_quota_exhausted_excludes_provider(conn):
    _seed(conn)
    _all_healthy(conn)
    import time
    _set_provider_health(conn, "deepseek", "exhausted",
                         quota_exhausted_until=int(time.time()) + 600)
    _, d = _route(conn, template="instant-reliable", allow_paid=True)
    ds = _cap(d, "llm-deepseek")
    assert ds["hard_pass"] is False
    assert "provider_quota_exhausted" in ds["excluded_reasons"]


def test_error_rate_high_excludes_provider(conn):
    _seed(conn)
    _all_healthy(conn)
    _set_provider_health(conn, "openrouter", "degraded", recent_error_rate=0.9)
    _, d = _route(conn, template="instant-reliable", allow_paid=True)
    or_ = _cap(d, "llm-openrouter")
    assert or_["hard_pass"] is False
    assert "provider_error_rate_high" in or_["excluded_reasons"]


def test_missing_key_excludes_provider(conn):
    _seed(conn)
    _all_healthy(conn)
    _set_provider_health(conn, "deepseek", "no_key", key_present=False)
    _, d = _route(conn, template="instant-reliable", allow_paid=True)
    ds = _cap(d, "llm-deepseek")
    assert ds["hard_pass"] is False
    assert "provider_no_key" in ds["excluded_reasons"]


def test_daily_budget_exceeded_excludes(conn):
    _seed(conn)
    _all_healthy(conn)
    _set_provider_health(conn, "openrouter", "healthy",
                         daily_budget_usd=5.0, daily_spend_usd=5.0)
    _, d = _route(conn, template="instant-reliable", allow_paid=True)
    or_ = _cap(d, "llm-openrouter")
    assert "provider_daily_budget_exceeded" in or_["excluded_reasons"]
    assert or_["hard_pass"] is False


def test_unknown_health_does_not_exclude(conn):
    _seed(conn)
    _set_provider_health(conn, "deepseek", "unknown",
                         key_present=None, recent_error_rate=0.0)
    # llm-hub offline style snapshot: key_present NULL, status unknown.
    conn.execute("UPDATE providers SET health='{}', health_status='unknown', "
                 "key_present=NULL WHERE id='deepseek'")
    _set_provider_health(conn, "openrouter", "healthy", recent_p50_ms=2200)
    _set_provider_health(conn, "local", "healthy")
    _, d = _route(conn, template="instant-reliable", allow_paid=True)
    ds = _cap(d, "llm-deepseek")
    assert ds["availability"] == "unknown"
    assert ds["hard_pass"] is True  # unknown is NOT a hard exclusion


def test_no_viable_candidate(conn):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, goal="drive a browser please", template="instant-approx",
                  output_type="computer_use",
                  expected_capabilities=["computer_use_browser"])
    assert d["chosen_capability"] is None
    assert d["chosen_reason"] == "no_viable_candidate"
    assert d["fallback_chain"] == []
    assert all("capability_does_not_match_task_type" in c["excluded_reasons"]
               for c in d["candidates"])


# --- soft scoring ------------------------------------------------------------

def test_latency_weight_prefers_fast_cloud(conn):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, template="instant-approx", allow_paid=True,
                  weights={"cost": 0.05, "latency": 0.9, "quality": 0.05})
    # Fast cheap DeepSeek beats both slow OpenRouter and 30s CLI agent.
    assert d["chosen_capability"] == "llm-deepseek"
    ds, cc = _cap(d, "llm-deepseek"), _cap(d, "claude-code")
    assert ds["scores"]["latency"] > cc["scores"]["latency"]


def test_quality_weight_prefers_capable(conn):
    _seed(conn)
    _all_healthy(conn)
    # async-rigorous weights quality 0.6; claude-code (quality 0.95, local) wins.
    _, d = _route(conn, goal="overnight research with sources",
                  template="async-rigorous")
    assert d["chosen_capability"] == "claude-code"
    cc = _cap(d, "claude-code")
    assert cc["scores"]["quality"] >= _cap(d, "llm-deepseek")["scores"]["quality"]


def test_async_economy_chain_is_free_only(conn):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, goal="whenever it is cheap", template="async-economy")
    assert d["chosen_capability"] == "claude-code"
    for f in d["fallback_chain"]:
        assert f["provider_id"] == "local"
    assert "paid_not_allowed" in _cap(d, "llm-deepseek")["excluded_reasons"]


def test_deterministic_tool_ranks_first_when_matching(conn):
    _seed(conn)
    _all_healthy(conn)
    _, d = _route(conn, goal="fetch https://example.com and extract the title",
                  template="instant-approx", output_type="url_fetch",
                  allow_paid=True, expected_capabilities=["url_fetch"])
    uf = _cap(d, "url-fetch")
    assert uf["hard_pass"] is True
    assert uf["deterministic"] is True
    assert d["chosen_capability"] == "url-fetch"
    assert d["chosen_reason"] == "deterministic_tool_preferred"
    assert d["fallback_chain"][0]["capability_id"] == "url-fetch"


# --- force / ban -------------------------------------------------------------

def test_ban_excludes_capability_and_provider(conn):
    _seed(conn)
    _all_healthy(conn)
    task = service.create_task(conn, goal="ban demo", sla_template="instant-reliable")
    from taskrouter.core import fsm
    fsm.transition(conn, task["id"], "preparing-context", event_type="phase")
    fsm.transition(conn, task["id"], "routing", event_type="phase")
    d = router.route_task(conn, task["id"], ban=["deepseek"])  # provider id
    ds = _cap(d, "llm-deepseek")
    assert "banned_by_operator" in ds["excluded_reasons"]
    assert ds["hard_pass"] is False
    assert "llm-deepseek" not in {f["capability_id"] for f in d["fallback_chain"]}
    assert d["forced"]["ban"] == ["deepseek"]


def test_force_overrides_economy_filters(conn):
    _seed(conn)
    _all_healthy(conn)
    task = service.create_task(conn, goal="force demo", sla_template="instant-approx")
    from taskrouter.core import fsm
    fsm.transition(conn, task["id"], "preparing-context", event_type="phase")
    fsm.transition(conn, task["id"], "routing", event_type="phase")
    # instant-approx bans paid usage; operator forces OpenRouter anyway.
    d = router.route_task(conn, task["id"], force=["llm-openrouter"])
    assert d["chosen_capability"] == "llm-openrouter"
    assert d["chosen_reason"] == "forced_by_operator"
    or_ = _cap(d, "llm-openrouter")
    assert "paid_not_allowed" in or_["forced_overrides"]
    assert d["forced"]["force"] == ["llm-openrouter"]


def test_force_does_not_override_safety_filters(conn):
    _seed(conn)
    _all_healthy(conn)
    task = service.create_task(conn, goal="force cloud on secret data",
                               sla_template="instant-reliable",
                               advanced={"local_only": True})
    from taskrouter.core import fsm
    fsm.transition(conn, task["id"], "preparing-context", event_type="phase")
    fsm.transition(conn, task["id"], "routing", event_type="phase")
    d = router.route_task(conn, task["id"], force=["llm-openrouter"])
    or_ = _cap(d, "llm-openrouter")
    assert or_["hard_pass"] is False
    assert "local_only_excludes_cloud" in or_["excluded_reasons"]
    assert or_["forced_overrides"] == []
    assert d["chosen_capability"] != "llm-openrouter"


def test_reroute_creates_newer_decision(conn):
    _seed(conn)
    _all_healthy(conn)
    task, d1 = _route(conn, template="instant-reliable")
    d2 = router.route_task(conn, task["id"], ban=["llm-openrouter", "llm-deepseek"])
    assert d2["id"] != d1["id"]
    assert router.get_latest_decision(conn, task["id"])["id"] == d2["id"]
    assert d2["fallback_chain"]  # local capabilities remain


# --- zero secrets (AC10) -----------------------------------------------------

def test_no_secret_in_registry_decisions_or_detail(conn, monkeypatch):
    # Even if a fake key lives in the environment (the only place M2 tolerates
    # a reference target), it must never land in rows, decisions or responses.
    monkeypatch.setenv("LLMHUB_KEY_DEEPSEEK", FAKE_KEY)
    _seed(conn)
    _all_healthy(conn)
    task, d = _route(conn, template="instant-reliable", allow_paid=True)

    blobs = [
        json.dumps(registry.list_providers(conn), ensure_ascii=False),
        json.dumps(registry.list_capabilities(conn), ensure_ascii=False),
        json.dumps(d, ensure_ascii=False),
        json.dumps(service.get_task_detail(conn, task["id"]), ensure_ascii=False),
        json.dumps(service.get_events(conn, task["id"]), ensure_ascii=False),
    ]
    for blob in blobs:
        assert FAKE_KEY not in blob
        assert not SECRET_PATTERN.search(blob)
    # Credential columns hold references only.
    provs = registry.list_providers(conn)
    for p in provs:
        if p["keychain_service"]:
            assert p["credential_ref"] == f"keychain:{p['keychain_service']}/{p['keychain_account']}"
        assert p["keychain_service"] in (None, "llm-hub")


def test_api_layer_exposes_no_secrets(monkeypatch):
    monkeypatch.setenv("LLMHUB_KEY_OPENROUTER", FAKE_KEY)
    from fastapi.testclient import TestClient
    from taskrouter.main import app

    with TestClient(app) as client:
        # Wait for the scheduler to route the task (loops run under lifespan).
        r = client.post("/api/tasks", json={
            "goal": "api zero-secret check", "sla_template": "instant-reliable"
        })
        tid = r.json()["id"]
        decision = None
        for _ in range(30):
            r = client.get(f"/api/tasks/{tid}/route-decision")
            if r.status_code == 200:
                decision = r.json()
                break
            import time
            time.sleep(0.3)
        assert decision is not None, "scheduler did not route the task in time"

        payloads = [
            client.get("/api/providers").text,
            client.get("/api/capabilities").text,
            client.get(f"/api/tasks/{tid}").text,
            json.dumps(decision),
        ]
        for blob in payloads:
            assert FAKE_KEY not in blob
            assert not SECRET_PATTERN.search(blob)

        # Provider payload carries reference + health booleans, never key material.
        provs = {p["id"]: p for p in client.get("/api/providers").json()["providers"]}
        assert "keychain_service" in provs["deepseek"]
        assert provs["deepseek"]["keychain_account"] == "deepseek"
