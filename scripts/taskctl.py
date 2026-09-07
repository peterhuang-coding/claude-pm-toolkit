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
    if task.get("attempts"):
        print("attempts:")
        for a in task["attempts"]:
            print(f"  #{a['seq']} {a['id']} status={a['status']} "
                  f"capability={a.get('capability_id')} stopped={a.get('stop_reason') or ''}")
    print("timeline:")
    for e in task["events"]:
        arrow = f"{e['from_status']} -> {e['to_status']}" if e["to_status"] else ""
        print(f"  {e['seq']:>3}. {e['created_at']} {e['type']:18s} {arrow}  {e.get('message') or ''}")


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

    p_show = sub.add_parser("show", help="show task detail with event timeline")
    p_show.add_argument("task_id")

    args = parser.parse_args()
    {"templates": cmd_templates, "create": cmd_create,
     "list": cmd_list, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    main()
