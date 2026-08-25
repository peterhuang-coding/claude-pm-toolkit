#!/usr/bin/env python3
"""Night-shift morning report via the Feishu gateway. Never interferes.

Usage: autopilot-notify.py "<status>" "<reason>"
Env gates: AP_NOTIFY_SKIP=1 (silently skip), AP_NOTIFY_DRY=1 (print instead of send).
"""
import os
import sys
from pathlib import Path

PM_SCRIPTS = Path.home() / ".claude" / "skills" / "pm-orchestrator" / "scripts"


def main() -> int:
    if os.environ.get("AP_NOTIFY_SKIP"):
        return 0
    status = sys.argv[1] if len(sys.argv) > 1 else "状态未知"
    reason = sys.argv[2] if len(sys.argv) > 2 else ""
    cwd = Path.cwd()
    text = (
        f"【夜班晨报】{cwd.name}\n"
        f"状态：{status}\n"
        f"{reason}\n"
        "进度：.claude/autopilot/mainline.md（/autopilot status 查看）"
    )
    if os.environ.get("AP_NOTIFY_DRY"):
        print(text)
        return 0
    try:
        sys.path.insert(0, str(PM_SCRIPTS))
        from pm_feishu import is_configured, send_text  # noqa: E402

        if not is_configured():
            return 0
        send_text(text, retries=1, timeout=5)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
