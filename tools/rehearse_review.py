#!/usr/bin/env python3
"""闭环切片 3 演练：在切片 2 的账本副本上读观察窗、写 review，并跑一份【合成】观察样例。

只读 `--source`（切片 2 演练的 `<phe>` 目录），把它复制到 `--out/phe`，原目录不改。复制后先
`review-plan` 列出两个已恢复执行的观察窗、原预测和证据，再对真实演练执行 `x0001` 写一条
`verdict=observe` / `prediction_met=unknown` 的 review：它只在临时沙箱 home 应用过，真实 Host 从未加载，
也没有基线。然后在一份明确标注【合成】的会话 JSONL 上按 P1 的 `prediction.verify` 手工计数，把合成
review 写进单独的账本副本 `--out/synthetic/phe`，不混入真实演练账本。全部工件留在 `--out`，不清理。
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

# 【合成】会话：不是真实会话，只为演示 P1 的 verify 计数方法。时间与应用时刻同日、同格式，可直接比字符串。
SYNTHETIC_SESSION = [
    {"ts": "2026-10-30T09:10:00Z", "session_id": "synthetic-001", "text": "派发子代理：交接写明工作树路径"},
    {"ts": "2026-10-30T09:40:00Z", "session_id": "synthetic-001", "text": "worker 完成后后续派发报 cwd does not exist"},
    {"ts": "2026-10-30T10:30:00Z", "session_id": "synthetic-001", "text": "应用前另一次派发：cwd does not exist"},
    {"ts": "2026-10-30T11:20:00Z", "session_id": "synthetic-001", "text": "worker 完成后后续派发：cwd does not exist"},
    {"ts": "2026-10-30T11:50:00Z", "session_id": "synthetic-001", "text": "审查交接写明有效 cwd 与提交范围"},
    {"ts": "2026-10-30T12:30:00Z", "session_id": "synthetic-001", "text": "应用后第二次派发：cwd does not exist"},
]
PHRASE = "cwd does not exist"


def copy_source(run: Rehearsal, source: Path) -> str:
    """复制切片 2 的 phe 目录；账本字节逐字节相同，原目录只读。"""
    before = sha256((source / "ledger.jsonl").read_bytes())
    shutil.copytree(source, run.out / "phe", symlinks=True)
    run.check(sha256((run.out / "phe/ledger.jsonl").read_bytes()) == before, "复制的账本字节与切片 2 账本一致")
    return before


def count_phrase(session: Path, applied_at: str) -> tuple[int, int, list[int]]:
    """P1 的 verify 是数 `cwd does not exist` 次数；这里按时间戳分应用前/后各数一次，不判断因果。"""
    before = after = 0
    after_lines = []
    for number, line in enumerate(session.read_text(encoding="utf-8").splitlines(), start=1):
        row = json.loads(line)
        if PHRASE not in row["text"]:
            continue
        if row["ts"] > applied_at:
            after += 1
            after_lines.append(number)
        else:
            before += 1
    return before, after, after_lines


def write_record(run: Rehearsal, name: str, record: dict) -> Path:
    path = run.out / f"inputs/{name}"
    write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def run_rehearsal(run: Rehearsal, source: Path) -> None:
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
    path = write_record(run, "review-x0001-observe.json", record)
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

    # c. 【合成】观察样例：按 P1 的 verify 手算计数，写进单独的账本副本。
    synthetic = out / "synthetic"
    session = synthetic / "session.jsonl"
    write(session, "\n".join(json.dumps(line, ensure_ascii=False) for line in SYNTHETIC_SESSION) + "\n")
    before_count, after_count, after_lines = count_phrase(session, applies["x0001"]["at"])
    run.check((before_count, after_count) == (2, 2), f"合成会话按应用前/后计数（实际 {before_count}/{after_count}）")
    run.check(after_lines == [4, 6], f"应用后命中的行号（实际 {after_lines}）")
    verdict = "keep" if after_count == 0 else "adjust"
    synthetic_record = {
        "verdict": verdict,
        "prediction_met": "not_met" if after_count else "met",
        "reason": (
            f"【合成】按 P1 的 verify 手工计数：应用前 {before_count} 次、应用后 {after_count} 次 cwd does not exist"
        ),
        "observations": [f"{session}:L{number}" for number in after_lines],
        "next": "【合成】只证明复盘流程可运行，不证明规则有效；真实判断要用户批准真实应用后在观察窗内统计",
    }
    run.check(verdict == "adjust", "合成计数非 0，verdict 取 adjust")
    synthetic_path = write_record(run, "review-synthetic-adjust.json", synthetic_record)
    shutil.copytree(phe, synthetic / "phe", symlinks=True)
    synthetic_phe = synthetic / "phe"
    synthetic_row = run.cli_json(
        "review x0001（合成账本）",
        "change.py",
        "review",
        "--execution",
        "x0001",
        "--record",
        synthetic_path,
        "--phe-home",
        synthetic_phe,
    )
    run.check(synthetic_row["record"]["verdict"] == "adjust", "合成 review 写进合成账本副本")
    run.check(len(ledger(synthetic_phe)) == len(after_facts) + 1, "合成账本比真实演练账本多一条 review")
    run.check(ledger(phe) == after_facts, "合成 review 不混入真实演练账本")

    # d. 合成账本的 status 显示 latest_review，restored 状态仍未变。
    synthetic_status = run.cli_json("status（合成账本）", "change.py", "status", "--phe-home", synthetic_phe)
    run.check(
        synthetic_status["executions"][0]["latest_review"]["record"]["verdict"] == "adjust",
        "合成账本的 status 显示最新 review",
    )
    run.check(
        [entry["state"] for entry in synthetic_status["executions"]] == ["restored", "restored"],
        "合成 review 也没改变 restored 状态",
    )
    run.check(sha256((source / "ledger.jsonl").read_bytes()) == source_ledger, "切片 2 账本原目录字节不变（只读）")


def render_report(run: Rehearsal, source: Path, head: str, status: str) -> str:
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
        "# 闭环切片 3 演练：读账本观察窗、写 review 与合成观察样例",
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
            "`$OUT/synthetic/phe` 是写合成 review 的独立账本副本。"
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
        "## 合成观察样例（不证明规则有效）",
        "",
        (
            f"- 合成会话：`$OUT/synthetic/session.jsonl`，{len(SYNTHETIC_SESSION)} 行手写 JSONL，"
            "明确标注 `synthetic-001`，不含真实会话内容。"
        ),
        (
            "- 计数方法取自 P1 的 `prediction.verify`（数 `cwd does not exist` 次数），脚本按时间戳把命中行分到"
            "应用前/应用后各数一次，不做统计推断。"
        ),
        "- 合成 review 写进 `$OUT/synthetic/phe`，不混入 `$OUT/phe`；合成数据的结论只说明计数与记账能跑通。",
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
            "真实复盘要在应用后、恢复前取样。"
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
    try:
        run_rehearsal(run, source)
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
    write(out / "report.md", render_report(run, source, head, status))
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
