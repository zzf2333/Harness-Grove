#!/usr/bin/env python3
"""R2 产品批准入口：在自己的 PHE_HOME 与 Host 入口上预览 → 批准 → 应用 → 复盘 → 恢复（标准库）。

写入政策分两套，互不影响：

- 旧沙箱入口（不改）：`tools/release.py`、`tools/change.py` 走 `release_deploy.sandbox_roots`，拒绝真实 home
  及其下的 `.claude`、`.codex`、`.pi`、`.phe`、`.agents`、`AI/temper`；`deploy` 的 `--home` 同时是盘点根和
  写入根。本文件不调用它，也不改它的守卫。
- 产品入口（本文件与 `personalize_plan.py`、`personalize_apply.py`）：`ApprovedScope` 只放行「计划列明的
  入口路径（精确匹配）」和「显式 PHE_HOME 产物」，不按整个 home 放行，也不查真实环境。用户批准的对象是
  计划原始字节（`plan_sha256`）和条目；`source=user` 由宿主按用户原话写成记录，工具不自动造用户批准；
  `sandbox_rehearsal` 仍是演练批准。

命令：`preview`（写预览与机读计划，不写 Host）、`apply`（只写计划列明的入口与 PHE_HOME 产物）、
`restore-preview` / `restore`（恢复也先预览、再批准）、`status` / `review-plan` / `review`（同一本账本）、
`digest`（摘要写 `<PHE_HOME>/episodes/<cycle>/`）、`cycle-record` / `cycle-plan`（两周期延续）、
`mcp-adopt` / `mcp-drift`（R3b：登记已存在的 MCP server 条目、看条目级漂移；写入仍走上面的 preview / apply）。

范围：R2 是单个 instructions 资产 + 多个 Host 入口共享同一来源；R3a 是 Skill 目录资产，入口仍是链接。
R3b 只处理 `kind=mcp` 的**已存在** server 条目：Pi 的 `~/.pi/agent/mcp.json` 与项目 `.pi/mcp.json`、
Claude Code 项目级 `.mcp.json`。写入只替换 `mcpServers` 下托管成员的值跨度，未托管条目与文件外围字节不变；
新增 / 删除条目与 Codex 的 TOML 配置本片不支持，也不验证 Host 是否加载或能否连上 server。启停按目标分开：
Pi 写条目的 `enabled`，Claude Code 写 `~/.claude.json` 项目条目的 `disabledMcpServers`（要 `--project-key`）。
产物路径、证据指针和观察窗的含义见 [README](https://github.com/) 的 R2 流程与 [Skill §11–§13]。
"""

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import change
import change_review
import personalize_apply as applying
import personalize_mcp as mcp_entry
import personalize_plan as planning
import personalize_restore as restoring
from change_ledger import append_ledger, read_ledger, require
from history_digest import build_digest
from release_deploy import GUARD_EXIT_CODE, GuardError, load_sibling

workspace_tool = load_sibling("workspace")

SCHEMA_VERSION = planning.SCHEMA_VERSION
# 周期记录里的信息状态沿用 SPEC 的三类：用户明确偏好、会话观察、假设（收益待验证）。
EVIDENCE_KINDS = ("user_preference", "observation", "hypothesis")
CYCLE_NOTE = (
    "cycle.at 与 review.at 是记录时间，不是已处理证据水位；观察窗是应用到恢复（或至今）的候选取样范围。"
    "本周期处理范围只看 processed_evidence 里的源文件路径、行号与原文 ts，只收上一周期没处理过的证据。"
)


# 账本视图与复盘：复用 change_ledger / change_review 的推导与账本行形状。


