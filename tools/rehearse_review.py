#!/usr/bin/env python3
"""闭环切片 3 演练：在切片 2 的账本副本上读观察窗、写 review，并跑【合成】的观察窗取样样例。

只读 `--source`（切片 2 演练的 `<phe>` 目录），把它复制到 `--out/phe`，原目录不改。复制后先
`review-plan` 列出两个已恢复执行的观察窗、原预测和证据，再对真实演练执行 `x0001` 写一条
`verdict=observe` / `prediction_met=unknown` 的 review：它只在临时沙箱 home 应用过，真实 Host 从未加载，
也没有基线。然后在一套【合成】沙箱（`rehearse_review_synthetic.py`）里 apply 一条合成条目后不 restore，
`to` 是 `null`，取样上界因此取演练声明的【模拟复盘时刻】`cutoff = applied_at + 60 分钟`，窗口是
`[应用时刻, cutoff)`；合成会话的时间戳按相对实际 `applied_at` 的偏移生成，按窗口两端取样后手算 P1 口径的
`cwd does not exist` 次数，把合成 review 写进合成账本。零宽窗口样例证明窗口外的命中（含恰好等于端点的记录）
不计入观察。
合成产物与真实演练账本分开，都标注【合成】，只证明复盘流程可运行。工件留在 `--out`，不清理。
不写真实 `~/.pi`、`~/.codex`、`~/.claude` 或 temper。
"""

import argparse
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rehearse_change import REPO, Rehearsal, git, ledger, sha256, write
from rehearse_review_report import render_report
from rehearse_review_synthetic import (
    CUTOFF_MINUTES,
    SYNTHETIC_ITEM,
    SYNTHETIC_SESSION,
    ZERO_WIDTH_SESSION,
    build_sandbox,
    cutoff_at,
    phrase_lines,
    sample_phrase,
    session_rows,
    write_session,
)


def copy_source(run: Rehearsal, source: Path) -> str:
    """复制切片 2 的 phe 目录；账本字节逐字节相同，原目录只读。"""
    before = sha256((source / "ledger.jsonl").read_bytes())
    shutil.copytree(source, run.out / "phe", symlinks=True)
    run.check(sha256((run.out / "phe/ledger.jsonl").read_bytes()) == before, "复制的账本字节与切片 2 账本一致")
    return before


