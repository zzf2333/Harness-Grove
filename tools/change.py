#!/usr/bin/env python3
"""闭环切片 2/3：按批准的机读变更包应用、查看、恢复与复盘（标准库）。

宿主显式准备两份输入：变更包（报告引用与 sha256、条目 ID、证据、预测/验证方法、目标资产、patch）
和批准记录（`source`、绑定变更包原始字节的 sha256、获批条目）。`apply` 取目标入口当前实际生效的
内容，打上一条获批条目的 patch，再走既有 release + deploy；`restore` 按 journal 回到上一个部署或
原文件。账本 `<phe-home>/ledger.jsonl` 只追加事实，状态由事实推导。

`review-plan` / `review` 是闭环切片 3 的复盘入口，实现在 `change_review.py`：只记录宿主的判断，
不改执行状态、不调用 restore。账本事实与推导在 `change_ledger.py`。

只在临时沙箱运行：`--home`、`--phe-home` 必填且不得指向真实环境，写入都经 release_deploy.Sandbox。
本轮范围：单个 instructions 资产 + 三个 Agent 的临时软链入口。skill、普通文件入口、多资产发布不支持。
"""

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release as release_tool
from asset_content import (
    asset_kind,
    content_files,
    content_sha,
    effective_file,
    sha256_bytes,
    sha256_file,
    stage_patch,
    write_asset,
)
from change_ledger import append_ledger, executions, latest_reviews, next_execution_id, read_json, read_ledger, require
from change_review import cmd_review, cmd_review_plan, phe_roots
from release_deploy import (
    GUARD_EXIT_CODE,
    GuardError,
    Sandbox,
    Writer,
    bind,
    deploy,
    read_registry,
    rollback,
    sandbox_roots,
    utc_now,
)

SCHEMA_VERSION = 1
# 演练批准必须显式写 sandbox_rehearsal，不能拿它冒充用户批准。
APPROVAL_SOURCES = ("user", "sandbox_rehearsal")


