"""闭环切片 3 演练的【合成】观察窗样例：独立沙箱、合成会话与按 `[from, upper)` 取样。

合成沙箱不来自真实报告、真实配置或真实会话：小 base `alpha/beta/gamma`、三个软链入口和全新 PHE_HOME，
apply 一条合成条目 `S1` 后不 restore，因此 `review-plan` 的 `to` 是 `null`。未恢复时取样上界取本次复盘
时刻，不取复盘之后的记录：合成样例显式声明【模拟复盘时刻】`cutoff = applied_at + 60 分钟`，窗口是
`[应用时刻, 模拟复盘时刻)`，并放一条晚于 `cutoff` 的命中证明上界生效。会话时间戳按相对实际 `applied_at`
的分钟偏移生成，取样按窗口两端（`from` 与上界）判，不只看应用时刻。零宽窗口样例用来证明窗口外的命中
（含恰好等于端点的记录）不计入观察。所有内容都标注【合成】，只证明复盘流程可运行，不证明规则有效。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from rehearse_change import ENTRIES, Failed, Rehearsal, sha256, write

PHRASE = "cwd does not exist"
# 【模拟复盘时刻】相对实际应用时刻的偏移；未恢复的观察窗上界取它，不取“至今”。
CUTOFF_MINUTES = 60

SYNTHETIC_BASE = "alpha\nbeta\ngamma\n"
SYNTHETIC_PATCH = (
    "--- a/config/AGENTS.md\n+++ b/config/AGENTS.md\n@@ -1,3 +1,3 @@\n alpha\n-beta\n+beta synthetic\n gamma\n"
)
SYNTHETIC_ITEM = "S1"
SYNTHETIC_PREDICTION = {
    "expected": "【合成】应用后不再出现 cwd does not exist",
    "verify": "【合成】按源记录时间戳统计观察窗（应用时刻到恢复时刻或本次复盘时刻）内 cwd does not exist 的次数",
    "baseline": "【合成】应用前的次数见合成会话，不来自真实会话",
}
# 每条是相对实际应用时刻的分钟偏移；窗口是 [0, CUTOFF_MINUTES)，偏移不小于 CUTOFF_MINUTES 的记录晚于模拟复盘时刻。
SYNTHETIC_SESSION = [
    (-90, "派发子代理：交接写明工作树路径"),
    (-60, "worker 完成后后续派发报 cwd does not exist"),
    (-30, "应用前另一次派发：cwd does not exist"),
    (2, "应用后第一次派发：cwd does not exist"),
    (15, "审查交接写明有效 cwd 与提交范围"),
    (40, "应用后第二次派发：cwd does not exist"),
    (90, "模拟复盘时刻之后的派发：cwd does not exist，晚于复盘时刻，不计入"),
]
# 零宽窗口边界样例：窗口是 [T, T)，偏移 0 的记录恰好等于两端端点，也不算窗口内。
ZERO_WIDTH_SESSION = [
    (-60, "窗口前一小时：派发报 cwd does not exist"),
    (-10, "窗口前十分钟：审查交接后再次派发报 cwd does not exist"),
    (0, "恢复时刻（= 应用时刻）的派发报 cwd does not exist：恰好等于端点，不算窗口内"),
    (10, "恢复后十分钟：派发仍报 cwd does not exist"),
    (60, "恢复后一小时：另一次派发报 cwd does not exist"),
]


def cutoff_at(applied_at: str) -> str:
    """【模拟复盘时刻】= 应用时刻 + CUTOFF_MINUTES；未恢复的窗口以它为上界。"""
    return (datetime.fromisoformat(applied_at) + timedelta(minutes=CUTOFF_MINUTES)).isoformat()


def window_upper(window: dict, cutoff: str) -> str:
    """取样上界：已恢复取恢复时刻，未恢复取本次复盘时刻（不取复盘之后的记录）。"""
    return window["to"] if window["to"] is not None else cutoff


def phrase_lines(items: list[tuple[int, str]], cutoff: int) -> tuple[list[int], list[int], list[int]]:
    """按偏移把命中行号分成窗口前、窗口内、窗口后三组；窗口是 `[0, cutoff)`，等于上界算窗口后。"""
    before, inside, after = [], [], []
    for number, (offset, text) in enumerate(items, start=1):
        if PHRASE not in text:
            continue
        if offset < 0:
            before.append(number)
        elif offset >= cutoff:
            after.append(number)
        else:
            inside.append(number)
    return before, inside, after


def sample_phrase(session: Path, window: dict, cutoff: str) -> dict:
    """按 `[from, 上界)` 用源记录时间戳取样：窗口前、窗口内、窗口后的命中行号；只有窗口内的算观察。"""
    start = datetime.fromisoformat(window["from"])
    upper = datetime.fromisoformat(window_upper(window, cutoff))
    sampled = {"before": [], "inside": [], "after": []}
    for number, line in enumerate(session.read_text(encoding="utf-8").splitlines(), start=1):
        row = json.loads(line)
        if PHRASE not in row["text"]:
            continue
        moment = datetime.fromisoformat(row["ts"])
        if moment < start:
            sampled["before"].append(number)
        elif moment >= upper:
            sampled["after"].append(number)
        else:
            sampled["inside"].append(number)
    return sampled


def session_rows(anchor: str, items: list[tuple[int, str]]) -> list[dict]:
    """按相对锚点的分钟偏移生成合成会话行；时间戳格式与账本一致，可直接解析比较。"""
    base = datetime.fromisoformat(anchor)
    return [
        {"ts": (base + timedelta(minutes=offset)).isoformat(), "session_id": "synthetic-001", "text": text}
        for offset, text in items
    ]


def write_session(path: Path, rows: list[dict]) -> None:
    write(path, "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def report_lines(summary: dict | None) -> list[str]:
    """演练报告里的合成样例小节：窗口、计数与推导结论；中断时只写没有数据。"""
    if summary is None:
        return ["- 演练在跑到合成样例前中断，本节没有数据。"]
    offsets = " / ".join(f"{offset:+d}" for offset, _ in SYNTHETIC_SESSION)
    return [
        (
            "- 【合成】沙箱：`$OUT/synthetic/` 下是全新临时 home（三个软链入口）、小 base `config/AGENTS.md` 和"
            "独立 PHE_HOME；资产、变更包 `S1`、报告与批准都由脚本合成，不来自真实报告或真实会话，批准写"
            " `source=sandbox_rehearsal`。"
        ),
        (
            f"- 观察窗：`from = {summary['window']['from']}`、`to = null`（apply 后不 restore）；未恢复时上界取"
            f"【模拟复盘时刻】`cutoff = {summary['cutoff']}`（= 应用时刻 + {CUTOFF_MINUTES} 分钟），窗口是"
            " `[from, cutoff)`。这是演练声明的模拟复盘时刻，不是实际至今，也不取复盘之后的记录。"
        ),
        (
            f"- 合成会话：`$OUT/synthetic/session.jsonl`，{len(SYNTHETIC_SESSION)} 行 JSONL，时间戳按相对实际"
            f" `applied_at` 的分钟偏移生成（{offsets}），标注 `synthetic-001`，不含真实会话内容。"
        ),
        (
            f"- 按 `prediction.verify` 核对窗口两端：窗口前 {summary['before']} 条命中、窗口内 {summary['inside']} 条命中、"
            f"窗口后 {summary['after']} 条命中（晚于 `cutoff` 的记录，不计入）；结论按窗口内计数推导为"
            f" `{summary['verdict']}` / `{summary['prediction_met']}`。"
        ),
        (
            f"- 零宽窗口：切片 2 的 `x0001` 窗口是 `[{summary['zero_window']['from']}, {summary['zero_window']['to']})`；"
            f"`$OUT/synthetic/session-zero-width.jsonl` 把 {summary['zero_before']} 条命中放在窗口前、"
            f"{summary['zero_after']} 条放在上界及之后（含恰好等于端点的一条），窗口内观察数为 0，结论只能是"
            " `observe` / `unknown`。"
        ),
        (
            "- 合成 review 写进 `$OUT/synthetic/phe`（`approval` / `apply` / `review` 三条事实），不混入 `$OUT/phe`；"
            "合成数据的结论只说明取样与记账能跑通。"
        ),
    ]


def build_sandbox(run: Rehearsal, root: Path) -> dict:
    """合成沙箱：小 base + 三个软链入口 + 全新 PHE_HOME；资产、变更包、报告与批准都由脚本合成。"""
    shared = root / "temper/config/AGENTS.md"
    write(shared, SYNTHETIC_BASE)
    home = root / "home"
    for relative in ENTRIES.values():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(shared, path)
    phe = root / "phe"
    phe.mkdir()
    inventory = root / "inventory-synthetic.json"
    registry = root / "registry-synthetic.json"
    for label, tool, argv in (
        ("【合成】盘点", "inventory.py", ("--home", home, "--out", inventory)),
        ("【合成】登记", "registry.py", ("adopt", "--inventory", inventory, "--registry", registry, "--group", shared)),
    ):
        proc = run.cli(tool, *argv)
        run.check(proc.returncode == 0, f"{label}：退出码 0（实际 {proc.returncode}）")
        if proc.returncode != 0:
            raise Failed(f"{label}: {proc.stderr.strip()}")
    report = root / "report-synthetic.md"
    write(report, "# 【合成】切片 3 观察窗样例报告\n\nS1 是合成改动，不来自真实会话，也不来自切片 1 报告。\n")
    session = root / "session.jsonl"
    _, inside_lines, _ = phrase_lines(SYNTHETIC_SESSION, CUTOFF_MINUTES)
    asset_id = json.loads(registry.read_text(encoding="utf-8"))["assets"][0]["asset_id"]
    package = {
        "schema_version": 1,
        "report": {"path": str(report), "sha256": sha256(report.read_bytes())},
        "items": [
            {
                "item_id": SYNTHETIC_ITEM,
                "evidence": [f"{session}:L{number}" for number in inside_lines],
                "prediction": SYNTHETIC_PREDICTION,
                "target": {"asset_id": asset_id},
                "patch": SYNTHETIC_PATCH,
            }
        ],
    }
    change = root / "inputs/change-synthetic.json"
    write(change, json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    approval = {
        "schema_version": 1,
        "approval_id": "synthetic-rehearsal-S1",
        "source": "sandbox_rehearsal",
        "change_sha256": sha256(change.read_bytes()),
        "items": [SYNTHETIC_ITEM],
        "note": "【合成】演练批准：不是用户批准，只在本次合成沙箱生效",
    }
    approval_path = root / "inputs/approval-synthetic-S1.json"
    write(approval_path, json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
    return {
        "home": home,
        "phe": phe,
        "change": change,
        "approval": approval_path,
        "registry": registry,
        "session": session,
        "inside_lines": inside_lines,
    }