def write_record(directory: Path, name: str, record: dict) -> Path:
    path = directory / name
    write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def run_rehearsal(run: Rehearsal, source: Path) -> dict:
    out = run.out
    source_ledger = copy_source(run, source)
    phe = out / "phe"
    facts = ledger(phe)
    applies = {fact["execution_id"]: fact for fact in facts if fact["kind"] == "apply"}
    restores = {fact["execution_id"]: fact for fact in facts if fact["kind"] == "restore"}

    # a. review-plan：两个已恢复执行的观察窗、原预测、证据和报告引用。
    plan = run.cli_json("review-plan", "change.py", "review-plan", "--phe-home", phe)
    run.check(
        [entry["execution_id"] for entry in plan["executions"]] == ["x0001", "x0002"],
        f"review-plan 列出两个执行（实际 {[entry['execution_id'] for entry in plan['executions']]}）",
    )
    run.check(
        [entry["state"] for entry in plan["executions"]] == ["restored", "restored"],
        "复制来的两个执行都还是 restored",
    )
    run.check("观察窗" in plan["note"], "review-plan 说明观察窗只是候选取样范围，不证明 Host 加载过配置")
    first = plan["executions"][0]
    run.check(first["prediction"] == applies["x0001"]["prediction"], "review-plan 原样回显账本里的原预测")
    run.check(first["evidence"] == applies["x0001"]["evidence"], "review-plan 回显证据指针")
    run.check(first["report"] == applies["x0001"]["report"], "review-plan 回显报告引用")
    run.check(
        first["observation_window"] == {"from": applies["x0001"]["at"], "to": restores["x0001"]["at"]},
        "观察窗 = 应用时刻到恢复时刻",
    )
    run.check(first["reviews"] == [] and first["latest_review"] is None, "复制来的账本还没有 review")
    status_before = run.cli_json("status（review 前）", "change.py", "status", "--phe-home", phe)

    # b. 真实演练执行：只在临时沙箱应用过，证据不足，记 observe / unknown。
    record = {
        "verdict": "observe",
        "prediction_met": "unknown",
        "reason": (
            "该执行只在临时沙箱 home 应用，真实 Host 从未加载，原预测基线是待观察，没有可比基线；"
            "现有材料不能判断 cwd does not exist 的次数是否变化"
        ),
        "observations": [],
        "next": (
            "需要用户批准真实应用后，按 prediction.verify 在观察窗（应用到恢复）内统计 cwd does not exist 次数，"
            "并抽查审查交接是否写明有效 cwd 与提交范围；先补基线再判断"
        ),
    }
    path = write_record(out / "inputs", "review-x0001-observe.json", record)
    row = run.cli_json(
        "review x0001", "change.py", "review", "--execution", "x0001", "--record", path, "--phe-home", phe
    )
    run.check(
        (row["kind"], row["execution_id"], row["proposal_id"]) == ("review", "x0001", "P1"),
        "review 事实带上 kind / execution_id / proposal_id",
    )
    run.check(row["record"] == record, "review 原样保存宿主写的记录")
    run.check(row["record_sha256"] == sha256(path.read_bytes()), "review 绑定记录原始字节 sha256")
    run.check(row["record_path"] == str(path), "review 记下记录文件路径")

    after_facts = ledger(phe)
    run.check(after_facts[: len(facts)] == facts, "既有账本行一字不改")
    run.check(
        [fact["kind"] for fact in after_facts] == [fact["kind"] for fact in facts] + ["review"],
        "账本只追加一条 review 事实",
    )
    status_after = run.cli_json("status（review 后）", "change.py", "status", "--phe-home", phe)
    run.check(status_after["targets"] == status_before["targets"], "review 前后入口的发布与 sha256 不变")
    run.check(
        [entry["state"] for entry in status_after["executions"]] == ["restored", "restored"],
        "review 不改变 restored 状态",
    )
    run.check(
        status_after["executions"][0]["latest_review"]["record"]["verdict"] == "observe", "status 显示 latest_review"
    )
    run.check(status_after["executions"][1]["latest_review"] is None, "未 review 的执行 latest_review 为 null")
    plan = run.cli_json("review-plan（review 后）", "change.py", "review-plan", "--phe-home", phe)
    run.check(plan["executions"][0]["latest_review"] == row, "review-plan 列出该执行的 review 并标为最新")

    # c. 【合成】独立沙箱：apply S1 后不 restore，窗口 = [应用时刻, 模拟复盘时刻)。
    synthetic = out / "synthetic"
    synthetic.mkdir()
    paths = build_sandbox(run, synthetic)
    applied = run.cli_json(
        "【合成】apply S1",
        "change.py",
        "apply",
        "--change",
        paths["change"],
        "--item",
        SYNTHETIC_ITEM,
        "--approval",
        paths["approval"],
        "--registry",
        paths["registry"],
        "--phe-home",
        paths["phe"],
        "--home",
        paths["home"],
    )
    run.check(applied["execution_id"] == "x0001", f"【合成】S1 的执行 ID=x0001（实际 {applied['execution_id']}）")
    applied_at = next(fact["at"] for fact in ledger(paths["phe"]) if fact["kind"] == "apply")
    cutoff = cutoff_at(applied_at)
    synthetic_plan = run.cli_json("【合成】review-plan", "change.py", "review-plan", "--phe-home", paths["phe"])
    window = synthetic_plan["executions"][0]["observation_window"]
    run.check(window == {"from": applied_at, "to": None}, f"【合成】未恢复，`to` 是 null（实际 {window}）")
    write_session(paths["session"], session_rows(window["from"], SYNTHETIC_SESSION))
    sampled = sample_phrase(paths["session"], window, cutoff)
    expected_before, expected_inside, expected_after = phrase_lines(SYNTHETIC_SESSION, CUTOFF_MINUTES)
    run.check(sampled["before"] == expected_before, f"【合成】窗口前的命中行号（实际 {sampled['before']}）")
    run.check(sampled["inside"] == expected_inside, f"【合成】窗口内的命中行号（实际 {sampled['inside']}）")
    run.check(
        sampled["after"] == expected_after,
        f"【合成】晚于模拟复盘时刻 {cutoff} 的命中不算窗口内（实际窗口后 {sampled['after']}）",
    )
    inside = len(sampled["inside"])
    verdict = "keep" if inside == 0 else "adjust"
    prediction_met = "met" if inside == 0 else "not_met"
    synthetic_record = {
        "verdict": verdict,
        "prediction_met": prediction_met,
        "reason": (
            f"【合成】模拟复盘时刻 cutoff = {cutoff}（= 应用时刻 + {CUTOFF_MINUTES} 分钟，不是实际至今）；"
            f"按 prediction.verify 在窗口 [{window['from']}, {cutoff}) 内统计：窗口前 {len(sampled['before'])} 次、"
            f"窗口内 {inside} 次、窗口后 {len(sampled['after'])} 次 cwd does not exist；合成数据只证明取样与记账能跑通，"
            "不证明规则有效"
        ),
        "observations": [f"{paths['session']}:L{number}" for number in sampled["inside"]],
        "next": (
            "【合成】真实判断要用户批准真实应用后按同一口径统计（未恢复时上界取本次复盘时刻），"
            "并抽查审查交接是否写明有效 cwd 与提交范围"
        ),
    }
    synthetic_path = write_record(synthetic / "inputs", "review-synthetic.json", synthetic_record)
    synthetic_row = run.cli_json(
        "【合成】review x0001",
        "change.py",
        "review",
        "--execution",
        "x0001",
        "--record",
        synthetic_path,
        "--phe-home",
        paths["phe"],
    )
    run.check(
        (synthetic_row["record"]["verdict"], synthetic_row["record"]["prediction_met"]) == (verdict, prediction_met),
        f"【合成】review 按窗口内计数推导：{len(sampled['before'])} 前 / {inside} 内 / {len(sampled['after'])} 后"
        f" → {verdict} / {prediction_met}",
    )
    run.check(
        cutoff in synthetic_row["record"]["reason"] and window["from"] in synthetic_row["record"]["reason"],
        "【合成】review 记录写明窗口下界与模拟复盘时刻上界（record 无专用字段，写进 reason）",
    )
    run.check(
        [fact["kind"] for fact in ledger(paths["phe"])] == ["approval", "apply", "review"],
        "【合成】合成账本只有 approval / apply / review 三条事实",
    )
    run.check(ledger(phe) == after_facts, "合成 review 不混入真实演练账本")

    # d. 合成账本的 status 显示 latest_review，applied 状态不变。
    synthetic_status = run.cli_json("status（合成账本）", "change.py", "status", "--phe-home", paths["phe"])
    run.check(
        synthetic_status["executions"][0]["latest_review"]["record"]["verdict"] == verdict,
        "合成账本的 status 显示最新 review",
    )
    run.check(
        [entry["state"] for entry in synthetic_status["executions"]] == ["applied"],
        "合成 review 没改变 applied 状态（本例未恢复）",
    )

    # e. 零宽窗口边界：切片 2 的 x0001 应用与恢复在同一秒，窗口 [T, T) 内观察数只能是 0。
    zero_window = {"from": applies["x0001"]["at"], "to": restores["x0001"]["at"]}
    run.check(zero_window["from"] == zero_window["to"], "切片 2 的 x0001 应用与恢复在同一秒，观察窗零宽")
    zero_session = synthetic / "session-zero-width.jsonl"
    write_session(zero_session, session_rows(zero_window["from"], ZERO_WIDTH_SESSION))
    zero = sample_phrase(zero_session, zero_window, zero_window["from"])
    zero_before, zero_inside, zero_after = phrase_lines(ZERO_WIDTH_SESSION, 0)
    run.check(zero["before"] == zero_before, f"零宽窗口前的命中行号（实际 {zero['before']}）")
    run.check(zero["inside"] == [] == zero_inside, f"零宽窗口内观察数为 0（实际 {zero['inside']}）")
    run.check(zero["after"] == zero_after, f"零宽窗口上界及之后的命中行号（实际 {zero['after']}）")
    zero_rows = [json.loads(line) for line in zero_session.read_text(encoding="utf-8").splitlines()]
    endpoint = next(number for number, row in enumerate(zero_rows, start=1) if row["ts"] == zero_window["from"])
    run.check(
        endpoint in zero["after"],
        f"恰好等于窗口端点（= 应用时刻 = 恢复时刻）的第 {endpoint} 行不算窗口内",
    )
    run.check(
        (record["verdict"], record["prediction_met"]) == ("observe", "unknown"),
        "零宽窗口没有窗口内观察，结论只能是 observe / unknown",
    )
    run.check(sha256((source / "ledger.jsonl").read_bytes()) == source_ledger, "切片 2 账本原目录字节不变（只读）")
    return {
        "window": window,
        "cutoff": cutoff,
        "before": len(sampled["before"]),
        "inside": inside,
        "after": len(sampled["after"]),
        "verdict": verdict,
        "prediction_met": prediction_met,
        "zero_window": zero_window,
        "zero_before": len(zero["before"]),
        "zero_after": len(zero["after"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="闭环切片 3 演练（只读切片 2 账本，工件留在 --out）")
    parser.add_argument("--out", type=Path, required=True, help="新建的工件根目录")
    parser.add_argument("--source", type=Path, required=True, help="切片 2 演练的 phe 目录，只读")
    args = parser.parse_args()
    out = Path(os.path.realpath(args.out))
    source = Path(os.path.realpath(args.source))
    if out.exists():
        print(f"拒绝覆盖已存在的工件目录：{out}", file=sys.stderr)
        return 1
    if not (source / "ledger.jsonl").is_file():
        print(f"来源里没有账本：{source / 'ledger.jsonl'}", file=sys.stderr)
        return 1
    out.mkdir(parents=True)
    head = git("rev-parse", "HEAD").stdout.decode().strip() or "unknown"
    porcelain = git("status", "--porcelain").stdout.decode().strip()
    status = "clean" if not porcelain else f"dirty（{len(porcelain.splitlines())} 项）"
    run = Rehearsal(out)
    summary = None
    try:
        summary = run_rehearsal(run, source)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - 中断也要先落盘现场
        run.aborted = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        print(traceback.format_exc(), file=sys.stderr)
    write(
        out / "commands.log",
        "\n\n".join(
            "\n".join(
                [
                    "$ "
                    + " ".join(str(arg).replace(str(REPO), "$REPO").replace(str(out), "$OUT") for arg in call["argv"]),
                    f"exit {call['exit']}",
                    f"stdout:\n{call['stdout']}",
                    f"stderr:\n{call['stderr']}",
                ]
            )
            for call in run.calls
        )
        + "\n",
    )
    write(out / "report.md", render_report(run, source, head, status, sha256(Path(__file__).read_bytes()), summary))
    failed = [check for check in run.checks if not check["ok"]]
    for check in run.checks:
        print(f"{'pass' if check['ok'] else 'fail'}: {check['message']}")
    print(f"report: {out / 'report.md'}")
    if run.aborted is not None or failed:
        if run.aborted is not None:
            print(f"中断：{run.aborted}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