def cmd_status(args: argparse.Namespace) -> int:
    """账本视图沿用 `change.cmd_status`；只补一列批准来源，区分真实确认与演练。"""
    phe = planning.workspace(args.phe_home)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = change.cmd_status(argparse.Namespace(phe_home=phe))
    if code != 0:
        return code
    status = json.loads(buffer.getvalue())
    sources = {fact["execution_id"]: fact["source"] for fact in read_ledger(phe) if fact["kind"] == "apply"}
    for item in status["executions"]:
        item["source"] = sources.get(item["execution_id"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def cmd_review_plan(args: argparse.Namespace) -> int:
    return change_review.cmd_review_plan(argparse.Namespace(phe_home=planning.workspace(args.phe_home)))


def cmd_review(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    record, record_sha = change_review.read_review(args.record)
    row = change_review.build_review_row(read_ledger(phe), args.execution, record, record_sha, str(args.record))
    append_ledger(planning.product_writer(phe), phe, row)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


# 周期证据：摘要写进 PHE_HOME；周期记录只追加账本事实，不建第二套数据库。


def cmd_digest(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    blocks, index = build_digest(args.host, Path(args.root), args.since, args.exclude)
    directory = phe / "episodes" / args.cycle / "digest"
    writer = planning.product_writer(phe)
    for name, text in blocks:
        writer.write_bytes(directory / name, text.encode("utf-8"))
    writer.write_json(directory / f"{args.host}-index.json", index)
    print(
        f"摘要：{args.host} 入选 {index['sessions_included']}/{index['sessions_total']} 个会话，"
        f"{len(blocks)} 块，跳过 {index['sessions_skipped']}，不可用 {index['sessions_unavailable']} → {directory}"
    )
    return 0


def read_cycle_record(path: Path, cycle: str) -> tuple[dict, str]:
    data = path.read_bytes()
    record = json.loads(data.decode("utf-8"))
    if require(record, "schema_version", "周期记录") != SCHEMA_VERSION:
        raise ValueError(f"周期记录 schema_version 只支持 {SCHEMA_VERSION}")
    recorded_cycle = require(record, "cycle", "周期记录")
    if recorded_cycle != cycle:
        raise ValueError(f"周期记录写的是 {recorded_cycle}，与 --cycle {cycle} 不一致")
    require(record, "recorded_at", "周期记录")
    require(record, "summary", "周期记录")
    for entry in require(record, "processed_evidence", "周期记录"):
        source = require(entry, "source_path", "processed_evidence")
        require(entry, "lines", "processed_evidence")
        kind = require(entry, "kind", "processed_evidence")
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"{source} 的 kind 只能是 {' / '.join(EVIDENCE_KINDS)}：{kind}")
        require(entry, "from_ts", "processed_evidence")
        require(entry, "to_ts", "processed_evidence")
    require(record, "next_observations", "周期记录")
    return record, change.sha256_bytes(data)


def processed_lines(facts: list[dict]) -> dict[str, set[int]]:
    seen: dict[str, set[int]] = {}
    for fact in facts:
        if fact["kind"] != "cycle":
            continue
        for entry in fact["processed_evidence"]:
            seen.setdefault(entry["source_path"], set()).update(entry["lines"])
    return seen


def repeated_evidence(record: dict, seen: dict[str, set[int]]) -> list[str]:
    """上一周期已经处理过的证据行：本周期只收新增，重复计入等于重复使用旧证据。"""
    repeated = []
    for entry in record["processed_evidence"]:
        overlap = sorted(set(entry["lines"]) & seen.get(entry["source_path"], set()))
        if overlap:
            repeated.append(f"{entry['source_path']}:L{'、L'.join(str(line) for line in overlap)}")
    return repeated


def cmd_cycle_record(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    record, record_sha = read_cycle_record(args.record, args.cycle)
    facts = read_ledger(phe)
    repeated = repeated_evidence(record, processed_lines(facts))
    if repeated:
        raise ValueError(f"这些证据行已经在上一周期处理过，本周期只记新增：{'；'.join(repeated)}")
    stored = phe / "episodes" / args.cycle / "cycle.json"
    writer = planning.product_writer(phe)
    planning.write_document(writer, stored, planning.json_text(record))
    row = {
        "kind": "cycle",
        "at": record["recorded_at"],
        "cycle": args.cycle,
        "record_path": str(args.record),
        "record_sha256": record_sha,
        "stored_path": str(stored.relative_to(phe)),
        "summary": record["summary"],
        "processed_evidence": record["processed_evidence"],
        "next_observations": record["next_observations"],
    }
    append_ledger(writer, phe, row)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_cycle_plan(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    facts = read_ledger(phe)
    previous = [
        {
            "cycle": fact["cycle"],
            "at": fact["at"],
            "summary": fact["summary"],
            "processed_evidence": fact["processed_evidence"],
            "next_observations": fact["next_observations"],
        }
        for fact in facts
        if fact["kind"] == "cycle"
    ]
    print(
        json.dumps(
            {
                "phe_home": str(phe),
                "cycle": args.cycle,
                "note": CYCLE_NOTE,
                "previous_cycles": previous,
                "executions": change_review.review_plan(facts),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


COMMANDS = {
    "preview": planning.cmd_preview,
    "apply": applying.cmd_apply,
    "restore-preview": restoring.cmd_restore_preview,
    "restore": applying.cmd_restore,
    "status": cmd_status,
    "review-plan": cmd_review_plan,
    "review": cmd_review,
    "digest": cmd_digest,
    "mcp-adopt": mcp_entry.cmd_mcp_adopt,
    "mcp-drift": mcp_entry.cmd_mcp_drift,
    "cycle-record": cmd_cycle_record,
    "cycle-plan": cmd_cycle_plan,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="R2 产品批准入口：在显式 PHE_HOME 与批准的计划范围内预览、应用、复盘与恢复"
    )
    commands = parser.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        workspace_tool.add_phe_home(sub)
        return sub

    preview = add("preview", "读报告、变更包与入口当前内容，写预览与机读计划（不写 Host）")
    preview.add_argument("--change", type=Path, required=True, help="机读变更包")
    preview.add_argument("--cycle", required=True, help="周期目录名，如 c1；产物写 reports/<cycle>/")
    preview.add_argument("--registry", type=Path, help=f"登记表，缺省 <PHE_HOME>/{workspace_tool.REGISTRY_RELATIVE}")

    apply = add("apply", "执行计划里一条获批条目；只写计划列明的入口与 PHE_HOME 产物")
    apply.add_argument("--plan", type=Path, required=True, help="preview 写出的计划 JSON")
    apply.add_argument("--item", required=True, help="条目 ID，如 P1")
    apply.add_argument("--approval", type=Path, required=True, help="绑定计划原始字节与获批条目的批准记录")
    apply.add_argument("--registry", type=Path, help=f"登记表，缺省 <PHE_HOME>/{workspace_tool.REGISTRY_RELATIVE}")

    restore_preview = add("restore-preview", "读账本执行、journal 与入口当前内容，写恢复预览与计划")
    restore_preview.add_argument("--execution", required=True, help="执行 ID，如 x0001")
    restore_preview.add_argument("--cycle", required=True, help="周期目录名，如 c2")

    restore = add("restore", "按批准的恢复计划把入口链接指回应用前的目标")
    restore.add_argument("--execution", required=True, help="执行 ID，如 x0001")
    restore.add_argument("--plan", type=Path, required=True, help="restore-preview 写出的恢复计划")
    restore.add_argument("--approval", type=Path, required=True, help="绑定恢复计划原始字节的批准记录")

    add("status", "从账本事实推导执行状态")
    add("review-plan", "列出每个执行的观察窗、原预测和 review 历史（只读）")
    review = add("review", "追加一条 review 事实，只记录不判断效果")
    review.add_argument("--execution", required=True, help="执行 ID，如 x0001")
    review.add_argument("--record", type=Path, required=True, help="宿主编写的 review 记录 JSON")

    digest = add("digest", "把会话整理成脱敏摘要，写进 <PHE_HOME>/episodes/<cycle>/digest")
    digest.add_argument("--cycle", required=True, help="周期目录名，如 c1")
    digest.add_argument("--host", choices=["claude", "codex", "pi"], required=True)
    digest.add_argument("--root", type=Path, required=True, help="Host 配置根，例如隔离的合成 Host 树")
    digest.add_argument("--since", help="`7d` 或 `YYYY-MM-DD`")
    digest.add_argument("--exclude", action="append", default=[], help="相对 --root 的 glob，可重复")

    record = add("cycle-record", "把本周期的证据范围、摘要与下次观察项记进账本")
    record.add_argument("--cycle", required=True, help="周期目录名，如 c1")
    record.add_argument("--record", type=Path, required=True, help="宿主编写的周期记录 JSON")

    cycle_plan = add("cycle-plan", "下一周期先读上一周期的证据范围、预测与结论（只读）")
    cycle_plan.add_argument("--cycle", required=True, help="本周期目录名，如 c2")

    adopt = add("mcp-adopt", "登记已存在的 MCP server 条目：写逻辑定义与目标绑定，不碰 Host")
    adopt.add_argument("--server", required=True, help="mcpServers 里的条目名，如 demo")
    adopt.add_argument(
        "--target",
        action="append",
        required=True,
        help="adapter=路径，可重复；adapter 取 pi / claude / claude-toggle，路径形状见 README 的 R3b 一节",
    )
    adopt.add_argument(
        "--project-key",
        help="claude-toggle 目标的 ~/.claude.json 项目键（工作目录路径字符串）；只对启停目标生效",
    )
    adopt.add_argument("--registry", type=Path, help=f"登记表，缺省 <PHE_HOME>/{workspace_tool.REGISTRY_RELATIVE}")

    drift = add("mcp-drift", "按托管定义渲染后与各 Host 的条目比较，只报字段名（只读）")
    drift.add_argument("--asset-id", help="MCP 资产 ID；PHE_HOME 里只有一条 MCP 资产时可省略")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.phe_home = workspace_tool.resolve_phe_home(parser, args.phe_home)
    try:
        return COMMANDS[args.cmd](args)
    except GuardError as exc:
        print(f"personalize: {exc}", file=sys.stderr)
        return GUARD_EXIT_CODE
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"personalize: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
