#!/usr/bin/env python3
"""R1 用户入口：初始化 PHE_HOME，生成只读发现报告（标准库）。

克隆后从任意工作目录运行：`python3 -I <源码目录>/tools/workspace.py init|discover ...`。
这不是安装包：没有默认 PHE_HOME（只认 `--phe-home` 或 `PHE_HOME` 环境变量），也不去读真实 home。
`discover` 只读 `--home` / `--project` 给出的 Host 根，不 adopt、不导入、不部署，不改输入。
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 本地模块必须导入，但不能在源码目录留下 __pycache__：从源码副本运行时源码树应当一个字节都不变。
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_deploy import load_sibling
from workspace_kinds import KIND_ROWS

inventory_tool = load_sibling("inventory")
registry_tool = load_sibling("registry")

WORKSPACE_FORMAT = 1
WORKSPACE_FILE = "workspace.json"
REPORTS_DIR = "reports"
REPORT_MD = "discovery.md"
REPORT_JSON = "discovery.json"
SKELETON_DIRS = ("assets", "releases")
REGISTRY_RELATIVE = Path("registry") / "assets.json"

MANAGEMENT_LABELS = {
    "sandbox_managed": "沙箱可托管",
    "sandbox_partial": "部分可托管",
    "product_partial": "产品入口部分可托管",
    "observe_only": "仅观察",
    "unsupported": "未支持",
}

# inventory 的候选表把 `.claude/agents`、`.pi/agent/agents` 记成 instructions：kind 枚举里没有 agent。
# 这些位置放的是子代理定义，逻辑类型未支持，所以标成只读观察，不继承单文件指令的能力。
SUBAGENT_DIRS = ((".claude", "agents"), (".pi", "agent", "agents"))
SUBAGENT_NOTE = "子代理定义：kind 枚举里没有 agent，逻辑类型未支持；只读观察，不导入也不部署。"
SUBAGENT_DIR_NOTE = "子代理定义目录：只记一层条目；子代理逻辑类型未支持，不导入也不部署。"

KIND_INDEX = {
    kind: {"label": label, "management": management, "note": note} for kind, label, management, note in KIND_ROWS
}
KIND_POSITION = {row[0]: position for position, row in enumerate(KIND_ROWS)}


# init：目录骨架与格式版本标记。


def format_version(marker: Path) -> int | None:
    """工作区格式版本；标记文件不存在时返回 None。"""
    if not marker.exists():
        return None
    return json.loads(marker.read_text(encoding="utf-8"))["format_version"]


def init_workspace(phe: Path) -> int:
    """建出目录骨架与版本标记；已存在的工作区保留全部数据，不迁移、不覆盖。"""
    marker = phe / WORKSPACE_FILE
    existing = format_version(marker)
    if existing is not None and existing != WORKSPACE_FORMAT:
        raise ValueError(f"{phe} 的工作区格式版本是 {existing}，本工具只支持 {WORKSPACE_FORMAT}；R1 不提供迁移")
    created = []
    for name in SKELETON_DIRS:
        directory = phe / name
        if not directory.is_dir():
            directory.mkdir(parents=True)
            created.append(name)
    if existing is None:
        marker.write_text(json.dumps({"format_version": WORKSPACE_FORMAT}) + "\n", encoding="utf-8")
        created.append(WORKSPACE_FILE)
        print(f"initialized {phe}（workspace format {WORKSPACE_FORMAT}）；新建：{'、'.join(created)}")
    else:
        print(
            f"已初始化 {phe}（workspace format {WORKSPACE_FORMAT}）；保留已有数据，新建：{'、'.join(created) or '无'}"
        )
    print("登记表、账本与绑定按实际操作创建（init 不预建）")
    return 0


# discover：只读盘点 + 登记状态 + 报告。


def require_workspace(phe: Path) -> None:
    version = format_version(phe / WORKSPACE_FILE)
    if version is None:
        raise ValueError(f"{phe} 还不是 PHE 工作区：先运行 init --phe-home {phe}")
    if version != WORKSPACE_FORMAT:
        raise ValueError(f"{phe} 的工作区格式版本是 {version}，本工具只支持 {WORKSPACE_FORMAT}；R1 不提供迁移")


def load_registry(path: Path) -> dict | None:
    """工作区登记表；没有表时全部资产都是未登记，不创建一个空表。"""
    if not path.exists():
        return None
    return registry_tool.read_registry(path)


def registered_index(registry: dict | None) -> tuple[dict[str, dict], dict[str, dict]]:
    """登记身份索引：(按登记来源路径, 按 observed 入口路径)。

    两个索引分开：入口当前指向的正是某个登记的来源路径时，认那个资产；发布部署后入口
    指向 release 文件，来源不在表里，再按入口路径认登记身份。合成一个索引会让旧资产
    的 observed 入口遮住后来登记的来源。
    """
    sources: dict[str, dict] = {}
    entries: dict[str, dict] = {}
    if registry is None:
        return sources, entries
    for asset in registry["assets"]:
        sources.setdefault(asset["resolved_path"], asset)
        # 同一入口换绑到另一个来源并重新登记时，以最近登记的资产为准。
        for observed in asset["observed"]:
            entries[observed["path"]] = asset
    return sources, entries


def registered_asset(record: dict, sources: dict[str, dict], entries: dict[str, dict]) -> dict | None:
    """当前解析目标已登记就直接认它的资产；否则按入口路径认（部署后入口指向 release 文件）。"""
    resolved = record["resolved_path"]
    if resolved is not None and resolved in sources:
        return sources[resolved]
    return entries.get(record["path"])


def subagent_note(path: str) -> str | None:
    """子代理定义（含它的目录节点）的说明；不是这两处的路径返回 None。"""
    parts = Path(path).parts
    for suffix in SUBAGENT_DIRS:
        size = len(suffix)
        if parts[-size:] == suffix:
            return SUBAGENT_DIR_NOTE
        if parts[-size - 1 : -1] == suffix:
            return SUBAGENT_NOTE
    return None


def describe(record: dict, sources: dict[str, dict], entries: dict[str, dict]) -> dict:
    """在 ObservedAsset 字段上补管理状态与登记状态。"""
    row = KIND_INDEX[record["kind"]]
    management, note = row["management"], row["note"]
    subagent = subagent_note(record["path"])
    if subagent is not None:
        management, note = "observe_only", subagent
    asset = registered_asset(record, sources, entries)
    described = dict(record)
    described.update(
        {
            "label": row["label"],
            "management": management,
            "management_note": note,
            "registry_status": "registered" if asset else "unregistered",
            "registry_asset_id": asset["asset_id"] if asset else None,
            "registry_state": asset["state"] if asset else None,
            "registry_target": asset["resolved_path"] if asset else None,
            "registry_target_matches": (asset["resolved_path"] == record["resolved_path"]) if asset else None,
        }
    )
    return described


def sort_key(record: dict) -> tuple:
    return (KIND_POSITION[record["kind"]], record["agent"], record["scope"], record["path"])


def coverage_rows(records: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["kind"]] = counts.get(record["kind"], 0) + 1
    rows = []
    for kind, label, management, note in KIND_ROWS:
        found = counts.get(kind, 0)
        if management == "unsupported":
            status = "unsupported"
        elif found:
            status = "found"
        else:
            status = "not_found"
        rows.append(
            {
                "kind": kind,
                "label": label,
                "management": management,
                "status": status,
                "discovered": found,
                "note": note,
            }
        )
    return rows


def scope_rows(records: list[dict], home: Path, projects: list[Path]) -> list[dict]:
    """用户级与项目级分开记；没传 --project 时写清是没扫描，不是没有项目级资产。"""
    user = sum(1 for record in records if record["scope"] == "user")
    rows = [{"scope": "user", "roots": [str(home)], "discovered": user, "note": "用户级根 --home"}]
    if projects:
        project = sum(1 for record in records if record["scope"] == "project")
        rows.append(
            {
                "scope": "project",
                "roots": [str(p) for p in projects],
                "discovered": project,
                "note": "项目级根 --project",
            }
        )
    else:
        rows.append({"scope": "project", "roots": [], "discovered": None, "note": "没有传 --project：项目级条目未扫描"})
    return rows


def discovery(phe: Path, home: Path, projects: list[Path], registry_path: Path) -> dict:
    registry = load_registry(registry_path)
    sources, entries = registered_index(registry)
    records = [describe(record, sources, entries) for record in inventory_tool.collect(home, projects)]
    records.sort(key=sort_key)
    return {
        "format_version": WORKSPACE_FORMAT,
        "kind": "phe_discovery",
        "phe_home": str(phe),
        "home": str(home),
        "projects": [str(p) for p in projects],
        "registry": {"path": str(registry_path), "present": registry is not None},
        "scopes": scope_rows(records, home, projects),
        "coverage": coverage_rows(records),
        "records": records,
        "groups": inventory_tool.shared_groups(records),
    }


def keys_text(keys: list[str] | None) -> str:
    if keys is None:
        return "不可解析"
    return "、".join(keys) if keys else "无顶层键"


def source_text(record: dict) -> str:
    """盘点的来源形态：软链只看指向；没有 sha256 与 size 的是目录记录。"""
    if record["is_symlink"]:
        return f"软链 → `{record['symlink_target']}`"
    if record["kind"] == "skill" and record["sha256"] is not None:
        # Skill 条目是目录，指纹取它里面的 SKILL.md，不是目录本身。
        return "目录（指纹取 SKILL.md）"
    if record["sha256"] is None and record["size"] is None and "access_error" not in record:
        return "目录"
    return "实体文件"


def entry_line(record: dict) -> str:
    parts = [f"`{record['path']}`", f"入口 {record['agent']}/{record['scope']}", source_text(record)]
    if record["broken_link"]:
        parts.append("断链")
    if "readers" in record:
        parts.append("读取者 " + "、".join(record["readers"]))
    if "access_error" in record:
        parts.append(f"访问错误 {record['access_error']}")
    if "top_level_keys" in record:
        parts.append("顶层键名 " + keys_text(record["top_level_keys"]))
    if record["registry_status"] == "registered":
        parts.append(f"已登记 {record['registry_asset_id']}（{record['registry_state']}）")
        if not record["registry_target_matches"]:
            current = f"`{record['resolved_path']}`" if record["resolved_path"] else "无法解析"
            parts.append(f"当前入口 {current}，不是登记源 `{record['registry_target']}`")
    else:
        parts.append("未登记")
    parts.append("能力 " + MANAGEMENT_LABELS[record["management"]])
    parts.append("load_chain unverified")
    return "- " + " · ".join(parts)


def render_markdown(data: dict) -> str:
    registry = data["registry"]
    registry_line = (
        f"`{registry['path']}`（已存在：先按登记来源认身份，发布目标按 observed 入口认，当前目标单独比较）"
        if registry["present"]
        else f"`{registry['path']}`（不存在：全部未登记）"
    )
    projects = "、".join(f"`{project}`" for project in data["projects"]) or "无（没有传 --project，项目级范围未扫描）"
    lines = [
        "# Harness 发现报告（只读）",
        "",
        f"- Host 根（`--home`）：`{data['home']}`",
        f"- 项目根（`--project`）：{projects}",
        f"- PHE_HOME：`{data['phe_home']}`",
        f"- 登记表：{registry_line}",
        "- 扫描结果：" + "；".join(scope_text(row) for row in data["scopes"]),
        "- 加载链：全部 `unverified`（没有在 Host 里验证是否被加载）",
        "",
        "## 覆盖摘要",
        "",
        "| 逻辑类型 | 管理状态 | 本根发现 | 限制 |",
        "|---|---|---|---|",
    ]
    for row in data["coverage"]:
        lines.append(
            f"| {row['label']} `{row['kind']}` | {MANAGEMENT_LABELS[row['management']]} | {found_text(row)} | {row['note']} |"
        )
    lines.extend(
        [
            "",
            "未发现 = 本根没有候选路径命中；未支持 = 盘点没有这一类候选路径，不出现在下面的条目里。",
            "",
            "## 发现条目",
        ]
    )
    for kind, label, _management, _note in KIND_ROWS:
        entries = [record for record in data["records"] if record["kind"] == kind]
        if not entries:
            continue
        lines.extend(["", f"### {label} `{kind}`（{len(entries)} 条）", ""])
        lines.extend(entry_line(record) for record in entries)
    if data["groups"]:
        lines.extend(["", "## 共享组（同一真实路径被多个入口读取）", ""])
        for group in data["groups"]:
            paths = "、".join(f"`{path}`" for path in group["paths"])
            lines.append(
                f"- `{group['resolved_path']}` · {group['record_count']} 个入口：{'、'.join(group['agents'])} · {paths}"
            )
    unsupported = [row for row in data["coverage"] if row["status"] == "unsupported"]
    if unsupported:
        lines.extend(["", "## 未支持的逻辑类型", ""])
        lines.extend(f"- {row['label']} `{row['kind']}`：{row['note']}" for row in unsupported)
    lines.extend(
        [
            "",
            "本报告只读：不改输入文件、软链、登记表或配置。管理状态是 PHE 当前能力，不是加载或效果证据。",
            "",
        ]
    )
    return "\n".join(lines)


def scope_text(row: dict) -> str:
    label = "用户级" if row["scope"] == "user" else "项目级"
    if row["discovered"] is None:
        return f"{label} 未扫描（没有传 --project）"
    return f"{label} {row['discovered']} 条"


def found_text(row: dict) -> str:
    if row["status"] == "unsupported":
        return "未支持"
    if row["status"] == "found":
        return f"{row['discovered']} 条"
    return "未发现"


def write_report(phe: Path, data: dict) -> tuple[Path, Path]:
    directory = phe / REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / REPORT_MD
    md_path.write_text(render_markdown(data), encoding="utf-8")
    json_path = directory / REPORT_JSON
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def discover_report(phe: Path, args: argparse.Namespace) -> int:
    require_workspace(phe)
    home = Path(os.path.realpath(args.home))
    projects = inventory_tool.real_roots(args.project)
    registry_path = args.registry or phe / REGISTRY_RELATIVE
    data = discovery(phe, home, projects, registry_path)
    md_path, json_path = write_report(phe, data)
    registered = sum(1 for record in data["records"] if record["registry_status"] == "registered")
    unsupported = [row["label"] for row in data["coverage"] if row["status"] == "unsupported"]
    print(
        f"discovered {len(data['records'])} 条（已登记 {registered} / 未登记 {len(data['records']) - registered}）；"
        f"未支持类型：{'、'.join(unsupported)}"
    )
    print(f"报告：{md_path}")
    print(f"结构化结果：{json_path}")
    return 0


# CLI。


def resolve_phe_home(parser: argparse.ArgumentParser, value: Path | None) -> Path:
    """命令行优先，其次 PHE_HOME 环境变量；都没有就报错，不替用户选中真实目录。"""
    if value is not None:
        return value
    from_env = os.environ.get("PHE_HOME")
    if from_env:
        return Path(from_env)
    parser.error("--phe-home 必填：没有默认目录，也可以用 PHE_HOME 环境变量")


def add_phe_home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phe-home", type=Path, help="PHE_HOME（仓库外数据目录）；缺省读 PHE_HOME 环境变量")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PHE 工作区初始化与只读发现（R1 入口，克隆后从任意目录运行）")
    commands = parser.add_subparsers(dest="cmd", required=True)

    initializer = commands.add_parser("init", help="创建/校验 PHE_HOME 的目录骨架与格式版本标记")
    add_phe_home(initializer)

    finder = commands.add_parser("discover", help="只读盘点 Host 根，把发现报告写进 <PHE_HOME>/reports")
    add_phe_home(finder)
    finder.add_argument(
        "--home", type=Path, required=True, help="显式 Host 根，例如合成树的 <tmp>/host；不扫描真实 home"
    )
    finder.add_argument("--project", type=Path, action="append", default=[], help="项目级扫描根，可重复")
    finder.add_argument("--registry", type=Path, help=f"登记表，缺省 <PHE_HOME>/{REGISTRY_RELATIVE}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    phe = resolve_phe_home(parser, args.phe_home)
    try:
        if args.cmd == "init":
            return init_workspace(phe)
        return discover_report(phe, args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"workspace: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
