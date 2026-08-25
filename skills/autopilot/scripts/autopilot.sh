#!/bin/sh
# autopilot.sh — 夜班无人值守循环
# 每轮：新会话 -> 读主线 -> 只做当前任务 -> 验证 -> commit -> 更新主线 -> 摘要
# 防停滞：轮次前后 git 无任何变化 = 停滞轮；连续 --max-stall 轮 -> 自动写待决策并停止
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LAUNCHER=${PM_CLAUDE_LAUNCHER:-"$SCRIPT_DIR/../../pm-orchestrator/scripts/launch-claude.sh"}
MAINLINE=".claude/autopilot/mainline.md"
AUTO_DIR=".claude/autopilot"
ROUNDS_DIR="$AUTO_DIR/rounds"
LOCK_DIR="$AUTO_DIR/.loop.lock"

MAX_ROUNDS=${AP_MAX_ROUNDS:-10}
MAX_STALL=${AP_MAX_STALL:-3}
SLEEP_SECONDS=${AP_SLEEP_SECONDS:-60}
ROUND_TIMEOUT=${AP_ROUND_TIMEOUT:-1800}
UNTIL=
MAX_LOG_BYTES=${AP_MAX_LOG_BYTES:-12000}
STATUS="异常退出"
EXIT_REASON=""
NOTIFIED=0

die() {
  EXIT_REASON="原因：$*"
  echo "autopilot: $*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage:
  autopilot.sh [--rounds N] [--until HH:MM|ISO-8601] [--sleep-seconds N] [--max-stall N] [--round-timeout S]
Defaults: 10 rounds, 3 stall rounds, 60s sleep, 1800s per-round timeout, 8 hours until deadline.
EOF
  exit 2
}

positive_int() {
  case "$2" in
    ''|*[!0-9]*) die "$1 must be a non-negative integer" ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --rounds)       [ "$#" -ge 2 ] || usage; MAX_ROUNDS=$2; shift 2 ;;
    --until)        [ "$#" -ge 2 ] || usage; UNTIL=$2; shift 2 ;;
    --sleep-seconds)[ "$#" -ge 2 ] || usage; SLEEP_SECONDS=$2; shift 2 ;;
    --max-stall)    [ "$#" -ge 2 ] || usage; MAX_STALL=$2; shift 2 ;;
    --round-timeout)[ "$#" -ge 2 ] || usage; ROUND_TIMEOUT=$2; shift 2 ;;
    *) usage ;;
  esac
done

positive_int '--rounds' "$MAX_ROUNDS"
positive_int '--sleep-seconds' "$SLEEP_SECONDS"
positive_int '--max-stall' "$MAX_STALL"
positive_int '--round-timeout' "$ROUND_TIMEOUT"

[ -f "$MAINLINE" ] || die "no $MAINLINE; run /autopilot (white shift) first"
[ -x "$LAUNCHER" ] || die "cannot find launcher: $LAUNCHER"

# deadline
if [ -n "$UNTIL" ]; then
  case "$UNTIL" in
    *T*)
      DEADLINE=$(date -j -f '%Y-%m-%dT%H:%M:%S' "$UNTIL" '+%s' 2>/dev/null) \
        || DEADLINE=$(date -d "$UNTIL" '+%s' 2>/dev/null) \
        || die "invalid --until: $UNTIL"
      ;;
    *)
      DEADLINE=$(date -j -f '%H:%M' "$UNTIL" '+%s' 2>/dev/null) \
        || die "invalid --until: $UNTIL (use HH:MM or YYYY-MM-DDTHH:MM:SS)"
      ;;
  esac
else
  DEADLINE=$(date -j -v+8H '+%s' 2>/dev/null || date -d '+8 hours' '+%s')
fi

# lock: one night shift per project
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  die "another autopilot loop owns this project: $LOCK_DIR (stop it or remove the lock after confirming it is dead)"
fi
notify_exit() {
  if [ "$NOTIFIED" -eq 1 ] || [ "${ROUND:-0}" -eq 0 ]; then return 0; fi
  NOTIFIED=1
  python3 "$SCRIPT_DIR/autopilot-notify.py" "$STATUS" "$EXIT_REASON" >/dev/null 2>&1 || true
}
cleanup() { notify_exit; rm -rf "$LOCK_DIR"; }
stop_loop() { STATUS="手动停止"; cleanup; exit 130; }
trap cleanup EXIT
trap stop_loop HUP INT TERM
printf '%s\n' "$$" > "$LOCK_DIR/pid"
date '+%Y-%m-%dT%H:%M:%S' > "$LOCK_DIR/started-at"

mkdir -p "$ROUNDS_DIR"

append_stall_decision() {
  STALL_N=$1
  python3 - "$MAINLINE" "$STALL_N" <<'PYEOF'
import sys
path, stall = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
entry = ("- 【夜班停滞】连续 %s 轮无增量（见 rounds/round-*.md 摘要）。"
         "选项：A 人工介入 B 换 work item C 结束夜班。推荐：C。"
         "影响：不决策则夜班不再自动推进。风险等级：低。auto-default：否\n" % stall)
if "## 待决策" in text:
    text = text.replace("## 待决策", "## 待决策\n" + entry, 1)
else:
    text = text.rstrip() + "\n\n## 待决策\n" + entry + "\n"
open(path, "w", encoding="utf-8").write(text)
PYEOF
}

ROUND=0
STALL=0
TRANSPORT_FAILURES=0

