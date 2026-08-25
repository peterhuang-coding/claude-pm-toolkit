#!/usr/bin/env python3
"""PreToolUse guard for Claude Code.

两层防护（对 bypassPermissions 会话同样生效，hook 独立于权限模式）：
1. 常开：重复调用循环检测 —— 同一 (tool, input) 在 300 秒内出现 6 次即拦截，
   覆盖重试风暴与读→跑→读短路循环。
2. 夜班：当前目录祖先存在 .claude/autopilot/.loop.lock 时，拦截不可逆/危险
   命令（rm -rf、git push -f、git reset --hard、sudo、diskutil 等）。

输入：PreToolUse hook JSON（stdin）。输出：允许 = "{}"（stdout）+ 退出码 0；
拦截 = stderr 说明 + 退出码 2。任何内部异常都放行，绝不打断会话。
"""
import json
import sys
import time
from pathlib import Path

HOME = Path.home()
STATE_FILE = HOME / ".claude" / "pm-guard-state.json"
REPEAT_LIMIT = 6
REPEAT_WINDOW = 300
MAX_STATE_ENTRIES = 300

DANGEROUS = [
    ("rm -rf", "rm -rf 不可逆删除"),
    ("rm -fr", "rm -fr 不可逆删除"),
    ("rm -rf ", "rm -rf 不可逆删除"),
    ("git push --force", "强制推送"),
    ("git push -f", "强制推送"),
    ("git reset --hard", "硬重置"),
    ("git clean -fd", "清理未跟踪文件"),
    ("sudo ", "sudo 提权"),
    ("diskutil ", "磁盘工具"),
    ("shutdown", "关机"),
    ("reboot", "重启"),
    ("halt", "关机"),
    ("launchctl unload", "卸载服务"),
    ("launchctl remove", "移除服务"),
    ("launchctl bootout", "移除服务"),
]


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def loop_check(payload: dict) -> int:
    """Record this call and return how many times it has fired in the window."""
    tool = str(payload.get("tool_name", ""))
    try:
        inp = json.dumps(payload.get("tool_input", {}), sort_keys=True)
    except (TypeError, ValueError):
        inp = str(payload.get("tool_input", ""))
    key = f"{tool}\t{inp}"
    now = time.time()
    state = load_state()
    calls = [
        c for c in state.get("calls", [])
        if isinstance(c, dict) and now - c.get("t", 0) < REPEAT_WINDOW
    ]
    count = 1 + sum(1 for c in calls if c.get("k") == key)
    calls.append({"k": key, "t": now})
    state["calls"] = calls[-MAX_STATE_ENTRIES:]
    save_state(state)
    return count


def night_shift_active(cwd: str) -> bool:
    try:
        p = Path(cwd).expanduser().resolve()
    except OSError:
        return False
    for parent in (p, *p.parents):
        if (parent / ".claude" / "autopilot" / ".loop.lock").exists():
            return True
    return False


def bash_danger(command: str):
    if not isinstance(command, str):
        return None
    low = command.lower()
    for pattern, label in DANGEROUS:
        if pattern.lower() in low:
            return label
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    try:
        tool = str(payload.get("tool_name", ""))

        # 1. always-on loop detection
        count = loop_check(payload)
        if count >= REPEAT_LIMIT:
            sys.stderr.write(
                f"[pm-guard] 第 {count} 次重复调用被拦截：{tool}。"
                "疑似循环 / 重试风暴，已阻止。若确属必要，"
                f"删除 {STATE_FILE} 后重试，或检查任务是否在原地打转。\n"
            )
            return 2

        # 2. night-shift irreversible blocking
        cwd = str(payload.get("cwd", ""))
        if cwd and tool == "Bash" and night_shift_active(cwd):
            inp = payload.get("tool_input")
            command = str(inp.get("command", "")) if isinstance(inp, dict) else ""
            label = bash_danger(command)
            if label:
                sys.stderr.write(
                    f"[pm-guard] 夜班保护拦截：{label}（{command[:80]}…）。"
                    "夜班期间禁止不可逆 / 危险操作。如有真实需求："
                    "写入主线待决策项等白班决定，或停止夜班后手动执行。\n"
                )
                return 2

        print("{}")
        return 0
    except Exception:
        print("{}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
