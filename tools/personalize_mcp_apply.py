"""MCP 条目的执行侧：应用托管跨度、写片段 journal、按 journal 反向拼接恢复（标准库）。

应用的写入只碰计划列明的 Host 配置，并且只替换 `mcpServers` 下托管条目的值跨度；文件其余字节不动，原文件
权限位在写回后还原。journal 是第三种执行记录（类型 `mcp_fragments`）：只记 `adapter` / `config_path` /
`server`、托管前后的片段、两侧完整文件 sha256 与执行 ID，不复制整份配置——未托管的条目可能有凭证。恢复按
同一跨度写回应用前的片段，并核对两侧完整文件 sha256；对不上就拒绝，不做自动协调。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import change
import mcp_config
import personalize_mcp as mcp_asset
import personalize_plan as planning
import release as release_tool
from asset_content import content_files, content_sha, stage_patch, write_asset
from change_ledger import append_ledger, next_execution_id, read_json, read_ledger, require
from release_deploy import utc_now

SCHEMA_VERSION = planning.SCHEMA_VERSION
RESTORE_PLAN_KIND = planning.RESTORE_PLAN_KIND
JOURNAL_KIND = mcp_asset.JOURNAL_KIND


def check_target_current(row: dict) -> tuple[Path, bytes, str, dict, int]:
    """执行前核对：整份文件与托管片段的 sha256 都还要与预览一致。"""
    path = Path(row["config_path"])
    data, text = mcp_asset.read_config(path)
    if mcp_asset.sha256_bytes(data) != row["current_file_sha256"]:
        raise ValueError(f"{path} 的内容在预览之后变了：请重新预览")
    location = mcp_config.server_location(text, row["server"])
    if location is None:
        raise ValueError(f"{path} 里已经没有条目 {row['server']}：请重新预览")
    fragment = text[location["span"][0] : location["span"][1]]
    if mcp_asset.sha256_bytes(fragment.encode("utf-8")) != row["current_fragment_sha256"]:
        raise ValueError(f"{path} 里条目 {row['server']} 的内容在预览之后变了：请重新预览")
    return path, data, text, location, mcp_asset.file_mode(path)


def write_journal(writer, phe: Path, execution_id: str, release_id: str, targets: list[dict]) -> str:
    """执行记录先落盘：只记托管片段与哈希，之后写 Host 失败也能按它回退。"""
    directory = phe / "journal"
    writer.makedirs(directory)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-mcp.json"
    suffix = 1
    while os.path.lexists(path):
        path = directory / f"{stamp}-mcp-{suffix}.json"
        suffix += 1
    writer.write_json(
        path,
        {
            "kind": JOURNAL_KIND,
            "created_at": utc_now(),
            "execution_id": execution_id,
            "release": release_id,
            "phe_home": str(phe),
            "targets": targets,
        },
    )
    return str(path.relative_to(phe))


def apply(
    phe: Path,
    args: argparse.Namespace,
    plan: dict,
    plan_sha: str,
    approval: dict,
    source: str,
    package: dict,
    report: dict,
    change_sha: str,
    item_plan: dict,
) -> dict:
    asset_id = plan["asset"]["asset_id"]
    meta = mcp_asset.metadata(phe, asset_id)
    base_file = mcp_asset.definition_file(phe, asset_id)
    server = plan["asset"]["server"]
    if change.sha256_file(base_file) != item_plan["current_sha256"]:
        raise ValueError(f"逻辑定义在预览之后变了：{base_file}，请重新预览")
    prepared = [check_target_current(dict(row, server=row.get("server") or server)) for row in item_plan["targets"]]
    item = change.select_item(package, args.item)
    patch = require(item, "patch", f"条目 {args.item}")
    targets = [Path(row["config_path"]) for row in item_plan["targets"]]
    writer = planning.product_writer(phe, targets, source)
    execution_id = next_execution_id(read_ledger(phe))
    staged = stage_patch(writer, phe / "staging" / execution_id, patch, base_file)
    if content_sha(staged) != item_plan["expected_sha256"]:
        raise ValueError("打出来的逻辑定义与预览的 diff 不一致：请重新预览")
    definition = mcp_config.check_definition(json.loads(staged.read_text(encoding="utf-8")))
    at = utc_now()
    write_asset(
        writer,
        phe,
        {"asset_id": asset_id, "resolved_path": str(base_file)},
        staged,
        base_file,
        at,
        kind="mcp",
        extra={"mcp": meta["mcp"]},
    )
    release_id = release_tool.build_release(writer, phe, release_tool.asset_entries(phe))
    journal_targets, writes = [], []
    for row, (path, data, text, location, mode) in zip(item_plan["targets"], prepared):
        rendered = mcp_config.render_entry_text(row["adapter"], definition, location["column"], location["step"])
        new_text = mcp_config.splice(text, location["span"], rendered)
        new_data = new_text.encode("utf-8")
        if mcp_asset.sha256_bytes(new_data) != row["expected_file_sha256"]:
            raise ValueError(f"{path} 的渲染结果与预览不一致：请重新预览")
        writes.append((path, new_data, mode))
        journal_targets.append(
            {
                "adapter": row["adapter"],
                "config_path": str(path),
                "server": server,
                "span": list(location["span"]),
                "column": location["column"],
                "step": location["step"],
                "before_fragment": text[location["span"][0] : location["span"][1]],
                "after_fragment": rendered,
                "before_file_sha256": mcp_asset.sha256_bytes(data),
                "after_file_sha256": row["expected_file_sha256"],
            }
        )
    journal = write_journal(writer, phe, execution_id, release_id, journal_targets)
    for path, new_data, mode in writes:
        writer.write_bytes(path, new_data)
        writer.chmod(path, mode)
    append_ledger(
        writer,
        phe,
        {
            "kind": "approval",
            "at": at,
            "execution_id": execution_id,
            "approval_id": require(approval, "approval_id", "批准记录"),
            "approval_path": str(args.approval),
            "source": source,
            "proposal_id": args.item,
            "approved_items": approval["items"],
            "plan_path": str(args.plan),
            "plan_sha256": plan_sha,
            "cycle": plan["cycle"],
            "change_path": str(plan["change"]["path"]),
            "change_sha256": change_sha,
            "note": approval.get("note"),
        },
    )
    after_sha = change.sha256_file(base_file)
    row = {
        "kind": "apply",
        "at": at,
        "execution_id": execution_id,
        "approval_id": approval["approval_id"],
        "source": source,
        "proposal_id": args.item,
        "report": report,
        "evidence": require(item, "evidence", f"条目 {args.item}"),
        "prediction": require(item, "prediction", f"条目 {args.item}"),
        "asset_kind": "mcp",
        "target": {
            "asset_id": asset_id,
            "resolved_path": str(base_file),
            "paths": [str(path) for path in targets],
            "server": server,
            "mcp_targets": journal_targets
            and [
                {"adapter": target["adapter"], "config_path": target["config_path"], "server": server}
                for target in journal_targets
            ],
        },
        "release": release_id,
        "journals": [journal],
        "before_sha256": item_plan["current_sha256"],
        "before_files": {mcp_config.DEFINITION_NAME: item_plan["current_sha256"]},
        "after_sha256": after_sha,
        "after_files": content_files(base_file),
        "plan_path": str(args.plan),
        "plan_sha256": plan_sha,
        "cycle": plan["cycle"],
        "phe_home": str(phe),
    }
    append_ledger(writer, phe, row)
    return {
        "execution_id": execution_id,
        "proposal_id": args.item,
        "source": source,
        "asset_id": asset_id,
        "release": release_id,
        "before_sha256": row["before_sha256"],
        "after_sha256": after_sha,
        "journal": journal,
        "host_writes": [str(path) for path in targets],
        "ledger": str(phe / "ledger.jsonl"),
    }


# 恢复：按 journal 的片段反向拼接，核对两侧完整文件 sha256。


def render_fragment_diff(current: str, before: str) -> str:
    import difflib

    old = (current + "\n").splitlines(keepends=True)
    new = (before + "\n").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile="当前条目", tofile="恢复后条目"))


def build_restore_plan(phe: Path, fact: dict, journal_paths: list[Path], cycle: str) -> dict:
    targets = []
    for journal_path in journal_paths:
        journal = read_json(journal_path)
        if journal.get("kind") != JOURNAL_KIND:
            raise ValueError(f"{journal_path} 不是 MCP 片段 journal")
        for entry in journal["targets"]:
            path = Path(entry["config_path"])
            data, text = mcp_asset.read_config(path)
            if mcp_asset.sha256_bytes(data) != entry["after_file_sha256"]:
                raise ValueError(f"{path} 与应用后的记录不一致：请先核对当前内容，不做自动协调")
            location = mcp_config.server_location(text, entry["server"])
            if location is None:
                raise ValueError(f"{path} 里已经没有条目 {entry['server']}：请先核对当前内容")
            fragment = text[location["span"][0] : location["span"][1]]
            if mcp_asset.sha256_bytes(fragment.encode("utf-8")) != mcp_asset.sha256_bytes(
                entry["after_fragment"].encode("utf-8")
            ):
                raise ValueError(f"{path} 里条目 {entry['server']} 的片段与应用后的记录不一致：请先核对当前内容")
            restored = mcp_config.splice(text, location["span"], entry["before_fragment"]).encode("utf-8")
            if mcp_asset.sha256_bytes(restored) != entry["before_file_sha256"]:
                raise ValueError(f"{path} 的片段不能还原成应用前的完整 sha256：未恢复")
            targets.append(
                {
                    "adapter": entry["adapter"],
                    "config_path": str(path),
                    "server": entry["server"],
                    "current_file_sha256": mcp_asset.sha256_bytes(data),
                    "current_fragment_sha256": mcp_asset.sha256_bytes(fragment.encode("utf-8")),
                    "restore_fragment_sha256": mcp_asset.sha256_bytes(entry["before_fragment"].encode("utf-8")),
                    "restored_file_sha256": mcp_asset.sha256_bytes(restored),
                    "before_fragment": entry["before_fragment"],
                    "diff": render_fragment_diff(fragment, entry["before_fragment"]),
                }
            )
    if not targets:
        raise ValueError(f"{fact['execution_id']} 的 journal 里没有可恢复的条目")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": RESTORE_PLAN_KIND,
        "asset_kind": "mcp",
        "cycle": cycle,
        "created_at": utc_now(),
        "phe_home": str(phe),
        "execution_id": fact["execution_id"],
        "proposal_id": fact["proposal_id"],
        "release": fact["release"],
        "journals": fact["journals"],
        "entries": targets,
        "host_writes": [target["config_path"] for target in targets],
        "phe_writes": ["ledger.jsonl"],
        "restore_note": "恢复按 journal 把每个 Host 的托管跨度写回应用前的片段，并核对两侧完整文件 sha256；"
        "PHE 的逻辑定义保留应用后的内容",
    }


def render_restore_preview(plan: dict, plan_sha: str) -> str:
    lines = [
        f"# MCP 条目恢复预览 · 周期 {plan['cycle']}",
        "",
        f"- 生成时间：{plan['created_at']}",
        f"- 执行：`{plan['execution_id']}`（提案 `{plan['proposal_id']}`，发布 `{plan['release']}`）",
        f"- 恢复计划 sha256：`{plan_sha}`（恢复批准绑定这份计划的原始字节）",
        f"- journal：{'、'.join(f'`{path}`' for path in plan['journals'])}",
        "",
        "| Agent | 配置 | 当前文件 sha256 | 恢复后文件 sha256 |",
        "|---|---|---|---|",
    ]
    for entry in plan["entries"]:
        lines.append(
            f"| {entry['adapter']} | `{entry['config_path']}` | `{entry['current_file_sha256']}` | "
            f"`{entry['restored_file_sha256']}` |"
        )
    for entry in plan["entries"]:
        lines.extend(
            [
                "",
                f"`{entry['adapter']}` · `{entry['config_path']}` · 条目 `{entry['server']}`",
                "",
                "```diff",
                entry["diff"].rstrip("\n"),
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "## 写入范围",
            "",
            "Host 配置（只这些）：",
            "",
            *[f"- `{path}`" for path in plan["host_writes"]],
            "",
            f"说明：{plan['restore_note']}。恢复只追加账本事实，不表示提案无效或 retired。",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def restore(phe: Path, args: argparse.Namespace, plan: dict, plan_sha: str, approval: dict, source: str) -> dict:
    prepared = []
    for entry in plan["entries"]:
        path = Path(entry["config_path"])
        data, text = mcp_asset.read_config(path)
        if mcp_asset.sha256_bytes(data) != entry["current_file_sha256"]:
            raise ValueError(f"{path} 在恢复预览之后变了：请重新预览")
        location = mcp_config.server_location(text, entry["server"])
        if location is None:
            raise ValueError(f"{path} 里已经没有条目 {entry['server']}：请重新预览")
        fragment = text[location["span"][0] : location["span"][1]]
        if mcp_asset.sha256_bytes(fragment.encode("utf-8")) != entry["current_fragment_sha256"]:
            raise ValueError(f"{path} 里条目 {entry['server']} 的片段在恢复预览之后变了：请重新预览")
        new_data = mcp_config.splice(text, location["span"], entry["before_fragment"]).encode("utf-8")
        if mcp_asset.sha256_bytes(new_data) != entry["restored_file_sha256"]:
            raise ValueError(f"{path} 的恢复结果与恢复预览不一致：请重新预览")
        prepared.append((path, new_data, mcp_asset.file_mode(path)))
    targets = [Path(entry["config_path"]) for entry in plan["entries"]]
    writer = planning.product_writer(phe, targets, source)
    for path, new_data, mode in prepared:
        writer.write_bytes(path, new_data)
        writer.chmod(path, mode)
    row = {
        "kind": "restore",
        "at": utc_now(),
        "execution_id": plan["execution_id"],
        "proposal_id": plan["proposal_id"],
        "journals": plan["journals"],
        "restored_configs": [
            {
                "adapter": entry["adapter"],
                "config_path": entry["config_path"],
                "server": entry["server"],
                "sha256": entry["restored_file_sha256"],
            }
            for entry in plan["entries"]
        ],
        "approval_id": require(approval, "approval_id", "恢复批准记录"),
        "approval_path": str(args.approval),
        "source": source,
        "plan_path": str(args.plan),
        "plan_sha256": plan_sha,
        "cycle": plan["cycle"],
        "note": approval.get("note"),
    }
    append_ledger(writer, phe, row)
    return row
