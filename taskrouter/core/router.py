"""Rule router (M2): hard constraints -> soft scoring -> fallback chain.

First-stage router is pure rules (no learning, PRD "先过滤再评分"):

  1. Hard filters exclude candidates that cannot serve the task contract:
     capability/task-type match, local-only data egress, risk/side-effect
     permissions, context window, deadline (eta vs max_wait), budget
     (max_cost / allow_paid / provider daily budget) and provider availability
     (enabled + key present + not circuit-open + not quota-exhausted +
     error_rate < 0.5 — semantics aligned with llm-hub ccr-router.js).
  2. Surviving candidates are soft-scored on cost / latency / quality /
     quota headroom / health; weights come from the task contract (SLA
     template), defaults from router_rules.json.
  3. Deterministic local tools that pass every hard filter rank first
     (zero cost, zero risk). The ordered remainder is the fallback chain.

Every routing pass persists one route_decisions row containing ALL candidates
with per-filter pass/fail reasons and per-dimension scores — the explainability
record the dashboard (M5) renders. Force/ban (API parameters, M2) implement
human override: ban excludes a capability/provider; force pins the choice
(overriding availability/soft filters, never data-safety filters).

Adapters do not exist until M3: after a decision the task parks in 'routing'
with wait_reason='awaiting-adapter'. Capabilities with adapter_ready=false are
NOT excluded — they are scored and ranked, just marked non-executable.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Optional

from .. import config
from . import fsm

ROUTER_RULES_NAME = "router_rules"
AWAITING_ADAPTER = "awaiting-adapter"

#: Soft-score weights for dimensions not carried by SLA templates.
_W_HEADROOM = 0.10
_W_HEALTH = 0.10
#: The cost/latency/quality triple (sums to 1.0 in every SLA template) gets
#: the remaining mass.
_W_MAIN = 1.0 - _W_HEADROOM - _W_HEALTH

_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

# Output-token estimate by correctness target (for cost/latency estimation).
_OUT_TOKENS = {"approx": 400, "reliable": 1200, "verified": 2500}
_BASE_CONTEXT_TOKENS = 2000


def _new_id(prefix: str) -> str:
    return prefix + "".join(secrets.choice(_ALPHABET) for _ in range(10))


def load_rules() -> dict[str, Any]:
    with open(config.router_rules_path(), "r", encoding="utf-8") as f:
        return json.load(f)


# --- inputs ------------------------------------------------------------------

_LIST_FIELDS = ("task_types", "models")
_DICT_FIELDS = ("supports", "config", "provider_pricing", "provider_quota", "provider_health")


def _load_capabilities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*, p.name AS provider_name, p.source AS provider_source,
               p.pricing AS provider_pricing, p.quota AS provider_quota,
               p.health AS provider_health, p.health_status, p.key_present
        FROM capabilities c LEFT JOIN providers p ON c.provider_id = p.id
        """
    ).fetchall()
    caps = []
    for r in rows:
        d = dict(r)
        for k in _LIST_FIELDS:
            d[k] = json.loads(d[k]) if d.get(k) else []
        for k in _DICT_FIELDS:
            d[k] = json.loads(d[k]) if d.get(k) else {}
        caps.append(d)
    return caps


def _estimate_context_tokens(task: dict[str, Any]) -> int:
    return _BASE_CONTEXT_TOKENS + len(task.get("goal") or "") // 4


def _estimate_output_tokens(contract: dict[str, Any]) -> int:
    return _OUT_TOKENS.get(contract.get("correctness_target", "approx"), 800)


def _estimate_cost_usd(cap: dict[str, Any], in_tokens: int, out_tokens: int) -> float:
    pricing = cap.get("provider_pricing") or {}
    models = cap.get("models") or []
    if not models:
        return 0.0  # local / CLI / deterministic tools carry no per-token price
    price = pricing.get(models[0]) or {}
    in_price = float(price.get("input_per_mtok_usd") or 0.0)
    out_price = float(price.get("output_per_mtok_usd") or 0.0)
    return (in_tokens * in_price + out_tokens * out_price) / 1_000_000.0


def _estimate_latency_ms(cap: dict[str, Any]) -> float:
    health = cap.get("provider_health") or {}
    if health.get("recent_p50_ms"):
        return float(health["recent_p50_ms"])
    return float((cap.get("config") or {}).get("est_latency_ms") or 10_000)


# --- hard filters ------------------------------------------------------------

