#!/usr/bin/env python3
"""闭环切片 3 演练：在切片 2 的账本副本上读观察窗、写 review，并跑【合成】的观察窗取样样例。

只读 `--source`（切片 2 演练的 `<phe>` 目录），把它复制到 `--out/phe`，原目录不改。复制后先
`review-plan` 列出两个已恢复执行的观察窗、原预测和证据，再对真实演练执行 `x0001` 写一条
`verdict=observe` / `prediction_met=unknown` 的 review：它只在临时沙箱 home 应用过，真实 Host 从未加载，
也没有基线。然后在一套【合成】沙箱（`rehearse_review_synthetic.py`）里 apply 一条合成条目后不 restore，
观察窗是 `[应用时刻, 至今)`；合成会话的时间戳按相对实际 `applied_at` 的偏移生成，按窗口两端取样后手算
P1 口径的 `cwd does not exist` 次数，把合成 review 写进合成账本。零宽窗口样例证明窗口外的命中不计入观察。
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
from rehearse_review_synthetic import (
    SYNTHETIC_ITEM,
    SYNTHETIC_SESSION,
    ZERO_WIDTH_SESSION,
    build_sandbox,
    phrase_lines,
    report_lines,
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

    # c. 【合成】独立沙箱：apply S1 后不 restore，观察窗 = [应用时刻, 至今)。
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
    synthetic_plan = run.cli_json("【合成】review-plan", "change.py", "review-plan", "--phe-home", paths["phe"])
    window = synthetic_plan["executions"][0]["observation_window"]
    run.check(window == {"from": applied_at, "to": None}, f"【合成】未恢复，观察窗从应用时刻到至今（实际 {window}）")
    write_session(paths["session"], session_rows(window["from"], SYNTHETIC_SESSION))
    sampled = sample_phrase(paths["session"], window)
    expected_before, expected_inside = phrase_lines(SYNTHETIC_SESSION)
    run.check(sampled["before"] == expected_before, f"【合成】窗口前的命中行号（实际 {sampled['before']}）")
    run.check(sampled["inside"] == expected_inside, f"【合成】窗口内的命中行号（实际 {sampled['inside']}）")
    run.check(sampled["after"] == [], "【合成】未恢复时窗口没有上界，应用后的命中都算窗口内")
    inside = len(sampled["inside"])
    verdict = "keep" if inside == 0 else "adjust"
    prediction_met = "met" if inside == 0 else "not_met"
    synthetic_record = {
        "verdict": verdict,
        "prediction_met": prediction_met,
        "reason": (
            f"【合成】按 prediction.verify 在观察窗内统计：窗口前 {len(sampled['before'])} 次、"
            f"窗口内 {inside} 次 cwd does not exist；合成数据只证明取样与记账能跑通，不证明规则有效"
        ),
        "observations": [f"{paths['session']}:L{number}" for number in sampled["inside"]],
        "next": "【合成】真实判断要用户批准真实应用后按同一口径统计，并抽查审查交接是否写明有效 cwd 与提交范围",
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
        f"【合成】review 按窗口内计数推导：{len(sampled['before'])} 前 / {inside} 内 → {verdict} / {prediction_met}",
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

    # e. 零宽窗口边界：切片 2 的 x0001 应用与恢复在同一秒，窗口内观察数只能是 0。
    zero_window = {"from": applies["x0001"]["at"], "to": restores["x0001"]["at"]}
    run.check(zero_window["from"] == zero_window["to"], "切片 2 的 x0001 应用与恢复在同一秒，观察窗零宽")
    zero_session = synthetic / "session-zero-width.jsonl"
    write_session(zero_session, session_rows(zero_window["from"], ZERO_WIDTH_SESSION))
    zero = sample_phrase(zero_session, zero_window)
    run.check(
        zero["inside"] == [] and len(zero["before"]) + len(zero["after"]) == len(ZERO_WIDTH_SESSION),
        f"零宽窗口内观察数为 0（窗口外命中 {len(zero['before'])} 前 / {len(zero['after'])} 后）",
    )
    run.check(
        (record["verdict"], record["prediction_met"]) == ("observe", "unknown"),
        "零宽窗口没有窗口内观察，结论只能是 observe / unknown",
    )
    run.check(sha256((source / "ledger.jsonl").read_bytes()) == source_ledger, "切片 2 账本原目录字节不变（只读）")
    return {
        "window": window,
        "before": len(sampled["before"]),
        "inside": inside,
        "verdict": verdict,
        "prediction_met": prediction_met,
        "zero_window": zero_window,
        "zero_before": len(zero["before"]),
        "zero_after": len(zero["after"]),
    }


def render_report(run: Rehearsal, source: Path, head: str, status: str, summary: dict | None) -> str:
    failed = [check for check in run.checks if not check["ok"]]
    if run.aborted is not None:
        verdict = f"演练中断（{run.aborted}）：只保存已执行的命令与部分断言，其余步骤未执行。"
    elif failed:
        verdict = f"演练有 {len(failed)} 条断言失败，见「断言明细」。"
    else:
        verdict = (
            f"演练 {len(run.checks)} 条断言全部成立；它只证明复盘流程（读观察窗、写 review、合成计数）可运行，"
            "不证明规则有效，也不代表真实 Host 加载过配置。"
        )

    def placeholder(argv: list) -> str:
        shown = ["python3", *argv[1:]]
        return " ".join(str(arg).replace(str(REPO), "$REPO").replace(str(run.out), "$OUT") for arg in shown)

    lines = [
        "# 闭环切片 3 演练：读账本观察窗、写 review 与合成观察窗样例",
        "",
        f"**结论：** {verdict}",
        "",
        "## 环境与输入",
        "",
        f"- 代码版本：commit `{head}`，工作树 {status}。",
        f"- 演练脚本：`tools/rehearse_review.py`，SHA-256 `{sha256(Path(__file__).read_bytes())}`。",
        f"- 解释器：Python `{sys.version.split()[0]}`。",
        f"- 切片 2 账本（只读复制）：`{source / 'ledger.jsonl'}`，SHA-256 `{sha256((source / 'ledger.jsonl').read_bytes())}`。",
        (
            "- 工件根：`$OUT`（本目录）。`$OUT/phe` 是切片 2 的 phe 副本（账本、发布、journal 原样复制），"
            "`$OUT/synthetic/` 是另一套【合成】沙箱（自己的 home、小 base 与独立 PHE_HOME）。"
        ),
        (
            "- 复制的账本保留切片 2 的绝对路径事实（`home`、`phe_home`、`target.paths`、`report.path`）；"
            "review 不改写任何历史行，`status` 因此仍读切片 2 演练的临时 home 入口。"
        ),
        (
            "- 真实 review（`x0001`）写 `verdict=observe` / `prediction_met=unknown`："
            "该执行只在临时沙箱 home 应用，真实 Host 从未加载，也没有基线。"
        ),
        "",
        "## 合成观察窗样例（不证明规则有效）",
        "",
    ]
    lines += report_lines(summary)
    lines += [
        "",
        "## 断言明细",
        "",
    ]
    lines += [f"- [{'x' if check['ok'] else ' '}] {check['message']}" for check in run.checks]
    lines += ["", "## 命令与退出码", ""]
    for call in run.calls:
        lines += ["```bash", f"$ {placeholder(call['argv'])}", f"exit {call['exit']}"]
        lines += [call["stdout"], "```", ""] if call["stdout"] else ["```", ""]
        if call["stderr"]:
            lines += ["stderr：", "", "```", call["stderr"], "```", ""]
    lines += [
        "## 证据边界",
        "",
        ("- 规则效果：**not-tested**。review 只记录宿主的判断；`prediction_met` 只说原预测是否达到，不是因果证明。"),
        (
            "- Host 加载：**not-tested**。观察窗只是候选取样范围（应用到恢复或至今），写入临时配置不等于 Host 加载过配置。"
        ),
        ("- 真实 review 的 `observations` 为空：本演练没有可指向的观察证据；合成 review 的指针只指向合成文件的行号。"),
        (
            "- 切片 2 的两个执行 `applied_at` 与 `restored_at` 落在同一秒，观察窗退化为一个时刻；"
            "真实复盘要按源记录时间戳核对窗口两端（应用后、恢复前），`history.py --since` 只到日期粒度，只能粗筛。"
        ),
        "- `verdict=revert` 只是建议：用户批准后才走 `restore`，已恢复的执行不重复恢复；本轮没有触发 revert。",
        "- 畸形 record、并发写入、权限与攻击场景不在本轮范围，未测。",
        "",
        "## 复现",
        "",
        "```bash",
        "python3 -I tools/rehearse_review.py --out <新目录> --source experiments/loop-apply-001/phe",
        "```",
        "",
    ]
    return "\n".join(lines)


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
    write(out / "report.md", render_report(run, source, head, status, summary))
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
