#!/usr/bin/env python3
"""静态、只读的 Harness 盘点：产出 ObservedAsset JSON（标准库，单文件）。"""

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

# 候选路径数据表。来自本机观察，未按官方文档逐项核实；路径存在才记录。
# kind 取值：instructions / skill / hook / mcp / settings / rules / memory / extension / prompt / plugin。
# hook 目前没有独立候选路径（hooks 写在 settings.json 里），kind 留给后续切片。
# scope=user 的路径相对 --home，scope=project 的路径相对每个 --project。
# 项目级 Pi 只给出 `.pi/`，这里按用户级的同名子路径展开，kind 与用户级一致。
FILE_CANDIDATES = (
    ("claude", "user", "instructions", ".claude/CLAUDE.md"),
    ("claude", "user", "settings", ".claude/settings.json"),
    ("claude", "user", "settings", ".claude/settings.local.json"),
    ("claude", "project", "instructions", "CLAUDE.md"),
    ("claude", "project", "instructions", "CLAUDE.local.md"),
    ("claude", "project", "instructions", "AGENTS.md"),
    ("claude", "project", "settings", ".claude/settings.json"),
    ("claude", "project", "settings", ".claude/settings.local.json"),
    ("claude", "project", "mcp", ".mcp.json"),
    ("codex", "user", "instructions", ".codex/AGENTS.md"),
    ("codex", "user", "settings", ".codex/config.toml"),
    ("codex", "project", "instructions", "AGENTS.md"),
    ("pi", "user", "instructions", ".pi/agent/AGENTS.md"),
    ("pi", "user", "settings", ".pi/agent/settings.json"),
    ("pi", "user", "mcp", ".pi/agent/mcp.json"),
    ("pi", "project", "instructions", "AGENTS.md"),
    ("pi", "project", "settings", ".pi/settings.json"),
    ("pi", "project", "mcp", ".pi/mcp.json"),
)

# 目录候选自身记一条（sha256 记 null），再按一层条目展开；条目的 kind 由所在目录决定。
# 目录候选是断链或无权限时只记自身那一条，不展开。
DIR_CANDIDATES = (
    ("claude", "user", "rules", ".claude/rules"),
    # 子代理定义本身是指令文件；kind 枚举里没有 agent，归到 instructions。
    ("claude", "user", "instructions", ".claude/agents"),
    # 只记一层条目，不递归 cache。
    ("claude", "user", "plugin", ".claude/plugins"),
    ("claude", "project", "rules", ".claude/rules"),
    ("claude", "project", "instructions", ".claude/agents"),
    ("pi", "user", "extension", ".pi/agent/extensions"),
    ("pi", "user", "prompt", ".pi/agent/prompts"),
    ("pi", "project", "extension", ".pi/extensions"),
    ("pi", "project", "prompt", ".pi/prompts"),
)

# skills 目录：目录自身一条 kind=skill，每个子目录（含软链子目录）再一条，指纹取子目录的 SKILL.md。
SKILL_CANDIDATES = (
    ("claude", "user", ".claude/skills"),
    ("claude", "project", ".claude/skills"),
    ("codex", "user", ".agents/skills"),
    ("codex", "project", ".agents/skills"),
    ("pi", "user", ".pi/agent/skills"),
    ("pi", "project", ".pi/skills"),
)

# 每个项目一个 memory 目录，同样按一层条目展开。
MEMORY_CANDIDATES = (("claude", ".claude/projects/*/memory"),)

SECRET_FILE_NAMES = frozenset({".mcp.json", "mcp.json", "config.toml"})


def is_secret_bearing(path: Path) -> bool:
    name = path.name
    return name in SECRET_FILE_NAMES or (name.startswith("settings") and name.endswith(".json"))


def top_level_keys(path: Path) -> list[str] | None:
    """只取顶层键名，不读取也不输出值；解析失败记 null。"""
    try:
        if path.suffix == ".toml":
            # 局部导入：仓库没有 ruff 配置，顶层导入 tomllib 会被 isort 当成第三方包。
            import tomllib

            with path.open("rb") as handle:
                data = tomllib.load(handle)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return sorted(data) if isinstance(data, dict) else None


def fingerprint(path: Path) -> dict:
    """按真实文件取指纹；目录、断链、无权限都记 null。"""
    try:
        st = path.stat()
    except OSError:
        return {"sha256": None, "size": None, "mtime": None}
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    if not stat.S_ISREG(st.st_mode):
        return {"sha256": None, "size": None, "mtime": mtime}
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = None
    return {"sha256": digest, "size": st.st_size, "mtime": mtime}


def probe(path: Path) -> tuple[int | None, str | None]:
    """返回 (lstat 的 st_mode, 访问错误类型)；路径不存在时两者都是 None。"""
    try:
        return os.lstat(path).st_mode, None
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        # 权限不足时连类型都判断不了：保留候选记录，把错误类型写进 access_error。
        return None, type(exc).__name__


