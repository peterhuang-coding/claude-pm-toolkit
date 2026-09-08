#!/usr/bin/env python3
"""taskctl — CLI for the Task Router API (stdlib only).

Examples:
  taskctl templates
  taskctl create "总结这个网页的要点" --sla instant-approx
  taskctl create "隔夜调研竞品，要有来源" --sla async-rigorous --set max_cost_usd=3.0
  taskctl list
  taskctl list --status routing
  taskctl show t_ab12cd34
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TASKROUTER_BASE_URL", "http://127.0.0.1:3459")


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"cannot reach Task Router at {BASE_URL}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _parse_overrides(pairs: list[str]) -> dict:
    """Parse --set key=value; values are JSON when possible, else raw string."""
    out: dict = {}
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f"--set expects key=value, got: {p}")
        k, v = p.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_templates(_args) -> None:
    data = _request("GET", "/api/sla-templates")
    for key, tpl in data["templates"].items():
        print(f"{key:16s} {tpl['name']}  deadline={tpl['deadline_seconds']}s "
              f"correctness={tpl['correctness_target']} paid={tpl['allow_paid']}")
        print(f"  {tpl['description']}")


def cmd_create(args) -> None:
    body = {
        "goal": args.goal,
        "sla_template": args.sla,
        "output_type": args.output_type,
        "priority": args.priority,
    }
    overrides = _parse_overrides(args.set)
    if overrides:
        body["advanced"] = overrides
    task = _request("POST", "/api/tasks", body)
    print(f"created {task['id']}  status={task['status']}  sla={task['sla_template']}")
    print(f"  goal: {task['goal']}")


def cmd_list(args) -> None:
    path = "/api/tasks?limit=" + str(args.limit)
    if args.status:
        path += "&status=" + args.status
    data = _request("GET", path)
    if not data["tasks"]:
        print("(no tasks)")
        return
    for t in data["tasks"]:
        print(f"{t['id']}  {t['status']:18s} {t['sla_template']:16s} {t['goal'][:60]}")


def cmd_show(args) -> None:
    task = _request("GET", f"/api/tasks/{args.task_id}")
    print(f"task {task['id']}  status={task['status']}  sla={task['sla_template']}")
    print(f"goal: {task['goal']}")
    c = task["contract"]
    print(f"contract: deadline={c['deadline_seconds']}s correctness={c['correctness_target']} "
          f"max_cost=${c['max_cost_usd']} allow_paid={c['allow_paid']} "
          f"risk={c['risk_level']} delivery={c['delivery_mode']}")
    if task.get("wait_reason"):
        print(f"wait_reason: {task['wait_reason']}")
    rd = task.get("route_decision")
    if rd:
        print(f"route decision {rd['id']} ({rd['rules_version']}, {rd['chosen_reason']}):")
        for c in rd["candidates"]:
            mark = "PASS" if c["hard_pass"] else "EXCL"
            reasons = ",".join(c["excluded_reasons"]) if c["excluded_reasons"] else ""
            forced = " [FORCED]" if c.get("forced") else ""
            print(f"  [{mark}] {c['capability_id']:16s} provider={str(c.get('provider_id')):10s} "
                  f"total={c['scores']['total']:.3f} cost=${c['est']['cost_usd']:.5f} "
                  f"eta={c['est']['latency_ms']:.0f}ms avail={c['availability']}{forced}")
            if reasons:
                print(f"         excluded: {reasons}")
        chain = " -> ".join(f["capability_id"] for f in rd["fallback_chain"]) or "(empty)"
        print(f"  chosen: {rd['chosen_capability']} via {rd['chosen_provider']}")
        print(f"  fallback chain: {chain}")
        if rd.get("forced", {}).get("force") or rd.get("forced", {}).get("ban"):
            print(f"  operator override: {rd['forced']}")
    if task.get("attempts"):
        print("attempts:")
        for a in task["attempts"]:
            print(f"  #{a['seq']} {a['id']} status={a['status']} "
                  f"capability={a.get('capability_id')} stopped={a.get('stop_reason') or ''}")
    print("timeline:")
    for e in task["events"]:
        arrow = f"{e['from_status']} -> {e['to_status']}" if e["to_status"] else ""
        print(f"  {e['seq']:>3}. {e['created_at']} {e['type']:18s} {arrow}  {e.get('message') or ''}")


def cmd_reroute(args) -> None:
    body = {}
    if args.force:
        body["force"] = args.force
    if args.ban:
        body["ban"] = args.ban
    d = _request("POST", f"/api/tasks/{args.task_id}/reroute", body)
    print(f"rerouted {args.task_id}: chosen={d['chosen_capability']} "
          f"via {d['chosen_provider']} ({d['chosen_reason']})")
    chain = " -> ".join(f["capability_id"] for f in d["fallback_chain"]) or "(empty)"
    print(f"  fallback chain: {chain}")
    for c in d["candidates"]:
        if not c["hard_pass"]:
            print(f"  excluded {c['capability_id']}: {','.join(c['excluded_reasons'])}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="taskctl", description="Task Router CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("templates", help="list SLA templates")

    p_create = sub.add_parser("create", help="create a task (goal + SLA, no agent choice)")
    p_create.add_argument("goal")
    p_create.add_argument("--sla", default="instant-approx",
                          choices=["instant-approx", "instant-reliable", "async-economy", "async-rigorous"])
    p_create.add_argument("--output-type", default="text")
    p_create.add_argument("--priority", type=int, default=0)
    p_create.add_argument("--set", action="append", metavar="key=value",
                          help="advanced contract override, repeatable")

    p_list = sub.add_parser("list", help="list tasks")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="show task detail with event timeline + route decision")
    p_show.add_argument("task_id")

    p_rr = sub.add_parser("reroute", help="re-route a task at 'routing' with force/ban")
    p_rr.add_argument("task_id")
    p_rr.add_argument("--force", action="append", metavar="ID",
                      help="capability/provider id to force (repeatable)")
    p_rr.add_argument("--ban", action="append", metavar="ID",
                      help="capability/provider id to ban (repeatable)")

    args = parser.parse_args()
    {"templates": cmd_templates, "create": cmd_create,
     "list": cmd_list, "show": cmd_show, "reroute": cmd_reroute}[args.cmd](args)


if __name__ == "__main__":
    main()
