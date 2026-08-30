#!/usr/bin/env python3
"""skill-hub — local web UI to inspect and curate all Claude Code skills.

Serves http://127.0.0.1:3458. Run with:
    sh ~/skill-hub/serve.sh
or
    python3 -m uvicorn server:app --host 127.0.0.1 --port 3458
(cwd must be ~/skill-hub)
"""
import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

HOME = Path.home()
HUB = Path(__file__).resolve().parent
SKILLS_DIR = HOME / ".claude" / "skills"
AGENTS_SRC = HOME / ".agents" / "skills"
COMMANDS_DIR = HOME / ".claude" / "commands"
AGENTS_DIR = HOME / ".claude" / "agents"
PLUGIN_CACHE = HOME / ".claude" / "plugins" / "cache"
ARCHIVE = HUB / "archive"
CATEGORIES_FILE = HUB / "categories.json"
TEMPLATES_DIR = HUB / "templates"
USAGE_FILE = HOME / ".claude" / "skill-usage.jsonl"
LLM_HUB = HOME / "llm-hub"
PM_PROVIDER = HOME / ".claude" / "skills" / "pm-orchestrator" / "scripts" / "pm-provider.py"

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

app = FastAPI(title="skill-hub")


class CreateBody(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    template: str = ""


class TemplateBody(BaseModel):
    name: str
    template_name: str


class SaveBody(BaseModel):
    content: str


class CopyBody(BaseModel):
    new_name: str


class MetaBody(BaseModel):
    name: str
    category: str = ""
    nature: str = ""


# ---------- frontmatter ----------

def parse_frontmatter(text: str) -> dict:
    """Parse a minimal SKILL.md frontmatter block (--- ... ---)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = text[3:end]
    meta: dict = {}
    cur_key = None
    buf: list = []
    for line in fm.splitlines():
        if cur_key and (line[:1] in (" ", "\t") or line.strip() == ""):
            if line.strip():
                buf.append(line.strip())
            continue
        if cur_key and buf:
            meta[cur_key] = " ".join(buf)
            buf = []
        cur_key = None
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and ":" in stripped:
            key, val = stripped.split(":", 1)
            meta[key.strip()] = val.strip()
            cur_key = key.strip()
    if cur_key and buf:
        meta[cur_key] = " ".join(buf)
    return meta


def is_bak(name: str) -> bool:
    return ".bak" in name


def quality_issues(it: dict) -> list:
    issues = []
    desc = (it.get("description") or "").strip()
    if not desc:
        issues.append("无描述")
    elif len(desc) < 15:
        issues.append("描述过短")
    low = desc.lower()
    if not any(k in low for k in ("use when", "when the user", "when you", "触发", "用于", "适用于")):
        issues.append("缺触发词")
    if (it.get("size") or 0) > 20000:
        issues.append("体积过大")
    return issues


def cheap_label(it: dict) -> str:
    if it.get("nature") == "pe" and (it.get("size") or 0) < 8000:
        return "省"
    if it.get("nature") == "api" or (it.get("size") or 0) > 20000:
        return "贵"
    return "中"


def load_usage() -> dict:
    """Aggregate ~/.claude/skill-usage.jsonl into {name: {total, last7d, last30d, last_used}}."""
    agg = {}
    if not USAGE_FILE.is_file():
        return agg
    now = time.time()
    try:
        for line in USAGE_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            name = str(rec.get("skill", "")).strip()
            if not name:
                continue
            a = agg.setdefault(name, {"total": 0, "last7d": 0, "last30d": 0, "last_used": 0})
            a["total"] += 1
            ts = rec.get("ts", 0)
            if isinstance(ts, (int, float)) and now - ts < 7 * 86400:
                a["last7d"] += 1
            if isinstance(ts, (int, float)) and now - ts < 30 * 86400:
                a["last30d"] += 1
            if isinstance(ts, (int, float)) and ts > a["last_used"]:
                a["last_used"] = ts
    except OSError:
        pass
    return agg


# ---------- taxonomy (category + nature) ----------

def _taxonomy() -> dict:
    if CATEGORIES_FILE.is_file():
        try:
            return json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"categories": {}, "natures": {}, "skills": {}}


def tax_for(name: str, raw_meta: dict = None) -> tuple:
    t = _taxonomy()
    entry = t.get("skills", {}).get(name, {})
    entry = entry if isinstance(entry, dict) else {}
    fm_cat = str((raw_meta or {}).get("category", "")) if isinstance(raw_meta, dict) else ""
    cat = fm_cat or entry.get("category", "")
    nat = entry.get("nature", "")
    cats = t.get("categories", {})
    nats = t.get("natures", {})
    return cat, cats.get(cat, cat), nat, nats.get(nat, "")


def upsert_frontmatter_field(text: str, key: str, value: str) -> str:
    """Set (or remove when value='') a top-level field in a SKILL.md frontmatter block."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    lines = text[3:end].splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith(key + ":"):
            i += 1
            while i < len(lines) and lines[i][:1] in (" ", "\t"):
                i += 1
            continue
        out.append(line)
        i += 1
    if value:
        out.append(f"{key}: {value}")
    return "---\n" + "\n".join(out).rstrip() + "\n---" + text[end + 4:]


def taxonomy_counts(skills: list) -> tuple:
    t = _taxonomy()
    cats = t.get("categories", {})
    nats = t.get("natures", {})
    cat_count, nat_count = {}, {}
    for s in skills:
        c = s.get("category", "")
        k = s.get("nature", "")
        cat_count[c] = cat_count.get(c, 0) + 1
        nat_count[k] = nat_count.get(k, 0) + 1
    cat_out = [{"key": k, "label": v, "count": cat_count.get(k, 0)} for k, v in cats.items()]
    cat_out.append({"key": "", "label": "未分类", "count": cat_count.get("", 0)})
    nat_out = [{"key": k, "label": v, "count": nat_count.get(k, 0)} for k, v in nats.items()]
    nat_out.append({"key": "", "label": "未标注", "count": nat_count.get("", 0)})
    return cat_out, nat_out


def md_meta(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    fm = parse_frontmatter(text)
    try:
        st = path.stat()
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    return {
        "description": (fm.get("description") or "")[:200],
        "size": size,
        "mtime": mtime,
        "has_frontmatter": bool(fm),
        "raw_meta": {k: str(v)[:300] for k, v in fm.items()},
    }


# ---------- scanning ----------

def _mounted_names() -> set:
    names = set()
    if not SKILLS_DIR.is_dir():
        return names
    for p in SKILLS_DIR.iterdir():
        if p.name.startswith("."):
            continue
        if p.is_dir() or p.is_symlink():
            names.add(p.name)
    return names


def scan_skills() -> list:
    items = []
    mounted = _mounted_names()

    # 1. entries under ~/.claude/skills (real dirs and symlinks)
    if SKILLS_DIR.is_dir():
        for p in sorted(SKILLS_DIR.iterdir()):
            if p.name.startswith("."):
                continue
            if not (p.is_dir() or p.is_symlink()):
                continue
            is_link = p.is_symlink()
            target = ""
            try:
                target = str(p.resolve())
            except OSError:
                pass
            broken = is_link and not p.exists()
            if is_link and not broken:
                if target.startswith(str(AGENTS_SRC)):
                    source_label = "agents-src"
                elif target.startswith(str(PLUGIN_CACHE)):
                    source_label = "plugin"
                else:
                    source_label = "other"
            else:
                source_label = "user"
            meta = md_meta(p / "SKILL.md")
            items.append({
                "group": "skill",
                "name": p.name,
                "kind": "symlink" if is_link else "user",
                "source": source_label,
                "path": str(p),
                "target": target,
                "broken": broken,
                "mounted": True,
                "editable": (not is_link) and not broken,
                "archivable": (not is_link) and not broken,
                **meta,
            })

    # 2. skills in ~/.agents/skills that are not mounted
    if AGENTS_SRC.is_dir():
        for p in sorted(AGENTS_SRC.iterdir()):
            if p.name.startswith(".") or not p.is_dir():
                continue
            if p.name in mounted:
                continue
            meta = md_meta(p / "SKILL.md")
            items.append({
                "group": "dormant",
                "name": p.name,
                "kind": "src",
                "source": "agents-src",
                "path": str(p),
                "target": "",
                "broken": False,
                "mounted": False,
                "editable": False,
                "archivable": False,
                **meta,
            })

    # 3. plugin-provided skills (managed by the plugin system, read-only)
    if PLUGIN_CACHE.is_dir():
        for org in PLUGIN_CACHE.iterdir():
            if not org.is_dir():
                continue
            for repo in org.iterdir():
                if not repo.is_dir():
                    continue
                for ver in repo.iterdir():
                    skills_root = ver / "skills"
                    if not skills_root.is_dir():
                        continue
                    for p in sorted(skills_root.iterdir()):
                        if not p.is_dir() or not (p / "SKILL.md").is_file():
                            continue
                        meta = md_meta(p / "SKILL.md")
                        items.append({
                            "group": "plugin",
                            "name": p.name,
                            "kind": "plugin",
                            "source": f"plugin:{org.name}/{repo.name}",
                            "path": str(p),
                            "target": "",
                            "broken": False,
                            "mounted": True,
                            "editable": False,
                            "archivable": False,
                            **meta,
                        })
    usage = load_usage()
    for it in items:
        it["category"], it["category_label"], it["nature"], it["nature_label"] = tax_for(it["name"], it.get("raw_meta"))
        u = usage.get(it["name"], {})
        it["usage_total"] = u.get("total", 0)
        it["usage_30d"] = u.get("last30d", 0)
        it["last_used"] = u.get("last_used", 0)
        it["quality_issues"] = quality_issues(it)
        it["cheap"] = cheap_label(it)
    items.sort(key=lambda s: s["name"])
    return items


def scan_md_dir(directory: Path, group: str, kind: str) -> list:
    items = []
    if not directory.is_dir():
        return items
    for p in sorted(directory.iterdir()):
        if not p.is_file() or not p.name.endswith(".md") or is_bak(p.name):
            continue
        meta = md_meta(p)
        items.append({
            "group": group,
            "name": p.stem,
            "kind": kind,
            "source": "user",
            "path": str(p),
            "target": "",
            "broken": False,
            "mounted": True,
            "editable": False,
            "archivable": False,
            **meta,
        })
    return items


def scan_commands() -> list:
    return scan_md_dir(COMMANDS_DIR, "command", "command")


def scan_agents() -> list:
    return scan_md_dir(AGENTS_DIR, "agent", "agent")


# ---------- health ----------

def build_health(skills: list) -> list:
    health = []
    # broken symlinks
    broken = [s for s in skills if s.get("broken")]
    if broken:
        health.append({
            "level": "error",
            "title": f"{len(broken)} 个断开的挂载链接",
            "detail": "symlink 指向的目标已不存在，Claude Code 会忽略这些技能。",
            "paths": [s["path"] for s in broken],
        })
    # duplicate names across sources
    by_name: dict = {}
    for s in skills:
        by_name.setdefault(s["name"], []).append(s)
    dup_names = {n: v for n, v in by_name.items() if len(v) > 1}
    if dup_names:
        health.append({
            "level": "warning",
            "title": f"{len(dup_names)} 个重名技能",
            "detail": "同一名称出现在多个来源（如本地与插件），触发词会互相竞争。",
            "paths": [f'{s["name"]} @ {s["source"]}' for v in dup_names.values() for s in v],
        })
    # duplicate descriptions (identical content, different names)
    by_desc: dict = {}
    for s in skills:
        d = (s.get("description") or "").strip().lower()
        if len(d) > 30:
            by_desc.setdefault(d, []).append(s["name"])
    dup_desc = {d: v for d, v in by_desc.items() if len(v) > 1}
    if dup_desc:
        for d, names in dup_desc.items():
            health.append({
                "level": "warning",
                "title": "描述逐字相同的技能：" + "、".join(names),
                "detail": "可能是同一技能的重复安装（如同一文件走了两条安装路径）。",
                "paths": [f"{n}: {d[:120]}" for n in names],
            })
    # skills without SKILL.md
    missing = [s for s in skills if s["group"] == "skill" and not s.get("has_frontmatter") and not s.get("broken")]
    if missing:
        health.append({
            "level": "warning",
            "title": f"{len(missing)} 个技能目录缺少 SKILL.md",
            "detail": "目录存在但没有可解析的 SKILL.md，不会被 Claude Code 加载。",
            "paths": [s["path"] for s in missing],
        })
    # .bak files in commands/agents
    baks = []
    for d in (COMMANDS_DIR, AGENTS_DIR):
        if d.is_dir():
            baks += [str(p) for p in d.iterdir() if p.is_file() and is_bak(p.name)]
    if baks:
        health.append({
            "level": "warning",
            "title": f"{len(baks)} 个 .bak 备份文件",
            "detail": "散落的历史备份，建议归档或删除。",
            "paths": baks,
        })
    # dormant count
    dormant = [s for s in skills if s["group"] == "dormant"]
    if dormant:
        health.append({
            "level": "info",
            "title": f"{len(dormant)} 个未挂载技能（~/.agents/skills）",
            "detail": "磁盘上存在但 Claude Code 当前不可见；可在 Skills 页挂载或归档。",
            "paths": [s["name"] for s in dormant[:30]],
        })
    # agents that write docs but lack Write tool
    for name in ("doc-agent", "dev-agent"):
        agent_dir = AGENTS_DIR / f"{name}.md"
        if not agent_dir.is_file():
            continue
        fm = parse_frontmatter(agent_dir.read_text(encoding="utf-8", errors="replace"))
        tools = fm.get("tools", "")
        if "Write" not in tools:
            health.append({
                "level": "info",
                "title": f"{name} 缺少 Write 工具",
                "detail": f"tools 为「{tools}」，写文件只能靠 Bash；建议补充 Write/Edit。",
                "paths": [str(agent_dir)],
            })
    return health


# ---------- helpers ----------

def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def resolve_skill(group: str, name: str) -> Path:
    if not NAME_RE.match(name):
        raise HTTPException(400, f"非法名称: {name}")
    if group == "skill":
        p = SKILLS_DIR / name
    elif group == "dormant":
        p = AGENTS_SRC / name
    elif group == "plugin":
        # find plugin skill by name
        if PLUGIN_CACHE.is_dir():
            for cand in PLUGIN_CACHE.rglob(f"skills/{name}/SKILL.md"):
                return cand.parent
        raise HTTPException(404, f"插件技能不存在: {name}")
    else:
        raise HTTPException(404, f"未知分组: {group}")
    if not p.exists():
        raise HTTPException(404, f"技能不存在: {group}/{name}")
    return p


# ---------- API ----------

@app.get("/")
def index():
    return FileResponse(HUB / "dashboard.html")


@app.get("/api/overview")
def overview():
    skills = scan_skills()
    commands = scan_commands()
    agents = scan_agents()
    counts = {
        "mounted_user": len([s for s in skills if s["group"] == "skill" and s["kind"] == "user"]),
        "mounted_symlink": len([s for s in skills if s["group"] == "skill" and s["kind"] == "symlink"]),
        "dormant": len([s for s in skills if s["group"] == "dormant"]),
        "plugin": len([s for s in skills if s["group"] == "plugin"]),
        "commands": len(commands),
        "agents": len(agents),
        "health_error": len([h for h in build_health(skills) if h["level"] == "error"]),
        "health_warning": len([h for h in build_health(skills) if h["level"] == "warning"]),
    }
    cats, nats = taxonomy_counts(skills)
    usage_rank = [{"name": s["name"], "total": s["usage_total"], "last30d": s["usage_30d"]} for s in skills if s["usage_total"] > 0]
    usage_rank.sort(key=lambda x: -x["total"])
    unused_api = [s["name"] for s in skills if s["usage_total"] == 0 and s["nature"] == "api"]
    unused_api.sort()
    fix_candidates = [s for s in skills if s["group"] != "plugin" and s.get("quality_issues")]
    fix_candidates.sort(key=lambda s: (-len(s["quality_issues"]), -(s.get("size") or 0)))
    fix_list = [{"name": s["name"], "issues": s["quality_issues"], "cheap": s["cheap"], "usage": s["usage_total"]} for s in fix_candidates[:3]]
    return {"counts": counts, "health": build_health(skills), "categories": cats, "natures": nats,
            "usage_rank": usage_rank[:10], "unused_api": unused_api, "fix_list": fix_list, "generated_at": now_ts()}


@app.get("/api/skills")
def list_skills():
    return {"items": scan_skills()}


@app.get("/api/skills/{group}/{name}")
def get_skill(group: str, name: str):
    p = resolve_skill(group, name)
    md = p / "SKILL.md"
    content = md.read_text(encoding="utf-8", errors="replace") if md.is_file() else ""
    return {"name": name, "group": group, "path": str(md), "content": content}


@app.get("/api/templates")
def list_templates():
    out = []
    if not TEMPLATES_DIR.is_dir():
        return {"templates": out}
    for p in sorted(TEMPLATES_DIR.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        body = text[text.find("\n---", 3) + 4:].strip() if text.startswith("---") else text
        out.append({
            "name": p.stem,
            "title": (fm.get("name") or p.stem)[:80],
            "preview": body[:120],
        })
    return {"templates": out}


@app.post("/api/templates")
def save_template(body: TemplateBody):
    if not NAME_RE.match(body.template_name):
        raise HTTPException(400, f"非法模板名: {body.template_name}")
    p = resolve_skill("skill", body.name)
    md = p / "SKILL.md"
    if not md.is_file():
        raise HTTPException(400, f"技能没有 SKILL.md: {body.name}")
    content = md.read_text(encoding="utf-8", errors="replace")
    content = upsert_frontmatter_field(content, "name", "{{name}}")
    content = upsert_frontmatter_field(content, "description", "{{description}}")
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    (TEMPLATES_DIR / f"{body.template_name}.md").write_text(content, encoding="utf-8")
    return {"ok": True, "template": body.template_name}


@app.post("/api/skills")
def create_skill(body: CreateBody):
    name = body.name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, f"非法名称: {name}（仅字母/数字/连字符/下划线）")
    dest = SKILLS_DIR / name
    if dest.exists():
        raise HTTPException(400, f"技能已存在: {name}")
    dest.mkdir(parents=True, exist_ok=True)
    if body.template:
        if not NAME_RE.match(body.template):
            raise HTTPException(400, f"非法模板名: {body.template}")
        tp = TEMPLATES_DIR / f"{body.template}.md"
        if not tp.is_file():
            raise HTTPException(400, f"模板不存在: {body.template}")
        content = tp.read_text(encoding="utf-8", errors="replace")
        content = content.replace("{{name}}", name).replace("{{description}}", body.description or "")
        content = upsert_frontmatter_field(content, "name", name)
        content = upsert_frontmatter_field(content, "description", body.description)
        final = content + "\n"
    else:
        content = body.content.strip("\n")
        if content.startswith("---"):
            final = content + "\n"
        else:
            desc = body.description.strip().replace("\n", " ")
            final = f"---\nname: {name}\ndescription: {desc}\n---\n\n{content}\n"
    (dest / "SKILL.md").write_text(final, encoding="utf-8")
    return {"ok": True, "name": name, "path": str(dest / "SKILL.md")}


@app.put("/api/skills/{group}/{name}")
def save_skill(group: str, name: str, body: SaveBody):
    p = resolve_skill(group, name)
    if group != "skill" or p.is_symlink():
        raise HTTPException(400, "上游管理的技能不可直接编辑；请使用「复制为自己的」")
    (p / "SKILL.md").write_text(body.content, encoding="utf-8")
    return {"ok": True, "name": name}


@app.post("/api/skills/{group}/{name}/toggle")
def toggle_skill(group: str, name: str):
    if group == "dormant":
        src = AGENTS_SRC / name
        if not (src / "SKILL.md").is_file():
            raise HTTPException(400, f"源技能不存在: {name}")
        link = SKILLS_DIR / name
        if link.exists():
            raise HTTPException(400, f"挂载位置已被占用: {name}")
        link.symlink_to(src)
        return {"ok": True, "action": "mounted", "name": name}
    if group == "skill":
        link = SKILLS_DIR / name
        if link.is_symlink():
            link.unlink()
            return {"ok": True, "action": "unmounted", "name": name}
        raise HTTPException(400, "本目录技能请使用「归档」而不是卸载")
    raise HTTPException(400, "该技能由插件管理，请在插件设置中启停")


@app.post("/api/skills/{group}/{name}/copy")
def copy_skill(group: str, name: str, body: CopyBody):
    new_name = body.new_name.strip()
    if not NAME_RE.match(new_name):
        raise HTTPException(400, f"非法名称: {new_name}")
    dest = SKILLS_DIR / new_name
    if dest.exists():
        raise HTTPException(400, f"技能已存在: {new_name}")
    p = resolve_skill(group, name)
    content = (p / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    if new_name != name and content.startswith("---"):
        content = re.sub(r"(?m)^name:\s*.*$", f"name: {new_name}", content, count=1)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(content, encoding="utf-8")
    return {"ok": True, "name": new_name, "path": str(dest / "SKILL.md")}


@app.post("/api/skills/{group}/{name}/archive")
def archive_skill(group: str, name: str):
    if group != "skill":
        raise HTTPException(400, "只能归档本目录技能")
    p = SKILLS_DIR / name
    if not p.is_dir() or p.is_symlink():
        raise HTTPException(400, "只能归档真实目录（symlink 请用卸载）")
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE / f"{now_ts()}-{name}"
    shutil.move(str(p), str(dest))
    return {"ok": True, "archived_to": str(dest)}


@app.get("/api/commands")
def list_commands():
    return {"items": scan_commands()}


@app.get("/api/commands/{name}")
def get_command(name: str):
    if not NAME_RE.match(name):
        raise HTTPException(400, f"非法名称: {name}")
    p = COMMANDS_DIR / f"{name}.md"
    if not p.is_file():
        raise HTTPException(404, f"命令不存在: {name}")
    return {"name": name, "path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/agents")
def list_agents():
    return {"items": scan_agents()}


@app.get("/api/agents/{name}")
def get_agent(name: str):
    if not NAME_RE.match(name):
        raise HTTPException(400, f"非法名称: {name}")
    p = AGENTS_DIR / f"{name}.md"
    if not p.is_file():
        raise HTTPException(404, f"Agent 不存在: {name}")
    return {"name": name, "path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")}


@app.post("/api/sync")
def sync():
    script = HUB / "pm-sync.sh"
    if not script.is_file():
        raise HTTPException(400, "pm-sync.sh 不存在")
    try:
        r = subprocess.run(
            ["sh", str(script), "--push"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "同步超时（300 秒）")
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-2000:]}


def _norm_sid(v: str) -> str:
    if v.endswith(".jsonl"):
        return Path(v).stem
    return v.split("/")[-1]


def session_process_flags() -> tuple:
    """返回 (sid -> {"fork":bool,"agent":bool}, 后台任务目录前缀集合)。"""
    flags, bg = {}, set()
    try:
        out_proc = subprocess.run(
            ["ps", "-Ao", "args"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return flags, bg
    for line in out_proc.splitlines():
        fk = "--fork-session" in line
        ag = bool(re.search(r"--agent(?:\s|$)", line))
        sids = [m.group(1) for m in re.finditer(r"--session-id\s+(\S+)", line)]
        if not sids:
            sids = [m.group(1) for m in re.finditer(r"--resume\s+(\S+)", line)]
        for raw in sids:
            sid = _norm_sid(raw)
            fl = flags.setdefault(sid, {})
            if fk:
                fl["fork"] = True
            if ag:
                fl["agent"] = True
        for m in re.finditer(r"\.claude/jobs/(\w[\w-]{5,})", line):
            bg.add(m.group(1))
    return flags, bg


def _llm_read(name: str) -> dict:
    p = LLM_HUB / name
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            pass
    return {}


def _llm_write(name: str, data: dict) -> None:
    (LLM_HUB / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/providers")
def list_providers():
    prov = _llm_read("providers.json")
    state = _llm_read("state.json")
    merged = []
    for pid, meta in prov.get("providers", {}).items():
        if not isinstance(meta, dict):
            continue
        h = state.get("providers", {}).get(pid, {})
        merged.append({
            "id": pid,
            "name": meta.get("name", pid),
            "tier": h.get("tier") or meta.get("tier", ""),
            "type": meta.get("type", ""),
            "models": meta.get("models", []),
            "base_url": meta.get("api_base_url", ""),
            "enabled": bool(meta.get("enabled", True)),
            "key_present": bool(h.get("key_present", False)),
            "error_rate": h.get("recent_error_rate"),
            "p50": h.get("recent_p50_ms"),
            "p95": h.get("recent_p95_ms"),
            "last_error": h.get("last_error"),
            "failures": h.get("consecutive_failures", 0),
            "daily_spend": h.get("daily_spend_usd", 0.0),
            "daily_budget": h.get("daily_budget_usd"),
            "quota_until": h.get("quota_exhausted_until", 0),
            "circuit_until": h.get("circuit_open_until", 0),
        })
    merged.sort(key=lambda x: (0 if x["tier"] == "plan" else 1, x["id"]))
    router = {}
    if PM_PROVIDER.is_file():
        try:
            r = subprocess.run(
                ["python3", str(PM_PROVIDER), "status", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                router = json.loads(r.stdout) if r.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return {
        "strategy": state.get("strategy") or prov.get("strategy", ""),
        "providers": merged,
        "router": {
            "active": router.get("active"),
            "mode": router.get("mode"),
            "current_provider": router.get("current_provider"),
            "cooldowns": router.get("cooldowns", {}),
        },
    }


class ProviderUseBody(BaseModel):
    mode: str


@app.post("/api/providers/use")
def provider_use(body: ProviderUseBody):
    mode = body.mode.strip()
    if not mode:
        raise HTTPException(400, "缺少 mode")
    if not PM_PROVIDER.is_file():
        raise HTTPException(400, "pm-provider.py 不存在")
    r = subprocess.run(
        ["python3", str(PM_PROVIDER), "use", mode],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise HTTPException(400, (r.stderr or r.stdout or "切换失败")[-300:])
    return {"ok": True, "mode": mode, "output": (r.stdout or "")[-200:]}


class ProviderStrategyBody(BaseModel):
    strategy: str


@app.post("/api/providers/strategy")
def provider_strategy(body: ProviderStrategyBody):
    if body.strategy not in ("plan-then-payg", "plan-only", "payg-only"):
        raise HTTPException(400, f"未知策略: {body.strategy}")
    prov = _llm_read("providers.json")
    state = _llm_read("state.json")
    prov["strategy"] = body.strategy
    state["strategy"] = body.strategy
    _llm_write("providers.json", prov)
    _llm_write("state.json", state)
    if PM_PROVIDER.is_file():
        subprocess.run(
            ["python3", str(PM_PROVIDER), "ensure"],
            capture_output=True, text=True, timeout=30,
        )
    return {"ok": True, "strategy": body.strategy}


class ProviderAddBody(BaseModel):
    id: str
    key: str
    tier: str = "payg"
    base_url: str = ""
    model: str = ""
    type: str = "openai"


@app.post("/api/providers/add")
def provider_add(body: ProviderAddBody):
    pid = body.id.strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,31}$", pid):
        raise HTTPException(400, f"非法 provider id: {pid}")
    if not body.key.strip():
        raise HTTPException(400, "API key 为空")
    prov = _llm_read("providers.json")
    if pid in prov.get("providers", {}):
        raise HTTPException(400, f"provider 已存在: {pid}")
    # 1. key 进 Keychain（service=llm-hub, account=<id>）
    r = subprocess.run(
        ["security", "add-generic-password", "-s", "llm-hub", "-a", pid,
         "-w", body.key.strip(), "-U"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise HTTPException(400, f"Keychain 写入失败: {(r.stderr or '')[-200:]}")
    # 2. providers.json 注册
    ptype = body.type if body.type in ("openai", "anthropic") else "openai"
    prov.setdefault("providers", {})[pid] = {
        "name": pid,
        "tier": body.tier if body.tier in ("plan", "payg") else "payg",
        "type": ptype,
        "api_base_url": body.base_url.strip(),
        "keychain_account": pid,
        "models": [body.model.strip()] if body.model.strip() else [],
        "transformer": "Anthropic" if ptype == "anthropic" else "OpenAI",
        "enabled": True,
    }
    prov["updated_at"] = now_ts()
    _llm_write("providers.json", prov)
    # 3. state.json 初始化健康条目
    state = _llm_read("state.json")
    state.setdefault("providers", {})[pid] = {
        "enabled": True,
        "key_present": True,
        "tier": body.tier if body.tier in ("plan", "payg") else "payg",
        "daily_spend_usd": 0.0,
        "daily_budget_usd": None,
        "recent_error_rate": 0.0,
        "consecutive_failures": 0,
        "last_error": None,
        "quota_exhausted_until": 0,
        "circuit_open_until": 0,
    }
    state["ts"] = int(time.time())
    _llm_write("state.json", state)
    return {"ok": True, "id": pid, "note": "已注册。key 仅存 Keychain，不回显。CCR 路由下次重启/探测后生效。"}


KNOWN_SERVICES = {
    "3458": ("skill-hub 总控台", "http://127.0.0.1:3458"),
    "3456": ("CCR 模型路由", "http://127.0.0.1:3456"),
    "3457": ("llm-hub 仪表盘", "http://127.0.0.1:3457"),
    "8787": ("tastegraph 视觉采样", "http://127.0.0.1:8787"),
    "8767": ("amsterdam 地图原型", "http://127.0.0.1:8767"),
    "8768": ("jp-us-arb 机票 API", "http://127.0.0.1:8768"),
    "8769": ("jp-us-arb 机票 API 2", "http://127.0.0.1:8769"),
    "56400": ("moodboard 可视化", "http://127.0.0.1:56400"),
    "7897": ("Clash Verge 代理", "socks5://127.0.0.1:7897"),
    "33331": ("Clash 控制端口", "http://127.0.0.1:33331"),
    "10000": ("网盘服务", "http://127.0.0.1:10000"),
    "9010": ("LG Hub", "http://127.0.0.1:9010"),
}
SKIP_SERVICES = {
    "launchd", "ControlCe", "ARDAgent", "rapportd", "distnoted", "WiFi",
    "AirPort", "sharingd", "CalendarA", "Sidecar", "universalaccessd",
    "coreaudiod", "biometrickitd", "identityservicesd", "remoted",
    # 桌面应用内部端口（不是 web 服务）
    "WeChat", "Spotify", "Electron", "Doubao",
}


def infer_service(port: str, cmd: str, args: str) -> tuple:
    if port in KNOWN_SERVICES:
        return KNOWN_SERVICES[port]
    m = re.search(r"uvicorn\s+([\w.]+)(?::app)?", args)
    if m:
        return f"uvicorn: {m.group(1)}", f"http://127.0.0.1:{port}"
    m = re.search(r"http\.server\s+\d+.*?--directory\s+([\"'])([^\"']+)\1", args)
    if m:
        return f"静态站点: {Path(m.group(2)).name}", f"http://127.0.0.1:{port}"
    if "python" in cmd and ("http.server" in args or "http_server" in args):
        return f"{cmd} http 服务", f"http://127.0.0.1:{port}"
    if cmd == "node" or "node" in args:
        return "node 服务", f"http://127.0.0.1:{port}"
    return f"{cmd} 进程", f"http://127.0.0.1:{port}"


@app.get("/api/services")
def list_services():
    out = []
    try:
        r = subprocess.run(
            ["/usr/sbin/lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"services": out, "error": str(e)}
    raw = r.stdout
    if not raw:
        return {"services": out, "error": f"lsof rc={r.returncode} stderr={r.stderr[:200]!r}"}
    seen = {}
    for line in raw.splitlines()[1:]:
        p = line.split()
        if len(p) < 9:
            continue
        cmd, pid, addr = p[0], p[1], p[8]
        if cmd in SKIP_SERVICES:
            continue
        if "claude" in cmd.lower() or "claude" in (p[-1] if len(p) > 9 else ""):
            continue
        m = re.match(r"^(\S+):(\d+)$", addr)
        if not m:
            continue
        host, port = m.group(1), m.group(2)
        if port in seen:
            continue
        args = ""
        try:
            args = subprocess.run(
                ["/bin/ps", "-p", pid, "-o", "args="],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()[:240]
        except (OSError, subprocess.TimeoutExpired):
            pass
        if "claude" in args.lower():
            continue
        name, url = infer_service(port, cmd, args)
        seen[port] = {
            "port": int(port),
            "host": "0.0.0.0" if host == "*" else host,
            "process": cmd,
            "pid": int(pid),
            "name": name,
            "url": url,
            "args": args,
        }
    return {"services": sorted(seen.values(), key=lambda x: x["port"])}


@app.get("/api/sessions")
def list_sessions():
    """断点恢复：扫描每个项目的会话记录，给出'停在哪'+恢复命令+主线任务+是否在跑。"""
    out = []
    projects_dir = HOME / ".claude" / "projects"
    if not projects_dir.is_dir():
        return {"sessions": out}
    running, bg_running = session_process_flags()
    for pdir in projects_dir.iterdir():
        if not pdir.is_dir():
            continue
        try:
            files = sorted(pdir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for f in files[:8]:
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size < 200:
                continue
            sid = f.stem
            cwd, last_user, last_assist, first_user = "", "", "", ""
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(32768)
                for line in head.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "user":
                        msg = obj.get("message")
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                txt = " ".join(
                                    str(b.get("text", "")) for b in content
                                    if isinstance(b, dict) and b.get("type") == "text"
                                )
                            else:
                                txt = str(content)
                            if txt.strip() and not txt.startswith("<"):
                                first_user = txt.strip()[:160]
                                break
            except OSError:
                pass
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    fh.seek(max(0, st.st_size - 65536))
                    tail = fh.read()
                for line in tail.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    c = obj.get("cwd")
                    if c:
                        cwd = str(c)
                    t = obj.get("type")
                    msg = obj.get("message")
                    if t == "user" and isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            txt = " ".join(
                                str(b.get("text", "")) for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        else:
                            txt = str(content)
                        if txt.strip() and not txt.startswith("<"):
                            last_user = txt.strip()[:200]
                    elif t == "assistant" and isinstance(msg, dict):
                        for b in msg.get("content", []) or []:
                            if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text", "")).strip():
                                last_assist = str(b["text"]).strip()[:200]
            except OSError:
                pass
            mainline_task = ""
            if cwd:
                ml = Path(cwd) / ".claude" / "autopilot" / "mainline.md"
                if ml.is_file():
                    try:
                        text = ml.read_text(encoding="utf-8", errors="replace")
                        m = re.search(r"##\s*当前任务\s*\n(.+?)(?=\n##|\Z)", text, re.S)
                        if m:
                            mainline_task = " ".join(m.group(1).split())[:120]
                    except OSError:
                        pass
            fl = running.get(sid, {})
            is_bg_job = "claude-jobs" in pdir.name
            out.append({
                "session_id": sid,
                "project": pdir.name,
                "cwd": cwd,
                "mtime": int(st.st_mtime),
                "size": st.st_size,
                "active": sid in running,
                "bg_active": any(sid.startswith(b) for b in bg_running),
                "fork": bool(fl.get("fork")),
                "agent": bool(fl.get("agent")),
                "bg_job": is_bg_job,
                "is_main": not fl.get("fork") and not fl.get("agent") and not is_bg_job,
                "first_user": first_user,
                "last_user": last_user,
                "last_assist": last_assist,
                "mainline_task": mainline_task,
                "resume": f'cd "{cwd}" && claude --resume {sid}' if cwd else f"claude --resume {sid}",
            })
    out.sort(key=lambda s: (-int(s["is_main"] and (s["active"] or s["bg_active"])),
                            -int(s["active"] or s["bg_active"]), -s["mtime"]))
    return {"sessions": out[:40]}


@app.get("/api/categories")
def list_categories():
    cats, nats = taxonomy_counts()
    return {"categories": cats, "natures": nats}


@app.post("/api/meta")
def set_meta(body: MetaBody):
    t = _taxonomy()
    known = {s["name"] for s in scan_skills()}
    if body.name not in known:
        raise HTTPException(404, f"技能不存在: {body.name}")
    skills = t.setdefault("skills", {})
    entry = skills.get(body.name, {})
    entry = entry if isinstance(entry, dict) else {}
    if body.category:
        if body.category not in t.get("categories", {}):
            raise HTTPException(400, f"未知分类: {body.category}")
        entry["category"] = body.category
    else:
        entry.pop("category", None)
    if body.nature:
        if body.nature not in t.get("natures", {}):
            raise HTTPException(400, f"未知性质: {body.nature}")
        entry["nature"] = body.nature
    else:
        entry.pop("nature", None)
    skills[body.name] = entry
    CATEGORIES_FILE.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    # persist category into the skill's own frontmatter when it is the user's editable skill
    p = SKILLS_DIR / body.name
    if p.is_dir() and not p.is_symlink() and (p / "SKILL.md").is_file():
        md = p / "SKILL.md"
        text = md.read_text(encoding="utf-8", errors="replace")
        updated = upsert_frontmatter_field(text, "category", body.category)
        if updated != text:
            md.write_text(updated, encoding="utf-8")
    return {"ok": True, "name": body.name, "category": entry.get("category", ""), "nature": entry.get("nature", "")}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=3458)