def observe(path: Path, agent: str, scope: str, kind: str, hash_source: Path | None = None) -> dict:
    mode, access_error = probe(path)
    is_link = mode is not None and stat.S_ISLNK(mode)
    resolved_path = None
    broken_link = False
    if mode is not None:
        try:
            os.stat(path)
            resolved_path = os.path.realpath(path)
        except FileNotFoundError:
            # 链接项本身可读、目标确实不存在时才判断链。
            broken_link = is_link
        except OSError as exc:
            # 目标不可访问或软链成环（ELOOP）时无法解析，不判断链。
            access_error = type(exc).__name__
    record = {
        "agent": agent,
        "scope": scope,
        "kind": kind,
        "path": str(path),
        "is_symlink": is_link,
        "symlink_target": os.readlink(path) if is_link else None,
        "resolved_path": resolved_path,
        "broken_link": broken_link,
    }
    record.update(fingerprint(hash_source if hash_source is not None else path))
    record["status"] = "unmanaged"
    record["load_chain"] = "unverified"
    if hash_source is None and is_secret_bearing(path):
        record["top_level_keys"] = top_level_keys(path)
    if access_error is not None:
        record["access_error"] = access_error
    return record


def bases(scope: str, home: Path, projects: list[Path]) -> list[Path]:
    return [home] if scope == "user" else projects


def file_candidate(path: Path) -> bool:
    mode, access_error = probe(path)
    if access_error is not None:
        return True
    return mode is not None and (stat.S_ISREG(mode) or stat.S_ISLNK(mode))


def skill_candidate(path: Path) -> bool:
    mode, access_error = probe(path)
    if access_error is not None:
        return True
    return mode is not None and (stat.S_ISDIR(mode) or stat.S_ISLNK(mode))


def directory_records(directory: Path, agent: str, scope: str, kind: str, skills: bool) -> list[dict]:
    """候选目录自身先记一条；断链或无权限只记这一条，不展开。"""
    mode, access_error = probe(directory)
    if mode is None and access_error is None:
        return []
    own = observe(directory, agent, scope, kind)
    if own["broken_link"] or "access_error" in own:
        return [own]
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        # 目录自身可 stat 但不可读：错误记在它那条记录上，不中断盘点。
        own["access_error"] = type(exc).__name__
        return [own]
    records = [own]
    for entry in children:
        if entry.name == "node_modules":
            continue
        if skills and not skill_candidate(entry):
            continue
        hash_source = entry / "SKILL.md" if skills else None
        records.append(observe(entry, agent, scope, kind, hash_source=hash_source))
    return records


def collect(home: Path, projects: list[Path]) -> list[dict]:
    records = []
    for agent, scope, kind, rel in FILE_CANDIDATES:
        for base in bases(scope, home, projects):
            path = base / rel
            if file_candidate(path):
                records.append(observe(path, agent, scope, kind))
    for agent, scope, kind, rel in DIR_CANDIDATES:
        for base in bases(scope, home, projects):
            records.extend(directory_records(base / rel, agent, scope, kind, skills=False))
    for agent, scope, rel in SKILL_CANDIDATES:
        for base in bases(scope, home, projects):
            records.extend(directory_records(base / rel, agent, scope, "skill", skills=True))
    for agent, pattern in MEMORY_CANDIDATES:
        for memory_dir in sorted(home.glob(pattern)):
            records.extend(directory_records(memory_dir, agent, "user", "memory", skills=False))
    return records


def shared_groups(records: list[dict]) -> list[dict]:
    """按 resolved_path 聚合，≥2 条记录指向同一真实路径的列为可统一托管候选。"""
    by_resolved: dict[str, list[dict]] = {}
    for record in records:
        if record["resolved_path"] is not None:
            by_resolved.setdefault(record["resolved_path"], []).append(record)
    groups = []
    for resolved, members in sorted(by_resolved.items()):
        if len(members) < 2:
            continue
        groups.append(
            {
                "resolved_path": resolved,
                "record_count": len(members),
                "agents": sorted({m["agent"] for m in members}),
                "paths": sorted({m["path"] for m in members}),
            }
        )
    return groups


def inventory(home: Path, projects: list[Path]) -> dict:
    records = collect(home, projects)
    records.sort(key=lambda r: (r["agent"], r["scope"], r["kind"], r["path"]))
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "home": str(home),
        "projects": [str(p) for p in projects],
        "path_table_note": "候选路径来自本机观察，未按官方文档逐项核实；路径存在才记录。",
        "records": records,
        "groups": shared_groups(records),
    }


def real_roots(paths: list[Path]) -> list[Path]:
    seen, roots = set(), []
    for path in paths:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            roots.append(Path(real))
    return roots


def main():
    ap = argparse.ArgumentParser(description="PHE inventory")
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--project", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    try:
        data = inventory(Path(os.path.realpath(args.home)), real_roots(args.project))
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if args.out is None:
            print(text, end="")
        else:
            # 只写 --out 本身；父目录不存在时一并创建。
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
    except OSError as exc:
        ap.exit(1, f"inventory: {exc}\n")


if __name__ == "__main__":
    main()
