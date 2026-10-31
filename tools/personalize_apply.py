"""R2 产品入口（执行侧）：按批准的预览计划应用条目、恢复入口（标准库）。

执行只写两类路径：计划列明的 Host 入口，以及显式 PHE_HOME 产物。批准记录绑定计划的原始字节
（`plan_sha256`）和条目或执行 ID；`source=user` 由宿主按用户确认写成记录，工具不自动造用户批准，
`sandbox_rehearsal` 仍是演练批准（沿用旧沙箱的真实环境拒绝条件）。写入之前核对：实际规范化 PHE_HOME 就是
计划里的那个工作区，每个入口现在读的还是计划要改的内容，恢复目标的实际内容也未变。变更包、报告、入口
当前形态或内容在预览之后变了就拒绝执行，要求重新预览，不拿旧计划执行另一份 diff。
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import change
import personalize_plan as planning
import personalize_restore as restoring
import release as release_tool
import release_deploy
from change_ledger import append_ledger, next_execution_id, read_json, read_ledger, require
from release_deploy import current_state, load_sibling, read_registry, restore_entry, unbind, utc_now

release_verify_tool = load_sibling("release_verify")
workspace_tool = load_sibling("workspace")


def check_approval(approval: dict, plan_sha: str, item_id: str) -> str:
    """应用批准：绑定计划原始字节并覆盖这个条目；返回批准来源。"""
    if require(approval, "schema_version", "批准记录") != planning.SCHEMA_VERSION:
        raise ValueError(f"批准记录 schema_version 只支持 {planning.SCHEMA_VERSION}")
    source = require(approval, "source", "批准记录")
    if source not in change.APPROVAL_SOURCES:
        raise ValueError(f"批准记录的 source 只能是 {' / '.join(change.APPROVAL_SOURCES)}：{source}")
    approved = require(approval, "items", "批准记录")
    if item_id not in approved:
        raise ValueError(f"批准记录没有覆盖条目 {item_id}（已批准：{approved}）")
    recorded = require(approval, "plan_sha256", "批准记录")
    if recorded != plan_sha:
        raise ValueError(f"批准记录绑定的计划 sha256 与当前文件不符：{recorded} != {plan_sha}")
    return source


def check_restore_approval(approval: dict, plan_sha: str, execution_id: str) -> str:
    """恢复批准：绑定恢复计划的原始字节与要恢复的那次执行。"""
    if require(approval, "schema_version", "恢复批准记录") != planning.SCHEMA_VERSION:
        raise ValueError(f"批准记录 schema_version 只支持 {planning.SCHEMA_VERSION}")
    source = require(approval, "source", "恢复批准记录")
    if source not in change.APPROVAL_SOURCES:
        raise ValueError(f"批准记录的 source 只能是 {' / '.join(change.APPROVAL_SOURCES)}：{source}")
    recorded_execution = require(approval, "execution", "恢复批准记录")
    if recorded_execution != execution_id:
        raise ValueError(f"批准记录针对的是 {recorded_execution}，不是 {execution_id}")
    recorded = require(approval, "plan_sha256", "恢复批准记录")
    if recorded != plan_sha:
        raise ValueError(f"批准记录绑定的恢复计划 sha256 与当前文件不符：{recorded} != {plan_sha}")
    return source


def check_entries(entries: list[dict], content_sha: str) -> None:
    """入口在预览之后没被手改：形态、链接目标与当前内容都要与计划一致，且没有普通文件入口。

    逐个入口核对，不只看第一个：共享入口读的必须是本计划要改的那份内容，否则应用会把它的不同内容改没。
    """
    for entry in entries:
        path = Path(entry["path"])
        if not entry["writable"]:
            raise ValueError(f"入口是普通文件或目录，计划里没有可写入口：{path}（{entry['note']}）")
        state, target = current_state(path)
        if state != entry["state"] or target != entry["target"]:
            raise ValueError(f"入口 {path} 与预览时不同（{entry['state']} → {state}）：请重新预览")
        if planning.sha_of(path) != entry["sha256"]:
            raise ValueError(f"入口 {path} 的内容在预览之后变了：请重新预览")
        if entry["sha256"] != content_sha:
            raise ValueError(f"入口 {path} 读的不是本计划要改的内容（{entry['sha256']} != {content_sha}）：请重新预览")


def check_restore_entries(entries: list[dict]) -> None:
    """恢复写入前的核对：入口形态与链接、入口当前内容，以及恢复目标的实际内容都要与恢复预览一致。"""
    for entry in entries:
        path = Path(entry["path"])
        state, target = current_state(path)
        if state != entry["current"]["state"] or target != entry["current"]["target"]:
            raise ValueError(f"入口 {path} 在恢复预览之后变了：请重新预览")
        if planning.sha_of(path) != entry["current"]["sha256"]:
            raise ValueError(f"入口 {path} 的内容在恢复预览之后变了：请重新预览")
        restore_target = restoring.link_content(path, entry["restore"]["old_target"])
        restored_sha = planning.sha_of(restore_target) if restore_target is not None else None
        if restored_sha != entry["restore"]["content_sha256"]:
            raise ValueError(f"恢复目标的内容在恢复预览之后变了（{path} → {restore_target}）：请重新预览")


def deploy_planned(writer, phe: Path, plan: dict, release_id: str) -> list[str]:
    """该资产的每个 Host 入口各部署一次；写哪些路径只取计划列明的入口。"""
    manifest = release_verify_tool.read_manifest(phe / "releases" / release_id)
    asset_id = plan["asset"]["asset_id"]
    target = release_verify_tool.link_target(phe, release_id, manifest["assets"][asset_id])
    entries = plan["asset"]["entries"]
    journals = []
    for agent in sorted({entry["agent"] for entry in entries}):
        plans = [
            {"asset_id": asset_id, "path": Path(entry["path"]), "target": target}
            for entry in entries
            if entry["agent"] == agent
        ]
        journal = release_deploy.deploy_paths(writer, phe, agent, release_id, plans)
        journals.append(str(Path(journal).relative_to(phe)))
    return journals


def cmd_apply(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    plan, plan_sha = planning.read_plan(args.plan, planning.PLAN_KIND)
    planning.require_planned_workspace(plan, phe)
    approval = read_json(args.approval)
    source = check_approval(approval, plan_sha, args.item)
    change_path = Path(plan["change"]["path"])
    if change.sha256_file(change_path) != plan["change"]["sha256"]:
        raise ValueError(f"变更包在预览之后变了：{change_path}，请重新预览")
    package, change_sha = change.read_change(change_path)
    report = change.check_report(package)
    if report["sha256"] != plan["report"]["sha256"]:
        raise ValueError("计划引用的报告版本与当前变更包不一致：请重新预览")
    item_plan = next((item for item in plan["items"] if item["item_id"] == args.item), None)
    if item_plan is None:
        raise ValueError(f"计划里没有条目：{args.item}")
    if item_plan["state"] != "applicable":
        raise ValueError(f"计划里条目 {args.item} 当前不适用：{item_plan['reason']}；请重新预览")
    item = change.select_item(package, args.item)
    asset = change.target_asset(read_registry(planning.registry_path(phe, args.registry)), plan["asset"]["asset_id"])
    content_file = change.effective_file(asset)
    base = content_file.read_bytes()
    if change.sha256_bytes(base) != item_plan["current_sha256"]:
        raise ValueError("入口当前内容在预览之后变了：请重新预览，不要用旧计划执行另一份 diff")
    check_entries(plan["asset"]["entries"], item_plan["current_sha256"])
    targets = [Path(entry["path"]) for entry in plan["asset"]["entries"]]
    writer = planning.product_writer(phe, targets, source)
    execution_id = next_execution_id(read_ledger(phe))
    patch = require(item, "patch", f"条目 {args.item}")
    content = change.apply_patch(writer, phe / "staging" / execution_id, patch, base)
    if change.sha256_bytes(content) != item_plan["expected_sha256"]:
        raise ValueError("打出来的内容和预览的 diff 不一致：请重新预览")
    at = utc_now()
    change.write_asset(writer, phe, asset, Path(asset["resolved_path"]).name, content, content_file, at)
    release_id = release_tool.build_release(writer, phe, release_tool.asset_entries(phe))
    # 共享部署函数会打人读进度；产品入口的 stdout 只留一份机读结果，进度转到 stderr。
    with contextlib.redirect_stdout(sys.stderr):
        journals = deploy_planned(writer, phe, plan, release_id)
    after_sha = planning.sha_of(targets[0])
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
            "change_path": str(change_path),
            "change_sha256": change_sha,
            "note": approval.get("note"),
        },
    )
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
        "target": {
            "asset_id": asset["asset_id"],
            "resolved_path": asset["resolved_path"],
            "paths": [str(path) for path in targets],
        },
        "release": release_id,
        "journals": journals,
        "before_sha256": change.sha256_bytes(base),
        "after_sha256": after_sha,
        "plan_path": str(args.plan),
        "plan_sha256": plan_sha,
        "cycle": plan["cycle"],
        "phe_home": str(phe),
    }
    append_ledger(writer, phe, row)
    print(
        json.dumps(
            {
                "execution_id": execution_id,
                "proposal_id": args.item,
                "source": source,
                "asset_id": asset["asset_id"],
                "release": release_id,
                "before_sha256": row["before_sha256"],
                "after_sha256": after_sha,
                "journals": journals,
                "host_writes": [str(path) for path in targets],
                "ledger": str(phe / "ledger.jsonl"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    plan, plan_sha = planning.read_plan(args.plan, planning.RESTORE_PLAN_KIND)
    planning.require_planned_workspace(plan, phe)
    approval = read_json(args.approval)
    source = check_restore_approval(approval, plan_sha, args.execution)
    fact, journal_paths = restoring.restorable(phe, args.execution)
    if plan["journals"] != fact["journals"]:
        raise ValueError("恢复计划绑定的 journal 与账本不一致：请重新预览")
    check_restore_entries(plan["entries"])
    targets = [Path(entry["path"]) for entry in plan["entries"]]
    writer = planning.product_writer(phe, targets, source)
    for entry in plan["entries"]:
        restore_entry(
            writer,
            {"path": entry["path"], "state": entry["restore"]["state"], "old_target": entry["restore"]["old_target"]},
        )
    for journal_path in journal_paths:
        journal = read_json(journal_path)
        unbind(writer, phe, journal["agent"], [entry["path"] for entry in journal["entries"]])
    change.rebind_restored(writer, phe, journal_paths)
    row = {
        "kind": "restore",
        "at": utc_now(),
        "execution_id": args.execution,
        "proposal_id": fact["proposal_id"],
        "journals": fact["journals"],
        "restored_sha256": planning.sha_of(targets[0]),
        "release": change.link_release(phe, plan["host_writes"][0]),
        "approval_id": require(approval, "approval_id", "恢复批准记录"),
        "approval_path": str(args.approval),
        "source": source,
        "plan_path": str(args.plan),
        "plan_sha256": plan_sha,
        "cycle": plan["cycle"],
        "note": approval.get("note"),
    }
    append_ledger(writer, phe, row)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0