def read_change(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    package = json.loads(data.decode("utf-8"))
    if require(package, "schema_version", "变更包") != SCHEMA_VERSION:
        raise ValueError(f"变更包 schema_version 只支持 {SCHEMA_VERSION}")
    return package, sha256_bytes(data)


# 输入：变更包、批准记录、报告版本。


def select_item(package: dict, item_id: str) -> dict:
    for item in require(package, "items", "变更包"):
        if require(item, "item_id", "变更包条目") == item_id:
            return item
    raise ValueError(f"变更包里没有条目：{item_id}")


def check_approval(approval: dict, change_sha: str, item_id: str) -> str:
    """批准记录绑定确定版本的变更包和获批条目；返回批准来源。"""
    if require(approval, "schema_version", "批准记录") != SCHEMA_VERSION:
        raise ValueError(f"批准记录 schema_version 只支持 {SCHEMA_VERSION}")
    source = require(approval, "source", "批准记录")
    if source not in APPROVAL_SOURCES:
        raise ValueError(f"批准记录的 source 只能是 {' / '.join(APPROVAL_SOURCES)}：{source}")
    approved = require(approval, "items", "批准记录")
    if item_id not in approved:
        raise ValueError(f"批准记录没有覆盖条目 {item_id}（已批准：{approved}）")
    recorded = require(approval, "change_sha256", "批准记录")
    if recorded != change_sha:
        raise ValueError(f"批准记录绑定的变更包 sha256 与当前文件不符：{recorded} != {change_sha}")
    return source


def check_report(package: dict) -> dict:
    """证据可追溯：变更包必须引用当前这一版报告。"""
    report = require(package, "report", "变更包")
    path = Path(require(report, "path", "变更包 report"))
    recorded = require(report, "sha256", "变更包 report")
    actual = sha256_file(path)
    if actual != recorded:
        raise ValueError(f"变更包引用的报告已变化：{path} {recorded} != {actual}")
    return {"path": str(path), "sha256": recorded}


# 资产：内容取自目标入口当前实际生效的文件。


def target_asset(registry: dict, asset_id: str) -> dict:
    for asset in registry["assets"]:
        if asset["asset_id"] == asset_id:
            return asset
    raise ValueError(f"登记表里没有资产：{asset_id}")


# 发布与部署：复用 release 的构建与 release_deploy 的逐 Agent 部署。


def journal_names(phe: Path) -> list[str]:
    directory = phe / "journal"
    return sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []


def deploy_agents(roots: list[Path], registry_path: Path, asset: dict, release_id: str) -> list[str]:
    """该资产的每个 Agent 入口各部署一次；一次应用记一条执行，三份 journal 一起入账。"""
    home, phe = roots
    journals = []
    for agent in sorted({observed["agent"] for observed in asset["observed"]}):
        before = set(journal_names(phe))
        args = argparse.Namespace(agent=agent, release=release_id, registry=registry_path, home=home, phe_home=phe)
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            deploy(args, roots)
        print(captured.getvalue().strip(), file=sys.stderr)
        created = sorted(set(journal_names(phe)) - before)
        if len(created) != 1:
            raise ValueError(f"{agent} 的部署没有留下唯一一份 journal：{created}")
        journals.append(f"journal/{created[0]}")
    return journals


def release_of(phe: Path, target: str | None) -> str | None:
    """链接目标若落在本 PHE_HOME 的发布里，返回发布号；否则返回 None（原文件）。"""
    if target is None:
        return None
    try:
        relative = Path(target).relative_to(phe / "releases")
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 2 or not (phe / "releases" / parts[0]).is_dir():
        return None
    return parts[0]


# 账本：只追加事实，状态从事实推导（实现与推导在 change_ledger.py）。


def link_release(phe: Path, path: str) -> str | None:
    return release_of(phe, os.readlink(path)) if os.path.islink(path) else None


# 命令。


def cmd_apply(args: argparse.Namespace, roots: list[Path]) -> int:
    sandbox = Sandbox(roots)
    home, phe = roots
    package, change_sha = read_change(args.change)
    item = select_item(package, args.item)
    approval = read_json(args.approval)
    source = check_approval(approval, change_sha, args.item)
    report = check_report(package)
    asset = target_asset(
        read_registry(args.registry), require(item["target"], "asset_id", f"条目 {args.item} 的 target")
    )
    content_file = effective_file(asset)
    before_sha = content_sha(content_file)
    before_files = content_files(content_file)
    execution_id = next_execution_id(read_ledger(phe))
    patch = require(item, "patch", f"条目 {args.item}")
    staged = stage_patch(sandbox, phe / "staging" / execution_id, patch, content_file)
    kind = asset_kind(phe, asset)
    at = utc_now()
    write_asset(sandbox, phe, asset, staged, content_file, at)
    release_id = release_tool.build_release(sandbox, phe, release_tool.asset_entries(phe))
    journals = deploy_agents(roots, args.registry, asset, release_id)
    paths = [observed["path"] for observed in asset["observed"]]
    after_sha = content_sha(Path(os.path.realpath(paths[0])))
    after_files = content_files(Path(os.path.realpath(paths[0])))
    append_ledger(
        sandbox,
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
            "change_path": str(args.change),
            "change_sha256": change_sha,
            "note": approval.get("note"),
        },
    )
    row = {
        "kind": "apply",
        "asset_kind": kind,
        "at": at,
        "execution_id": execution_id,
        "approval_id": approval["approval_id"],
        "source": source,
        "proposal_id": args.item,
        "report": report,
        "evidence": require(item, "evidence", f"条目 {args.item}"),
        "prediction": require(item, "prediction", f"条目 {args.item}"),
        "target": {"asset_id": asset["asset_id"], "resolved_path": asset["resolved_path"], "paths": paths},
        "release": release_id,
        "journals": journals,
        "before_sha256": before_sha,
        "before_files": before_files,
        "after_sha256": after_sha,
        "after_files": after_files,
        "home": str(home),
        "phe_home": str(phe),
    }
    append_ledger(sandbox, phe, row)
    print(
        json.dumps(
            {
                "execution_id": execution_id,
                "proposal_id": args.item,
                "source": source,
                "asset_id": asset["asset_id"],
                "release": release_id,
                "before_sha256": row["before_sha256"],
                "before_files": before_files,
                "after_sha256": after_sha,
                "after_files": after_files,
                "journals": journals,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def rebind_restored(writer: Writer, phe: Path, journals: list[Path]) -> None:
    """rollback 会解除绑定；恢复到的若是本 PHE_HOME 的发布，重新绑定，下一次 deploy 才认它是自己的部署。"""
    for journal_path in journals:
        journal = read_json(journal_path)
        for entry in journal["entries"]:
            release_id = release_of(phe, entry["old_target"])
            if release_id is not None:
                bind(writer, phe, journal["agent"], [entry["path"]], release_id)


def cmd_restore(args: argparse.Namespace, roots: list[Path]) -> int:
    sandbox = Sandbox(roots)
    home, phe = roots
    facts = read_ledger(phe)
    items = executions(facts)
    execution = next((item for item in items if item["execution_id"] == args.execution), None)
    if execution is None:
        raise ValueError(f"账本里没有执行：{args.execution}")
    if execution["state"] == "restored":
        raise ValueError(f"{args.execution} 已经恢复过")
    active = [item for item in items if item["state"] == "applied"]
    if active[-1]["execution_id"] != args.execution:
        raise ValueError(f"同一资产只支持逆序恢复：先恢复 {active[-1]['execution_id']}，再恢复 {args.execution}")
    fact = next(item for item in facts if item["kind"] == "apply" and item["execution_id"] == args.execution)
    journals = [phe / relative for relative in fact["journals"]]
    for journal in reversed(journals):
        namespace = argparse.Namespace(journal=journal, home=home, phe_home=phe)
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            rollback(namespace, roots)
        print(captured.getvalue().strip(), file=sys.stderr)
    rebind_restored(sandbox, phe, journals)
    paths = fact["target"]["paths"]
    restored = Path(os.path.realpath(paths[0]))
    row = {
        "kind": "restore",
        "at": utc_now(),
        "execution_id": args.execution,
        "proposal_id": fact["proposal_id"],
        "journals": fact["journals"],
        "restored_sha256": content_sha(restored),
        "restored_files": content_files(restored),
        "release": link_release(phe, paths[0]),
    }
    append_ledger(sandbox, phe, row)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    phe = args.phe_home
    facts = read_ledger(phe)
    items = executions(facts)
    latest = latest_reviews(facts)
    for item in items:
        item["latest_review"] = latest.get(item["execution_id"])
    applies = [fact for fact in facts if fact["kind"] == "apply"]
    targets = []
    if applies:
        for path in applies[-1]["target"]["paths"]:
            real = Path(os.path.realpath(path))
            targets.append(
                {
                    "path": path,
                    "release": link_release(phe, path),
                    "sha256": content_sha(real),
                    "files": content_files(real),
                }
            )
    print(
        json.dumps(
            {
                "phe_home": str(phe),
                "executions": items,
                "active": [item for item in items if item["state"] == "applied"],
                "targets": targets,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


COMMANDS = {"apply": cmd_apply, "restore": cmd_restore, "review": cmd_review}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="闭环切片 2/3：按批准应用、恢复与复盘（只在临时沙箱运行）")
    commands = parser.add_subparsers(dest="cmd", required=True)

    applier = commands.add_parser("apply", help="应用变更包里一条获批条目")
    applier.add_argument("--change", type=Path, required=True, help="机读变更包")
    applier.add_argument("--item", required=True, help="条目 ID，如 P1")
    applier.add_argument("--approval", type=Path, required=True, help="绑定变更包与获批条目的批准记录")
    applier.add_argument("--registry", type=Path, required=True)
    release_tool.add_sandbox_arguments(applier)

    restorer = commands.add_parser("restore", help="按 journal 逆序恢复一次应用")
    restorer.add_argument("--execution", required=True, help="执行 ID，如 x0001")
    release_tool.add_sandbox_arguments(restorer)

    viewer = commands.add_parser("status", help="从账本事实推导执行状态")
    viewer.add_argument("--phe-home", type=Path, required=True, help="PHE 账本根目录")

    planner = commands.add_parser("review-plan", help="列出每个执行的观察窗、原预测和 review 历史（只读）")
    planner.add_argument("--phe-home", type=Path, required=True, help="PHE 账本根目录")

    reviewer = commands.add_parser("review", help="追加一条 review 事实，只记录不判断效果")
    reviewer.add_argument("--execution", required=True, help="执行 ID，如 x0001")
    reviewer.add_argument("--record", type=Path, required=True, help="宿主编写的 review 记录 JSON")
    reviewer.add_argument("--phe-home", type=Path, required=True, help="PHE 账本根目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "review-plan":
            return cmd_review_plan(args)
        roots = phe_roots(args.phe_home) if args.cmd == "review" else sandbox_roots(args.home, args.phe_home)
        return COMMANDS[args.cmd](args, roots)
    except GuardError as exc:
        print(f"change: {exc}", file=sys.stderr)
        return GUARD_EXIT_CODE
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"change: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