def _availability(cap: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Return (state, reason). state in usable|unknown|unusable.

    Usable semantics aligned with llm-hub ccr-router.js isProviderUsable:
    enabled AND key_present AND not quota-exhausted AND not circuit-open
    AND recent_error_rate < 0.50.
    """
    if cap.get("provider_id") == "local":
        return "usable", None
    h = cap.get("provider_health") or {}
    if h.get("enabled") is False:
        return "unusable", "provider_disabled"
    # SQLite stores booleans as 0/1 integers; 0 (or False) means key missing.
    if cap.get("key_present") is not None and not cap.get("key_present"):
        return "unusable", "provider_no_key"
    status = cap.get("health_status") or "unknown"
    if status == "exhausted" or (h.get("quota_exhausted_until") or 0) > _now_ts():
        return "unusable", "provider_quota_exhausted"
    if status == "cooldown" or (h.get("circuit_open_until") or 0) > _now_ts():
        return "unusable", "provider_circuit_open"
    if (h.get("recent_error_rate") or 0.0) >= 0.5:
        return "unusable", "provider_error_rate_high"
    if status == "unknown":
        return "unknown", None
    return "usable", None


def _now_ts() -> int:
    import time
    return int(time.time())


def hard_filters(
    task: dict[str, Any],
    contract: dict[str, Any],
    cap: dict[str, Any],
    context_tokens: int,
    est_cost: float,
    eta_ms: float,
) -> list[str]:
    """Return a list of failed-filter reason codes (empty = hard pass)."""
    reasons: list[str] = []
    tags = set((cap.get("config") or {}).get("tags") or [])
    task_types = set(cap.get("task_types") or [])
    expected = set(contract.get("expected_capabilities") or [])
    output_type = task.get("output_type") or "text"

    if not cap.get("enabled", 1):
        reasons.append("capability_disabled")

    # 1. capability matches task type / expected capabilities
    if output_type not in task_types and not (tags & expected):
        reasons.append("capability_does_not_match_task_type")

    supports = cap.get("supports") or {}
    # 2. local-only / data sensitivity -> no cloud egress
    if contract.get("local_only") and supports.get("cloud"):
        reasons.append("local_only_excludes_cloud")

    # 3. risk level & side-effect permissions
    risk_levels = set(supports.get("risk_levels") or [])
    if risk_levels and contract.get("risk_level") not in risk_levels:
        reasons.append("risk_level_not_supported")
    side_effects = set(supports.get("side_effects") or [])
    if side_effects and contract.get("side_effect_limit") not in side_effects:
        reasons.append("side_effect_not_supported")

    # 4. context fits model window
    window = supports.get("context_window")
    if window and context_tokens > int(window):
        reasons.append("context_exceeds_window")

    # 5. deadline feasible: estimated latency must fit max wait
    max_wait = float(contract.get("max_wait_seconds") or contract.get("deadline_seconds") or 0)
    if max_wait and eta_ms / 1000.0 > max_wait:
        reasons.append("eta_exceeds_max_wait")

    # 6. budget: task max cost, paid permission, provider daily budget
    max_cost = float(contract.get("max_cost_usd") or 0.0)
    if est_cost > max_cost:
        reasons.append("estimated_cost_over_max_cost")
    if not contract.get("allow_paid", False) and est_cost > 0:
        reasons.append("paid_not_allowed")
    quota = cap.get("provider_quota") or {}
    remaining = quota.get("remaining_usd")
    if remaining is not None and est_cost > float(remaining):
        reasons.append("provider_daily_budget_exceeded")
    if (quota.get("quota_exhausted_until") or 0) > _now_ts():
        reasons.append("provider_quota_exhausted")

    # 7. provider availability (ccr-router semantics); unknown does NOT exclude
    avail, avail_reason = _availability(cap)
    if avail == "unusable" and avail_reason:
        reasons.append(avail_reason)

    # de-duplicate while preserving order
    seen: set[str] = set()
    unique = [r for r in reasons if not (r in seen or seen.add(r))]
    return unique


# --- soft scoring ------------------------------------------------------------

def _score_dimensions(
    contract: dict[str, Any],
    cap: dict[str, Any],
    est_cost: float,
    eta_ms: float,
    avail: str,
) -> dict[str, float]:
    rules = load_rules()
    w = dict(rules.get("default_weights", {}))
    w.update({k: v for k, v in (contract.get("weights") or {}).items() if k in ("cost", "latency", "quality")})
    w_cost = float(w.get("cost", 0.3))
    w_lat = float(w.get("latency", 0.3))
    w_qual = float(w.get("quality", 0.4))
    wsum = w_cost + w_lat + w_qual or 1.0

    # cost: free = 1.0; cheaper paid scores higher (log-ish decay)
    cost_s = 1.0 if est_cost <= 0 else 1.0 / (1.0 + est_cost / 0.02)
    # latency: 800ms ~ 0.86, 2s ~ 0.71, 30s ~ 0.14
    latency_s = 1.0 / (1.0 + eta_ms / 5000.0)
    # quality: registered static quality score
    quality_s = float((cap.get("config") or {}).get("quality") or 0.5)
    # quota headroom
    quota = cap.get("provider_quota") or {}
    budget = quota.get("daily_budget_usd")
    remaining = quota.get("remaining_usd")
    if cap.get("provider_id") == "local" or est_cost <= 0:
        headroom_s = 1.0
    elif remaining is not None and budget:
        headroom_s = max(0.0, min(1.0, float(remaining) / float(budget)))
    else:
        headroom_s = 0.7  # unknown budget: neutral
    # health
    status = cap.get("health_status") or "unknown"
    health_s = {"healthy": 1.0, "degraded": 0.4, "unknown": 0.6}.get(status, 0.0)
    if avail == "unusable":
        health_s = 0.0

    total = (
        _W_MAIN * (
            (w_cost / wsum) * cost_s
            + (w_lat / wsum) * latency_s
            + (w_qual / wsum) * quality_s
        )
        + _W_HEADROOM * headroom_s
        + _W_HEALTH * health_s
    )
    return {
        "cost": round(cost_s, 4),
        "latency": round(latency_s, 4),
        "quality": round(quality_s, 4),
        "quota_headroom": round(headroom_s, 4),
        "health": round(health_s, 4),
        "total": round(total, 4),
    }


# --- routing -----------------------------------------------------------------

#: Hard filters a human force MAY override (availability/economy only;
#: data-safety filters are never overridden).
_FORCE_OVERRIDABLE = {
    "eta_exceeds_max_wait",
    "estimated_cost_over_max_cost",
    "paid_not_allowed",
    "provider_daily_budget_exceeded",
    "provider_quota_exhausted",
    "provider_circuit_open",
    "provider_error_rate_high",
    "provider_disabled",
    "provider_no_key",
}
_SAFETY_FILTERS = {
    "local_only_excludes_cloud",
    "risk_level_not_supported",
    "side_effect_not_supported",
    "context_exceeds_window",
    "capability_disabled",
    "capability_does_not_match_task_type",
}


def route_task(
    conn: sqlite3.Connection,
    task_id: str,
    force: Optional[list[str]] = None,
    ban: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run one routing pass and persist a route_decisions row.

    Returns the decision dict (also stored). Does NOT move task status — the
    caller (scheduler/API) parks the task at routing with wait_reason set.
    """
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError(f"task not found: {task_id}")
    task = dict(task)
    contract = json.loads(task["contract"])
    force = list(force or [])
    ban = list(ban or [])

    context_tokens = _estimate_context_tokens(task)
    out_tokens = _estimate_output_tokens(contract)
    caps = _load_capabilities(conn)

    candidates: list[dict[str, Any]] = []
    exclusions: dict[str, list[str]] = {}
    scores: dict[str, Any] = {}

    def _banned(cap: dict[str, Any]) -> bool:
        return cap["id"] in ban or cap.get("provider_id") in ban

    def _forced(cap: dict[str, Any]) -> bool:
        return cap["id"] in force or cap.get("provider_id") in force

    for cap in caps:
        est_cost = _estimate_cost_usd(cap, context_tokens, out_tokens)
        eta_ms = _estimate_latency_ms(cap)
        reasons = hard_filters(task, contract, cap, context_tokens, est_cost, eta_ms)
        avail, _ = _availability(cap)
        is_banned = _banned(cap)
        is_forced = _forced(cap)
        if is_banned:
            reasons = reasons + ["banned_by_operator"]
        overridden = [r for r in reasons if r in _FORCE_OVERRIDABLE] if is_forced else []
        safety_block = [r for r in reasons if r in _SAFETY_FILTERS]
        hard_pass = (not reasons) or (is_forced and not safety_block and "banned_by_operator" not in reasons)

        sc = _score_dimensions(contract, cap, est_cost, eta_ms, avail)
        deterministic = bool((cap.get("supports") or {}).get("deterministic"))
        cand = {
            "capability_id": cap["id"],
            "capability_name": cap["name"],
            "kind": cap["kind"],
            "provider_id": cap.get("provider_id"),
            "provider_name": cap.get("provider_name"),
            "hard_pass": hard_pass,
            "excluded_reasons": reasons,
            "forced_overrides": overridden,
            "availability": avail,
            "adapter_ready": bool((cap.get("config") or {}).get("adapter_ready", False)),
            "deterministic": deterministic,
            "forced": is_forced,
            "banned": is_banned,
            "est": {
                "context_tokens": context_tokens,
                "output_tokens": out_tokens,
                "latency_ms": round(eta_ms, 1),
                "cost_usd": round(est_cost, 6),
            },
            "scores": sc,
        }
        candidates.append(cand)
        if reasons:
            exclusions[cap["id"]] = reasons
        scores[cap["id"]] = sc

    viable = [c for c in candidates if c["hard_pass"]]
    # Deterministic zero-cost tools rank first when they satisfy hard filters;
    # then total score desc, then latency asc as a stable tiebreaker.
    viable.sort(key=lambda c: (
        0 if c["forced"] else 1,
        0 if c["deterministic"] else 1,
        -c["scores"]["total"],
        c["est"]["latency_ms"],
    ))
    fallback_chain = [
        {"capability_id": c["capability_id"], "provider_id": c["provider_id"],
         "total": c["scores"]["total"], "deterministic": c["deterministic"],
         "adapter_ready": c["adapter_ready"]}
        for c in viable
    ]
    chosen = viable[0] if viable else None
    rules = load_rules()

    if chosen is None:
        chosen_reason = "no_viable_candidate"
    elif chosen["forced"]:
        chosen_reason = "forced_by_operator"
    elif chosen["deterministic"]:
        chosen_reason = "deterministic_tool_preferred"
    else:
        chosen_reason = "highest_soft_score"

    decision_id = _new_id("rd_")
    now = fsm.utcnow()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO route_decisions
                (id, task_id, candidates, exclusions, scores,
                 chosen_capability, chosen_provider, fallback_chain,
                 rules_version, forced, chosen_reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                decision_id, task_id,
                json.dumps(candidates, ensure_ascii=False),
                json.dumps(exclusions, ensure_ascii=False),
                json.dumps(scores, ensure_ascii=False),
                chosen["capability_id"] if chosen else None,
                chosen["provider_id"] if chosen else None,
                json.dumps(fallback_chain, ensure_ascii=False),
                f"{ROUTER_RULES_NAME}@v{rules.get('version', 1)}",
                json.dumps({"force": force, "ban": ban}, ensure_ascii=False),
                chosen_reason,
                now,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "id": decision_id,
        "task_id": task_id,
        "candidates": candidates,
        "exclusions": exclusions,
        "scores": scores,
        "chosen_capability": chosen["capability_id"] if chosen else None,
        "chosen_provider": chosen["provider_id"] if chosen else None,
        "chosen_reason": chosen_reason,
        "fallback_chain": fallback_chain,
        "rules_version": f"{ROUTER_RULES_NAME}@v{rules.get('version', 1)}",
        "forced": {"force": force, "ban": ban},
        "created_at": now,
    }


def get_latest_decision(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM route_decisions WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    for k in ("candidates", "exclusions", "scores", "fallback_chain", "forced"):
        d[k] = json.loads(d[k])
    return d


def mark_awaiting_adapter(
    conn: sqlite3.Connection, task_id: str, decision: dict[str, Any]
) -> None:
    """Park a routed task: status stays 'routing', wait_reason marks the M3
    boundary, and a 'routed' event records the explainable outcome."""
    conn.execute("BEGIN")
    try:
        now = fsm.utcnow()
        conn.execute(
            "UPDATE tasks SET wait_reason = ?, updated_at = ? WHERE id = ?",
            (AWAITING_ADAPTER, now, task_id),
        )
        chosen = decision["chosen_capability"]
        n_excluded = sum(
            1 for c in decision["candidates"] if not c["hard_pass"]
        )
        if chosen:
            msg = (
                f"routed -> {chosen} (provider {decision['chosen_provider']}, "
                f"{decision['chosen_reason']}); {n_excluded} candidate(s) excluded; "
                f"fallback chain depth {len(decision['fallback_chain'])}; "
                "parked awaiting adapter (M3)"
            )
        else:
            msg = (
                f"no viable candidate after hard filters "
                f"({n_excluded} excluded); parked awaiting adapter (M3)"
            )
        fsm.add_event(
            conn,
            task_id,
            event_type="routed",
            message=msg,
            data={
                "decision_id": decision["id"],
                "chosen_capability": chosen,
                "chosen_provider": decision["chosen_provider"],
                "chosen_reason": decision["chosen_reason"],
                "fallback_chain": decision["fallback_chain"],
                "excluded": decision["exclusions"],
                "rules_version": decision["rules_version"],
                "forced": decision["forced"],
                "wait_reason": AWAITING_ADAPTER,
            },
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
