"""闭环切片 3 演练报告：把已收集的断言、命令与合成样例小节渲染成 Markdown。

只读 `Rehearsal` 收集到的断言与命令，不执行命令、不改工件；报告的写入位置由调用方决定。
"""

import sys
from pathlib import Path

from rehearse_change import REPO, Rehearsal, sha256
from rehearse_review_synthetic import report_lines


def render_report(run: Rehearsal, source: Path, head: str, status: str, script_sha: str, summary: dict | None) -> str:
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
        f"- 演练脚本：`tools/rehearse_review.py`，SHA-256 `{script_sha}`。",
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
            "- Host 加载：**not-tested**。观察窗只是候选取样范围（应用到恢复或本次复盘时刻），写入临时配置不等于 Host 加载过配置。"
        ),
        ("- 真实 review 的 `observations` 为空：本演练没有可指向的观察证据；合成 review 的指针只指向合成文件的行号。"),
        (
            "- 切片 2 的两个执行 `applied_at` 与 `restored_at` 落在同一秒，窗口退化为 `[T, T)`，窗口内观察数只能是 0；"
            "真实复盘要按源记录时间戳核对窗口两端（不早于应用时刻、早于恢复时刻或本次复盘时刻），"
            "`history.py --since` 只到日期粒度，只能粗筛。"
        ),
        (
            "- 【合成】review 事实的 `at` 是账本实际写入时刻（apply 后立即写），不是模拟复盘时刻；样例用声明的 "
            "`cutoff` 当取样上界，因此引用的命中都早于 `cutoff`，但晚于账本 `at`。真实复盘没有这层差异："
            "`at` 就是取样上界。"
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
