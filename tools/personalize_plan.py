"""R2 产品入口（计划与预览）：读报告、变更包、登记表与入口当前内容，写预览与机读计划（标准库）。

写入范围在计划里定：`ApprovedScope` 只放行「本次计划列明的入口路径（精确匹配）」和「显式 PHE_HOME 产物」，
不按整个 home 放行，也不查真实环境。这与旧沙箱入口（`release_deploy.sandbox_roots` 拒绝真实 home 及其下的
`.claude`、`.codex`、`.pi`、`.phe`、`.agents`）是两套政策，互不影响，本文件不直接调用旧守卫，也不改它；
只有 `source=sandbox_rehearsal` 的批准叠加那个拒绝条件，因为演练批准不是用户批准。

`preview` 只写 `<PHE_HOME>/reports/<cycle>/`：人可读预览、机读计划与计划 sha256。计划绑定报告与变更包的
sha256、每个入口的当前形态与内容 sha256、每条目的当前/预期内容 sha256 与实际 diff；批准记录绑定这份计划的
原始字节，输入或目标变了就要重新预览。恢复预览在 `personalize_restore.py`，绑定账本执行、journal 与恢复后
的实际内容。
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import change
from asset_content import asset_kind, content_files, content_sha, file_diffs, patch_targets, stage_patch
from change_ledger import require
from release_deploy import (
    GuardError,
    Writer,
    current_state,
    load_sibling,
    read_registry,
    refusal_reason,
    under,
    utc_now,
    write_target,
)

release_verify_tool = load_sibling("release_verify")
workspace_tool = load_sibling("workspace")

SCHEMA_VERSION = 1
PLAN_KIND = "phe_product_plan"
RESTORE_PLAN_KIND = "phe_restore_plan"
DIFF_LABELS = {"added": "新增", "deleted": "删除", "modified": "修改"}


class ApprovedScope:
    """产品写入范围：显式 PHE_HOME + 本次计划列明的入口路径（精确匹配，不按整个 home 放行）。"""

    def __init__(self, phe_home: Path, targets: list[Path]) -> None:
        self.phe_home = Path(os.path.realpath(phe_home))
        self.targets = {write_target(Path(target)) for target in targets}

    def check(self, path: Path) -> None:
        written = write_target(Path(path))
        if written in self.targets or under(written, [self.phe_home]):
            return
        for target in self.targets:
            # 原子替换会先在入口同目录建临时链接（`release_deploy.temp_link_path` 的命名）：只放行这些形状。
            if written.parent == target.parent and written.name.startswith(f".{target.name}.phe-tmp-"):
                return
        raise GuardError(f"拒绝写入计划之外的路径：{path}")


def product_writer(phe_home: Path, targets: list[Path] | None = None, source: str | None = None) -> Writer:
    """产品写入策略：显式 PHE_HOME + 计划列明的入口。

    `source=sandbox_rehearsal` 叠加旧沙箱的真实环境拒绝条件（`release_deploy.refusal_reason`）：演练批准不是
    用户批准，不获得真实 home、`.phe` 或 Host 入口的写入能力；`source=user` 按计划列明的精确范围放行。
    """
    if source == "sandbox_rehearsal":
        for path in [phe_home, *(targets or [])]:
            reason = refusal_reason(Path(path))
            if reason is not None:
                raise GuardError(f"演练批准不能写入真实环境：{reason}")
    return Writer(ApprovedScope(phe_home, targets or []).check)


def require_planned_workspace(plan: dict, phe_home: Path) -> None:
    """执行写入前的核对：实际规范化 PHE_HOME 必须就是这份计划所在的工作区。"""
    recorded = Path(require(plan, "phe_home", "计划"))
    if Path(os.path.realpath(recorded)) != Path(os.path.realpath(phe_home)):
        raise ValueError(f"计划属于 PHE_HOME {recorded}，本次执行的是 {phe_home}：请重新预览")


def json_text(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_document(writer: Writer, path: Path, text: str) -> str:
    """写一份 UTF-8 文档，返回落盘后的 sha256：计划与预览的绑定都以原始字节为准。"""
    data = text.encode("utf-8")
    writer.write_bytes(path, data)
    return change.sha256_bytes(data)


def sha_of(path: Path) -> str | None:
    """入口或恢复目标的当前内容 sha256：单文件取文件字节，目录（skill）取整棵摘要。"""
    return content_sha(path)


def workspace(phe_home: Path) -> Path:
    phe = Path(os.path.realpath(phe_home))
    workspace_tool.require_workspace(phe)
    return phe


def registry_path(phe: Path, value: Path | None) -> Path:
    return value or phe / workspace_tool.REGISTRY_RELATIVE


def read_plan(path: Path, kind: str) -> tuple[dict, str]:
    data = path.read_bytes()
    plan = json.loads(data.decode("utf-8"))
    if plan.get("kind") != kind:
        raise ValueError(f"{path} 不是 {kind}：{plan.get('kind')}")
    return plan, change.sha256_bytes(data)


# 应用预览：只读输入，预期内容在系统临时目录里整棵预演。


def preview_writer() -> Writer:
    """预览暂存只落系统临时目录：不在 PHE_HOME 也不在 Host，因此不套产品写入范围。"""
    return Writer(lambda path: None)


def preview_item(patch: str, base: Path) -> dict:
    """在系统临时目录里预演这条 patch：只算预期内容 sha256、文件集合与逐文件 diff。

    目录资产先整棵复制再打 patch，所以预览包含引用文件的新增与删除；不写 PHE_HOME，也不写 Host。
    """
    with tempfile.TemporaryDirectory(prefix="phe-preview-") as directory:
        staged = stage_patch(preview_writer(), Path(directory), patch, base)
        return {
            "expected_sha256": content_sha(staged),
            "expected_files": content_files(staged),
            "diffs": file_diffs(base, staged),
        }


def entry_rows(asset: dict) -> list[dict]:
    """每个 Host 入口的当前形态：软链或缺失才可写；普通文件与普通目录不覆盖。"""
    rows = []
    for observed in asset["observed"]:
        path = Path(observed["path"])
        state, target = current_state(path)
        rows.append(
            {
                "agent": observed["agent"],
                "path": str(path),
                "state": state,
                "target": target,
                "sha256": sha_of(path),
                "files": content_files(path),
                "writable": state != "other",
                "note": "入口是普通文件或目录：PHE 不覆盖，这条入口不在本次写入范围" if state == "other" else None,
            }
        )
    return rows


def require_same_content(entries: list[dict], content_sha: str) -> None:
    """共享入口必须都读同一份内容：预览的 diff 只按一份基线算，分叉时不生成可执行计划。

    R2 只支持多入口共享一份内容，不支持多来源合并：某个入口读的已经是另一份内容时，应用会把它现有的
    内容直接换成这份 diff 的结果，展示的改动就不等于那个入口实际发生的事。
    """
    differing = [
        f"{entry['path']}（{entry['sha256']}）"
        for entry in entries
        if entry["writable"] and entry["sha256"] != content_sha
    ]
    if differing:
        raise ValueError(
            "这些入口读的内容与计划要改的那份不同，不支持多来源合并："
            + "；".join(differing)
            + "。请先让它们指向同一份内容，再重新预览"
        )


def single_asset(package: dict, registry: dict) -> dict:
    """R2 一次只处理一个资产：所有条目必须指向同一个 asset_id。"""
    assets = []
    for item in require(package, "items", "变更包"):
        target = require(item, "target", f"条目 {require(item, 'item_id', '变更包条目')} 的 target")
        assets.append(require(target, "asset_id", "条目的 target"))
    if len(set(assets)) != 1:
        raise ValueError(f"R2 一次预览只处理一个资产，变更包里出现了：{sorted(set(assets))}")
    return change.target_asset(registry, assets[0])


def missing_notes(item_id: str, item: dict) -> list[dict]:
    """缺项清单：三行说明没写全或缺少证据指针时列出，供报告补全（正常契约，不是错误）。"""
    notes = []
    prediction = item.get("prediction")
    if not isinstance(prediction, dict):
        notes.append({"item_id": item_id, "missing": ["prediction（三行说明对象）"]})
    else:
        notes.extend(
            {"item_id": item_id, "missing": [f"prediction.{key}"]}
            for key in ("why", "expected", "verify")
            if not prediction.get(key)
        )
    if not item.get("evidence"):
        notes.append({"item_id": item_id, "missing": ["evidence（证据指针）"]})
    return notes


def build_plan(phe: Path, args: argparse.Namespace) -> dict:
    package, change_sha = change.read_change(args.change)
    report = change.check_report(package)
    registry = read_registry(registry_path(phe, args.registry))
    asset = single_asset(package, registry)
    if asset_kind(phe, asset) == "mcp":
        # MCP 条目没有入口链接，托管对象是各 Host 配置里的一个 JSON 成员。
        import personalize_mcp_plan as mcp_planning

        return mcp_planning.build_plan(phe, args, package, report, change_sha, asset)
    content_file = change.effective_file(asset)
    kind = asset_kind(phe, asset)
    base_sha = sha_of(content_file)
    if base_sha is None:
        raise ValueError(f"入口内容读不到（断链或缺失），先恢复入口再预览：{content_file}")
    base_files = content_files(content_file)
    entries = entry_rows(asset)
    require_same_content(entries, base_sha)
    items, missing = [], []
    for item in require(package, "items", "变更包"):
        item_id = require(item, "item_id", "变更包条目")
        patch = require(item, "patch", f"条目 {item_id}")
        missing.extend(missing_notes(item_id, item))
        targets = patch_targets(patch)
        row = {
            "item_id": item_id,
            "evidence": require(item, "evidence", f"条目 {item_id}"),
            "prediction": require(item, "prediction", f"条目 {item_id}"),
            "patch_targets": targets,
            "current_sha256": base_sha,
            "current_files": base_files,
        }
        try:
            # 每条目都按入口当前内容预演：同一资产的多条目各自独立，只有当前打得上的一条可应用。
            preview = preview_item(patch, content_file)
        except ValueError as error:
            # 打不上当前内容：标成不适用，不挡同包其他条目。
            row.update(state="not_applicable", reason=str(error).splitlines()[0])
        else:
            row.update(
                state="applicable",
                expected_sha256=preview["expected_sha256"],
                expected_files=preview["expected_files"],
                diffs=preview["diffs"],
                diff="".join(entry["diff"] for entry in preview["diffs"]),
            )
        items.append(row)
    kind_note = "目录资产整棵重建（含引用文件）" if kind == "skill" else "单文件资产"
    content_entry = (
        f"assets/{asset['asset_id']}/content/（整棵替换）"
        if kind == "skill"
        else f"assets/{asset['asset_id']}/content/{Path(asset['resolved_path']).name}"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "cycle": args.cycle,
        "created_at": utc_now(),
        "phe_home": str(phe),
        "report": report,
        "change": {"path": str(Path(args.change)), "sha256": change_sha},
        "asset": {
            "asset_id": asset["asset_id"],
            "kind": kind,
            "resolved_path": asset["resolved_path"],
            "shared": len(entries) > 1,
            "entries": entries,
        },
        "items": items,
        "host_writes": [entry["path"] for entry in entries if entry["writable"]],
        "phe_writes": [
            content_entry,
            kind_note,
            f"assets/{asset['asset_id']}/asset.json",
            "releases/<下一个编号>/ 与 releases/high_water.json",
            "staging/<执行 ID>/",
            "journal/<时间戳>.json",
            "bindings.json",
            "ledger.jsonl",
        ],
        "restore": {
            "method": "journal",
            "note": "恢复把每个入口链接指回应用前的目标（journal 记录）；共享来源文件本身不改，原字节不变",
        },
        "missing": missing,
    }


def render_preview(plan: dict, plan_sha: str) -> str:
    if plan["asset"].get("kind") == "mcp":
        import personalize_mcp_plan as mcp_planning

        return mcp_planning.render_preview(plan, plan_sha)
    asset = plan["asset"]
    report = plan["report"]
    entries = asset["entries"]
    lines = [
        f"# 应用预览 · 周期 {plan['cycle']}",
        "",
        f"- 生成时间：{plan['created_at']}（工具按输入生成，不是自动诊断；报告本身是宿主写的材料）",
        f"- PHE_HOME：`{plan['phe_home']}`",
        f"- 报告：`{report['path']}` sha256 `{report['sha256']}`",
        f"- 变更包：`{plan['change']['path']}` sha256 `{plan['change']['sha256']}`",
        f"- 计划 sha256：`{plan_sha}`（批准记录绑定这份计划的原始字节）",
        f"- 资产：`{asset['asset_id']}` ← `{asset['resolved_path']}`（{'多个入口共享' if asset['shared'] else '单一入口'}）",
        "",
        "## 入口与当前内容",
        "",
        "| Agent | 入口 | 当前形态 | 当前文件 | 当前 sha256 | 应用后 |",
        "|---|---|---|---|---|---|",
    ]
    for entry in entries:
        current = f"软链 → `{entry['target']}`" if entry["state"] == "symlink" else entry["state"]
        after = "指向新发布" if entry["writable"] else f"不改（{entry['note']}）"
        files = f"{len(entry['files'])} 个" if entry["files"] else "无"
        lines.append(f"| {entry['agent']} | `{entry['path']}` | {current} | {files} | `{entry['sha256']}` | {after} |")
    lines.extend(
        [
            "",
            "共享关系："
            + (
                "这些入口当前读同一份内容；应用后它们都指向同一份新发布，共享来源文件本身不改。"
                if asset["shared"]
                else "这个资产只有一个入口。"
            ),
            "",
            "## 逐条 diff 与三行说明",
        ]
    )
    for item in plan["items"]:
        if item["state"] != "applicable":
            lines.extend(
                [
                    "",
                    f"### {item['item_id']}（当前不适用）",
                    "",
                    f"- 打不上当前内容：{item['reason']}",
                    "- 这不代表已经生效；要改就按当前内容重新生成变更包，再预览、再批准。",
                ]
            )
            continue
        prediction = item["prediction"] if isinstance(item["prediction"], dict) else {}
        evidence = item["evidence"]
        changed = "、".join(f"`{row['path']}`（{DIFF_LABELS[row['state']]}）" for row in item["diffs"])
        lines.extend(
            [
                "",
                f"### {item['item_id']}",
                "",
                f"- 为什么改：{prediction.get('why', '（未写）')}",
                f"- 改后期待什么：{prediction.get('expected', '（未写）')}",
                f"- 凭什么判断：{prediction.get('verify', '（未写）')}",
                f"- 证据：{'、'.join(evidence) if isinstance(evidence, list) else evidence}",
                f"- 当前内容 sha256：`{item['current_sha256']}` → 应用后 `{item['expected_sha256']}`",
                f"- 改动文件：{changed}",
            ]
        )
        for row in item["diffs"]:
            lines.extend(["", f"`{row['path']}`", "", "```diff", row["diff"].rstrip("\n"), "```"])
    lines.extend(["", "## 缺项清单（正常契约，补全后再批准更稳）", ""])
    if plan["missing"]:
        lines.extend(f"- {note['item_id']}：缺 {'、'.join(note['missing'])}" for note in plan["missing"])
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 写入范围",
            "",
            "Host 入口（只这些）：",
            "",
            *[f"- `{path}`" for path in plan["host_writes"]],
            "",
            "PHE_HOME 产物（相对 `<PHE_HOME>`）：",
            "",
            *[f"- `{path}`" for path in plan["phe_writes"]],
            "",
            f"恢复方式：{plan['restore']['note']}。恢复也要单独预览并再次批准。",
            "",
            "## 说明",
            "",
            "- 应用成功只说明入口指向了 PHE 发布；Host 是否加载、行为是否改善都没有验证，复盘记 `observe` / `unknown`。",
            "- 计划绑定变更包与报告的 sha256，以及每个入口当前内容的 sha256：输入或目标变了就要重新预览，不能拿旧计划执行另一份 diff。",
            "- 变更包引用的报告必须是当前这一版；缺项只是提示，不代表条目不可批准。",
        ]
    )
    return "\n".join(lines) + "\n"


def cmd_preview(args: argparse.Namespace) -> int:
    phe = workspace(args.phe_home)
    plan = build_plan(phe, args)
    directory = phe / "reports" / args.cycle
    writer = product_writer(phe)
    plan_path = directory / "plan.json"
    plan_sha = write_document(writer, plan_path, json_text(plan))
    writer.write_bytes(directory / "plan.sha256", (plan_sha + "\n").encode("utf-8"))
    preview_path = directory / "preview.md"
    write_document(writer, preview_path, render_preview(plan, plan_sha))
    print(
        json.dumps(
            {
                "cycle": plan["cycle"],
                "plan": str(plan_path),
                "plan_sha256": plan_sha,
                "preview": str(preview_path),
                "host_writes": plan["host_writes"],
                "items": [item["item_id"] for item in plan["items"]],
                "missing": plan["missing"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