while :; do
  NOW=$(date '+%s')
  if [ "$NOW" -ge "$DEADLINE" ]; then
    STATUS="时间到"
    echo "autopilot: deadline reached"
    exit 0
  fi
  if [ "$ROUND" -ge "$MAX_ROUNDS" ]; then
    STATUS="轮次用尽"
    echo "autopilot: max rounds reached ($MAX_ROUNDS)"
    exit 0
  fi

  ROUND=$((ROUND + 1))
  PROMPT_FILE="$ROUNDS_DIR/round-$ROUND.prompt"
  RAW_OUTPUT="$ROUNDS_DIR/round-$ROUND.raw"
  OUTPUT="$ROUNDS_DIR/round-$ROUND.log"
  RECENT=$(ls "$ROUNDS_DIR"/round-*.md 2>/dev/null | tail -2 | while read -r f; do echo "--- $(basename "$f") ---"; head -30 "$f"; done)

  cat > "$PROMPT_FILE" <<EOF
You are round $ROUND of the unattended "night shift" development loop.

Read these, in order:
1. The mainline (single source of truth):
$(cat "$MAINLINE")

2. Recent round summaries:
${RECENT:-"(none yet)"}

3. git status.

Rules:
- Work ONLY on the 当前任务 in the mainline. Minimal changes. Do not expand scope.
- Do not repeat a path already listed under 已试路径.
- Verify with real commands and record real output. Never claim "should work".
- Commit verified changes on the current branch.
- Update the mainline file: append progress under 进度, add anything you tried to 已试路径, and if 当前任务 is done (verified against its acceptance criteria) mark it so.
- If you cannot proceed without a user decision: add ONE item to 待决策 (background, options A/B/C, recommendation, impact, auto-default), do NOT decide it yourself, and reply BLOCKED.
- End your response with exactly one line: CONTINUE, DONE, or BLOCKED.

Return a concise round summary: what changed, verification output (short), commit, and next action. No full files, no long logs.
EOF

  BEFORE_HEAD=$(git rev-parse HEAD 2>/dev/null || echo none)
  BEFORE_DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

  set +e
  "$LAUNCHER" --print --no-session-persistence -p "$(cat "$PROMPT_FILE")" > "$RAW_OUTPUT" 2>&1 &
  CL_PID=$!
  WAITED=0
  CLAUDE_RC=0
  while kill -0 "$CL_PID" 2>/dev/null; do
    if [ "$WAITED" -ge "$ROUND_TIMEOUT" ]; then
      kill "$CL_PID" 2>/dev/null || true
      sleep 2
      kill -9 "$CL_PID" 2>/dev/null || true
      CLAUDE_RC=124
      break
    fi
    sleep 10
    WAITED=$((WAITED + 10))
  done
  if [ "$CLAUDE_RC" -ne 124 ]; then
    wait "$CL_PID"
    CLAUDE_RC=$?
  fi
  set -e

  tail -c "$MAX_LOG_BYTES" "$RAW_OUTPUT" | sed -E 's/sk-[A-Za-z0-9_-]{16,}/[REDACTED]/g' > "$OUTPUT"
  rm -f "$RAW_OUTPUT" "$PROMPT_FILE"

  # trust boundary: refuse to continue if the shared mainline picked up injection markers
  INJ_MAINLINE=$(grep -iE 'ignore (all )?(previous|prior|above) instructions|disregard (all )?(previous|prior)|system prompt|忽略(以上|之前|先前)' "$MAINLINE" 2>/dev/null | head -2 || true)
  if [ -n "$INJ_MAINLINE" ]; then
    STATUS="注入告警停机"
    printf '%s\n' 'injection markers found in mainline; stopping night shift:' >&2
    printf '%s\n' "$INJ_MAINLINE" >&2
    exit 1
  fi

  AFTER_HEAD=$(git rev-parse HEAD 2>/dev/null || echo none)
  AFTER_DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  if [ "$AFTER_HEAD" != "$BEFORE_HEAD" ] || [ "$AFTER_DIRTY" != "$BEFORE_DIRTY" ]; then
    INCREMENT=1
  else
    INCREMENT=0
  fi

  MARKER=$(grep -E '^(CONTINUE|DONE|BLOCKED)$' "$OUTPUT" | tail -n 1 || true)
  COMMIT=$(git rev-parse --short HEAD 2>/dev/null || printf 'none')

  {
    printf 'round=%s marker=%s increment=%s claude_exit=%s commit=%s\n' \
      "$ROUND" "${MARKER:-MISSING}" "$INCREMENT" "$CLAUDE_RC" "$COMMIT"
    printf 'log: %s\n' "$OUTPUT"
  } > "$ROUNDS_DIR/round-$ROUND.md"

  if [ "$CLAUDE_RC" -ne 0 ]; then
    TRANSPORT_FAILURES=$((TRANSPORT_FAILURES + 1))
    STALL=$((STALL + 1))
    if [ "$TRANSPORT_FAILURES" -ge 3 ]; then
      STATUS="传输故障"
      append_stall_decision "$STALL"
      die 'three consecutive Claude transport failures; stall decision written to mainline'
    fi
    sleep "$SLEEP_SECONDS"
    continue
  fi
  TRANSPORT_FAILURES=0

  if [ "$INCREMENT" -eq 1 ] && [ -n "$MARKER" ]; then
    STALL=0
  else
    STALL=$((STALL + 1))
  fi
  if [ "$STALL" -ge "$MAX_STALL" ]; then
    STATUS="停滞停机"
    append_stall_decision "$STALL"
    die "night shift stalled: $STALL consecutive rounds without increment; decision item written to mainline"
  fi

  case "$MARKER" in
    DONE)
      STATUS="任务完成"
      echo "autopilot: current task done (round $ROUND)"
      exit 0
      ;;
    BLOCKED)
      STATUS="需决策阻塞"
      echo "autopilot: night shift blocked at round $ROUND (decision item in mainline); see $OUTPUT"
      exit 0
      ;;
    *)
      ;;
  esac
  sleep "$SLEEP_SECONDS"
done
