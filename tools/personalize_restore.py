"""R2 产品入口（恢复预览）：读账本执行、journal 与入口当前内容，写恢复预览与机读计划（标准库）。

恢复计划绑定账本里的那次执行、journal 与恢复后的实际内容：`current` 记入口现在的形态与内容 sha256，
`restore` 记要写回的链接形态、原始链接字符串与目标内容的 sha256。相对链接目标按链接自身所在目录解析，
不按进程的工作目录。执行侧（`personalize_apply.py`）在写入前复核这三项，变了就要求重新预览。
"""

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_content import content_files
from change_ledger import executions, read_json, read_ledger
from personalize_plan import (
    RESTORE_PLAN_KIND,
    SCHEMA_VERSION,
    json_text,
    product_writer,
    sha_of,
    workspace,
    write_document,
)
from release_deploy import current_state, utc_now


def restorable(phe: Path, execution_id: str) -> tuple[dict, list[Path]]:
    """账本里可恢复的那次执行：必须还是倒数第一个生效中的应用（同一资产逆序恢复）。"""
    facts = read_ledger(phe)
    items = executions(facts)
    execution = next((item for item in items if item["execution_id"] == execution_id), None)
    if execution is None:
        raise ValueError(f"账本里没有执行：{execution_id}")
    if execution["state"] == "restored":
        raise ValueError(f"{execution_id} 已经恢复过")
    active = [item for item in items if item["state"] == "applied"]
    if active[-1]["execution_id"] != execution_id:
        raise ValueError(f"同一资产只支持逆序恢复：先恢复 {active[-1]['execution_id']}，再恢复 {execution_id}")
    fact = next(fact for fact in facts if fact["kind"] == "apply" and fact["execution_id"] == execution_id)
    return fact, [phe / relative for relative in fact["journals"]]


def link_content(path: Path, target: str | None) -> Path | None:
    """链接目标对应的内容文件：相对目标按链接自身所在的目录解析，不按当前工作目录。"""
    if not target:
        return None
    link_target = Path(target)
    return link_target if link_target.is_absolute() else Path(path).parent / link_target


def build_restore_plan(phe: Path, execution_id: str, cycle: str) -> dict:
    fact, journal_paths = restorable(phe, execution_id)
    if fact.get("asset_kind") == "mcp":
        import personalize_mcp_apply as mcp_applying

        return mcp_applying.build_restore_plan(phe, fact, journal_paths, cycle)
    entries = []
    for journal_path in reversed(journal_paths):
        journal = read_json(journal_path)
        for entry in reversed(journal["entries"]):
            path = Path(entry["path"])
            state, target = current_state(path)
            restore_target = link_content(path, entry["old_target"])
            entries.append(
                {
                    "agent": journal["agent"],
                    "path": str(path),
                    "current": {"state": state, "target": target, "sha256": sha_of(path), "files": content_files(path)},
                    "restore": {
                        "state": entry["state"],
                        "old_target": entry["old_target"],
                        "content_sha256": sha_of(restore_target) if restore_target is not None else None,
                        "files": content_files(restore_target) if restore_target is not None else {},
                    },
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": RESTORE_PLAN_KIND,
        "cycle": cycle,
        "created_at": utc_now(),
        "phe_home": str(phe),
        "execution_id": execution_id,
        "proposal_id": fact["proposal_id"],
        "release": fact["release"],
        "journals": fact["journals"],
        "entries": entries,
        "host_writes": [entry["path"] for entry in entries],
        "phe_writes": ["bindings.json", "ledger.jsonl"],
        "restore_note": "恢复按 journal 逆序把入口链接指回应用前的目标；恢复后要核对文件集合与每个文件的字节；"
        "应用前的共享来源文件本身从未被改过",
    }


def render_restore_preview(plan: dict, plan_sha: str) -> str:
    if plan.get("asset_kind") == "mcp":
        import personalize_mcp_apply as mcp_applying

        return mcp_applying.render_restore_preview(plan, plan_sha)
    lines = [
        f"# 恢复预览 · 周期 {plan['cycle']}",
        "",
        f"- 生成时间：{plan['created_at']}",
        f"- 执行：`{plan['execution_id']}`（提案 `{plan['proposal_id']}`，发布 `{plan['release']}`）",
        f"- 恢复计划 sha256：`{plan_sha}`（恢复批准绑定这份计划的原始字节）",
        f"- journal：{'、'.join(f'`{path}`' for path in plan['journals'])}",
        "",
        "| Agent | 入口 | 当前形态 | 当前文件 | 当前 sha256 | 恢复后 | 恢复后文件 | 恢复后内容 sha256 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in plan["entries"]:
        current, restore = entry["current"], entry["restore"]
        current_text = f"软链 → `{current['target']}`" if current["state"] == "symlink" else current["state"]
        restore_text = (
            f"指向 `{restore['old_target']}`" if restore["state"] == "symlink" else "删除入口（应用前不存在）"
        )
        lines.append(
            f"| {entry['agent']} | `{entry['path']}` | {current_text} | {len(current['files'])} 个 | "
            f"`{current['sha256']}` | {restore_text} | {len(restore['files'])} 个 | `{restore['content_sha256']}` |"
        )
    lines.extend(
        [
            "",
            "## 写入范围",
            "",
            "Host 入口（只这些）：",
            "",
            *[f"- `{path}`" for path in plan["host_writes"]],
            "",
            f"PHE_HOME 产物：{'、'.join(f'`{path}`' for path in plan['phe_writes'])}（相对 `<PHE_HOME>`）",
            "",
            f"说明：{plan['restore_note']}。恢复只追加账本事实，不表示提案无效或 retired。",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def cmd_restore_preview(args: argparse.Namespace) -> int:
    phe = workspace(args.phe_home)
    plan = build_restore_plan(phe, args.execution, args.cycle)
    directory = phe / "reports" / args.cycle
    writer = product_writer(phe)
    plan_path = directory / f"restore-{args.execution}.json"
    plan_sha = write_document(writer, plan_path, json_text(plan))
    writer.write_bytes(directory / f"restore-{args.execution}.sha256", (plan_sha + "\n").encode("utf-8"))
    preview_path = directory / f"restore-{args.execution}.md"
    write_document(writer, preview_path, render_restore_preview(plan, plan_sha))
    print(
        json.dumps(
            {
                "cycle": plan["cycle"],
                "execution_id": plan["execution_id"],
                "plan": str(plan_path),
                "plan_sha256": plan_sha,
                "preview": str(preview_path),
                "host_writes": plan["host_writes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
