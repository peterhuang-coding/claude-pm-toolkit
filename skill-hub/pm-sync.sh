#!/bin/sh
# pm-sync.sh — 把本机 skill 集同步进 ~/claude-pm-toolkit 仓库，可选推送 GitHub
# 用法: pm-sync.sh [--push]
# 原则：本地是源，GitHub 是镜像；密钥/使用统计不入仓库
set -eu

TOOLKIT=${TOOLKIT_REPO:-"$HOME/claude-pm-toolkit"}
CLAUDE_SKILLS="$HOME/.claude/skills"
COMMANDS="$HOME/.claude/commands"
HUB="$HOME/skill-hub"
PROXY="socks5h://127.0.0.1:7897"
# 有独立仓库的 skill 不同步
EXCLUDE="pm-orchestrator"
PUSH=0
[ "${1:-}" = "--push" ] && PUSH=1

[ -d "$TOOLKIT/.git" ] || { echo "pm-sync: toolkit repo not found: $TOOLKIT" >&2; exit 1; }

# 1. skills（仅真实目录；symlink、独立仓库、.bak 跳过）
mkdir -p "$TOOLKIT/skills"
for p in "$CLAUDE_SKILLS"/*/; do
  name=$(basename "$p")
  [ -d "$p" ] || continue
  [ -L "$CLAUDE_SKILLS/$name" ] && continue
  case " $EXCLUDE " in
    *" $name "*) continue ;;
  esac
  [ -f "$p/SKILL.md" ] || continue
  rm -rf "$TOOLKIT/skills/$name"
  cp -R "$p" "$TOOLKIT/skills/$name"
done
for p in "$TOOLKIT/skills"/*/; do
  name=$(basename "$p")
  [ -d "$CLAUDE_SKILLS/$name" ] || rm -rf "$TOOLKIT/skills/$name"
done

# 2. commands（不含 .bak）
mkdir -p "$TOOLKIT/commands"
for f in "$COMMANDS"/*.md; do
  case "$(basename "$f")" in
    *.bak*) continue ;;
  esac
  cp "$f" "$TOOLKIT/commands/"
done
for f in "$TOOLKIT/commands"/*.md; do
  name=$(basename "$f")
  [ -f "$COMMANDS/$name" ] || rm -f "$TOOLKIT/commands/$name"
done

# 3. hub 服务端 + 模板 + 分类数据
cp "$HUB/server.py" "$HUB/dashboard.html" "$HUB/serve.sh" "$HUB/categories.json" "$HUB/pm-sync.sh" "$TOOLKIT/skill-hub/"
[ -f "$HUB/com.petermini.skillhub.plist" ] && cp "$HUB/com.petermini.skillhub.plist" "$TOOLKIT/skill-hub/"
rm -rf "$TOOLKIT/skill-hub/templates"
[ -d "$HUB/templates" ] && cp -R "$HUB/templates" "$TOOLKIT/skill-hub/templates"

# 4. pm-guard
cp "$CLAUDE_SKILLS/pm-orchestrator/scripts/pm-guard.py" "$TOOLKIT/pm-guard/"

cd "$TOOLKIT"
git add -A
if git diff --cached --quiet; then
  echo "pm-sync: no changes"
else
  git -c user.email=peterhuang-coding@users.noreply.github.com -c user.name=peterhuang-coding \
    commit -qm "sync: $(date '+%Y-%m-%d %H:%M') local skill set update"
  echo "pm-sync: committed"
fi
if [ "$PUSH" -eq 1 ]; then
  git -c http.proxy="$PROXY" -c https.proxy="$PROXY" push -q origin main \
    && echo "pm-sync: pushed"
fi
