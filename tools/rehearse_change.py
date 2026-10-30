#!/usr/bin/env python3
"""闭环切片 2 演练：真实报告 P1/P2 在真实配置的只读副本上走 apply / status / restore。

只读真实 `--report`（切片 1 报告）与 `--config`（用户级 `config/AGENTS.md`），把字节复制到 `--out` 下；
在临时 home 的三个 Agent 入口软链上依次应用 P1、P2，再逆序恢复。全部工件留在 `--out`：发布、journal、
账本、输入变更包、命令日志和断言报告都不清理，恢复所需文件保持原样。P1/P2 的 patch 在
`tools/rehearse_change_input.py` 里，是手工从报告提取的，本脚本不解析 Markdown。

批准记录写明 `source=sandbox_rehearsal`：这是演练批准，不是用户批准。不写真实 `~/.pi`、`~/.codex`、
`~/.claude` 或 temper。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rehearse_change_input import ITEMS, MARKERS

REPO = Path(__file__).resolve().parents[1]
ENTRIES = {
    "claude": ".claude/CLAUDE.md",
    "codex": ".codex/AGENTS.md",
    "pi": ".pi/agent/AGENTS.md",
}


class Failed(Exception):
    """关键步骤失败：停止后续步骤，仍写出已收集的命令与断言。"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Rehearsal:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.calls: list[dict] = []
        self.checks: list[dict] = []
        self.aborted: str | None = None

    def cli(self, tool: str, *argv: str | Path) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-I", str(REPO / "tools" / tool), *map(str, argv)]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.calls.append(
            {"argv": cmd, "exit": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
        )
        return proc

    def check(self, ok: bool, message: str) -> None:
        self.checks.append({"ok": bool(ok), "message": message})
        if not ok:
            print(f"FAIL: {message}", file=sys.stderr)

    def cli_json(self, label: str, tool: str, *argv: str | Path) -> dict:
        proc = self.cli(tool, *argv)
        self.check(proc.returncode == 0, f"{label}：退出码 0（实际 {proc.returncode}）")
        if proc.returncode != 0:
            raise Failed(f"{label}: {proc.stderr.strip()}")
        return json.loads(proc.stdout)


def build_sandbox(run: Rehearsal, config: Path) -> dict:
    """临时 home 的三个入口软链指向真实配置的只读副本；真实路径只被读。"""
    out = run.out
    shared = out / "temper/config/AGENTS.md"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(config.read_bytes())
    for relative in ENTRIES.values():
        path = out / "home" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(shared, path)
    (out / "phe").mkdir()
    (out / "inputs").mkdir()
    return {"shared": shared, "home": out / "home", "phe": out / "phe"}


def prepare_inputs(run: Rehearsal, paths: dict, report: Path) -> dict:
    """显式准备机读变更包与两份批准记录：绑定变更包原始字节的 sha256 和获批条目。"""
    inventory = run.out / "inventory-1.json"
    registry = run.out / "registry.json"
    for label, tool, argv in (
        ("盘点", "inventory.py", ("--home", paths["home"], "--out", inventory)),
        (
            "登记",
            "registry.py",
            ("adopt", "--inventory", inventory, "--registry", registry, "--group", paths["shared"]),
        ),
    ):
        proc = run.cli(tool, *argv)
        run.check(proc.returncode == 0, f"{label}：退出码 0（实际 {proc.returncode}）")
        if proc.returncode != 0:
            raise Failed(f"{label}: {proc.stderr.strip()}")
    asset_id = json.loads(registry.read_text(encoding="utf-8"))["assets"][0]["asset_id"]
    package = {
        "schema_version": 1,
        "report": {"path": str(report), "sha256": sha256(report.read_bytes())},
        "items": [dict(item, target={"asset_id": asset_id}) for item in ITEMS],
    }
    change = run.out / "inputs/change-2026-10-30.json"
    write(change, json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    approvals = {}
    for item_id in ("P1", "P2"):
        approval = {
            "schema_version": 1,
            "approval_id": f"sandbox-rehearsal-{item_id}",
            "source": "sandbox_rehearsal",
            "change_sha256": sha256(change.read_bytes()),
            "items": [item_id],
            "note": "演练批准：不是用户批准，只在本演练的临时配置上生效",
        }
        path = run.out / f"inputs/approval-{item_id}.json"
        write(path, json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
        approvals[item_id] = path
    return {"asset_id": asset_id, "change": change, "approvals": approvals, "registry": registry}


def entry_texts(paths: dict) -> dict:
    return {agent: (paths["home"] / relative).read_bytes().decode() for agent, relative in ENTRIES.items()}


def ledger(phe: Path) -> list[dict]:
    return [json.loads(line) for line in (phe / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def run_rehearsal(run: Rehearsal, config: Path, report: Path) -> None:
    config_before = sha256(config.read_bytes())
    report_before = sha256(report.read_bytes())
    paths = build_sandbox(run, config)
    inputs = prepare_inputs(run, paths, report)
    common = ("--registry", str(inputs["registry"]), "--phe-home", str(paths["phe"]), "--home", str(paths["home"]))

    # 1. 只批准 P1。
    first = run.cli_json(
        "apply P1",
        "change.py",
        "apply",
        "--change",
        inputs["change"],
        "--item",
        "P1",
        "--approval",
        inputs["approvals"]["P1"],
        *common,
    )
    run.check(first["execution_id"] == "x0001", f"P1 执行 ID=x0001（实际 {first['execution_id']}）")
    run.check(first["release"] == "r0001", f"P1 发布号=r0001（实际 {first['release']}）")
    run.check(len(first["journals"]) == 3, f"P1 三个 Agent 入口各一份 journal（实际 {len(first['journals'])}）")
    for agent, text in entry_texts(paths).items():
        run.check(MARKERS["P1"] in text, f"{agent} 入口读到 P1 的新句子")
        run.check(MARKERS["P2"] not in text, f"{agent} 入口读不到未获批的 P2")

    facts = ledger(paths["phe"])
    run.check([fact["kind"] for fact in facts] == ["approval", "apply"], "账本先记批准再记应用")
    approval_fact, apply_fact = facts
    run.check(approval_fact["source"] == "sandbox_rehearsal", "批准来源是 sandbox_rehearsal，不冒充用户批准")
    run.check(approval_fact["approved_items"] == ["P1"], f"批准只覆盖 P1（实际 {approval_fact['approved_items']}）")
    run.check(
        approval_fact["change_sha256"] == sha256(inputs["change"].read_bytes()),
        "批准绑定的 sha256 等于变更包原始字节",
    )
    run.check(apply_fact["execution_id"] != apply_fact["proposal_id"], "执行 ID 与提案 ID 不同")
    run.check(apply_fact["before_sha256"] == config_before, "before_sha256 等于演练前的配置字节")
    effective = sha256((paths["home"] / ENTRIES["pi"]).read_bytes())
    run.check(apply_fact["after_sha256"] == effective, "after_sha256 与三个入口实际生效的内容一致")
    run.check(apply_fact["report"]["sha256"] == report_before, "账本记录的报告 sha256 与报告文件一致")
    for journal in apply_fact["journals"]:
        run.check((paths["phe"] / journal).is_file(), f"journal 存在：{journal}")

    status = run.cli_json("status（P1 后）", "change.py", "status", "--phe-home", paths["phe"])
    run.check([item["state"] for item in status["executions"]] == ["applied"], "status：x0001=applied")
    run.check(status["targets"][0]["release"] == "r0001", "status：当前生效发布=r0001")
    run.check(status["targets"][0]["sha256"] == effective, "status 的 sha256 与入口实际生效内容一致")

    # 2. 再批准并应用 P2。
    second = run.cli_json(
        "apply P2",
        "change.py",
        "apply",
        "--change",
        inputs["change"],
        "--item",
        "P2",
        "--approval",
        inputs["approvals"]["P2"],
        *common,
    )
    run.check((second["execution_id"], second["release"]) == ("x0002", "r0002"), "P2 执行 x0002 / 发布 r0002")
    for agent, text in entry_texts(paths).items():
        run.check(MARKERS["P1"] in text and MARKERS["P2"] in text, f"{agent} 入口同时读到 P1 与 P2")

    # 3. 逆序恢复 P2：P1 保留，链接回到 P1 的发布并重新绑定。
    restored = run.cli_json(
        "restore x0002",
        "change.py",
        "restore",
        "--execution",
        "x0002",
        "--phe-home",
        paths["phe"],
        "--home",
        paths["home"],
    )
    run.check(restored["release"] == "r0001", f"恢复 P2 后当前发布=r0001（实际 {restored['release']}）")
    run.check(restored["restored_sha256"] == apply_fact["after_sha256"], "恢复后的内容 sha256 等于 P1 应用后的值")
    for agent, text in entry_texts(paths).items():
        run.check(MARKERS["P1"] in text, f"恢复 P2 后 {agent} 入口仍保留 P1")
        run.check(MARKERS["P2"] not in text, f"恢复 P2 后 {agent} 入口没有 P2")
    bindings = json.loads((paths["phe"] / "bindings.json").read_text(encoding="utf-8"))
    run.check(
        {agent: sorted(releases.values()) for agent, releases in bindings.items()}
        == {"claude": ["r0001"], "codex": ["r0001"], "pi": ["r0001"]},
        f"恢复后重新绑定到 r0001（实际 {bindings}）",
    )
    status = run.cli_json("status（恢复 P2 后）", "change.py", "status", "--phe-home", paths["phe"])
    run.check([item["state"] for item in status["executions"]] == ["applied", "restored"], "status：x0002=restored")
    run.check([item["execution_id"] for item in status["active"]] == ["x0001"], "status：只有 x0001 生效")

    # 4. 恢复 P1：三个入口回到原文件，字节与演练前的配置一致。
    last = run.cli_json(
        "restore x0001",
        "change.py",
        "restore",
        "--execution",
        "x0001",
        "--phe-home",
        paths["phe"],
        "--home",
        paths["home"],
    )
    run.check(last["release"] is None, "恢复 P1 后链接回到原文件（release=None）")
    run.check(last["restored_sha256"] == config_before, "恢复后的 sha256 等于演练前的配置字节")
    for agent, relative in ENTRIES.items():
        path = paths["home"] / relative
        run.check(os.readlink(path) == str(paths["shared"]), f"{agent} 入口链接回到配置副本")
        run.check(path.read_bytes() == config.read_bytes(), f"{agent} 入口字节与真实配置一致")
    run.check((paths["phe"] / "bindings.json").read_text(encoding="utf-8").strip() == "{}", "恢复后 bindings 为空")
    status = run.cli_json("status（全部恢复后）", "change.py", "status", "--phe-home", paths["phe"])
    run.check(status["active"] == [], "status：没有生效的执行")
    run.check(status["targets"][0]["release"] is None, "status：当前没有 PHE 发布生效")
    run.check(status["targets"][0]["sha256"] == config_before, "status 的 sha256 等于原配置")

    facts = ledger(paths["phe"])
    run.check(
        [fact["kind"] for fact in facts] == ["approval", "apply", "approval", "apply", "restore", "restore"],
        f"账本事实顺序：批准/应用/批准/应用/恢复/恢复（实际 {[fact['kind'] for fact in facts]}）",
    )
    run.check(
        [fact["source"] for fact in facts if fact["kind"] == "approval"] == ["sandbox_rehearsal"] * 2,
        "两次批准都记 sandbox_rehearsal",
    )
    # 恢复只记事实：不改写历史行，也不写 retired / invalid 之类的效果结论。
    run.check(all("retired" not in fact for fact in facts), "账本没有把恢复写成 retired 或无效")

    # 5. 只读来源：真实配置与报告在演练前后一致。
    run.check(sha256(config.read_bytes()) == config_before, "真实 config/AGENTS.md 前后字节一致（只读）")
    run.check(sha256(report.read_bytes()) == report_before, "报告前后字节一致（只读）")


def render_report(run: Rehearsal, config: Path, report: Path, head: str, status: str) -> str:
    failed = [check for check in run.checks if not check["ok"]]
    if run.aborted is not None:
        verdict = f"演练中断（{run.aborted}）：只保存已执行的命令与部分断言，其余步骤未执行。"
    elif failed:
        verdict = f"演练有 {len(failed)} 条断言失败，见「断言明细」。"
    else:
        verdict = f"演练 {len(run.checks)} 条断言全部成立；结论只覆盖临时配置上的流程，不代表规则改动有效。"

    def placeholder(argv: list) -> str:
        shown = ["python3", *argv[1:]]
        return " ".join(str(arg).replace(str(REPO), "$REPO").replace(str(run.out), "$OUT") for arg in shown)

    lines = [
        "# 闭环切片 2 演练：报告 P1/P2 的批准、应用与逆序恢复",
        "",
        f"**结论：** {verdict}",
        "",
        "## 环境与输入",
        "",
        f"- 代码版本：commit `{head}`，工作树 {status}。",
        f"- 演练脚本：`tools/rehearse_change.py`，SHA-256 `{sha256(Path(__file__).read_bytes())}`。",
        f"- 解释器：Python `{sys.version.split()[0]}`。",
        f"- 真实报告（只读）：`{report}`，SHA-256 `{sha256(report.read_bytes())}`。",
        f"- 真实配置（只读复制）：`{config}`，SHA-256 `{sha256(config.read_bytes())}`。",
        (
            "- 工件根：`$OUT`（本目录）。临时 home 是 `$OUT/home`，三个 Agent 入口是软链；"
            "`$OUT/temper/config/AGENTS.md` 是真实配置的副本。"
        ),
        (
            "- PHE_HOME：`$OUT/phe`，账本是 `$OUT/phe/ledger.jsonl`；发布、journal、staging 都在其下，"
            "恢复所需文件全部保留。"
        ),
        (
            "- 输入工件：`$OUT/inputs/change-2026-10-30.json`（报告引用与 sha256、条目、证据、预测/验证方法、"
            "目标资产、patch）与 `inputs/approval-P1.json`、`inputs/approval-P2.json`。"
        ),
        "- P1/P2 的 diff 从报告手工提取成变更包（`tools/rehearse_change_input.py`），脚本不解析 Markdown。",
        (
            "- 批准记录写 `source=sandbox_rehearsal`：**这是演练批准，不是用户批准**；"
            "没有写真实 `~/.pi`、`~/.codex`、`~/.claude` 或 temper。"
        ),
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
        "## 未测边界",
        "",
        (
            "- 真实配置写入：**not-tested**。演练只在临时 home 的软链上部署；真实 `~/.pi`、`~/.codex`、"
            "`~/.claude` 与 temper 只读。"
        ),
        ("- 效果：**not-tested**。账本是事实记录，不是效果结论；P1/P2 的预测基线都是“待观察”，本次没有对照实验。"),
        "- 三入口是否被对应 Host 实际加载：**not-tested**。演练只改软链目标，没有启动任何 Host 会话。",
        "- skill 资产、普通文件入口、多资产发布：**未支持**，本轮只覆盖单个 instructions 资产 + 软链入口。",
        "- 非逆序恢复（保留 B 单独撤销 A）与补丁重排：**未实现**，同一资产只支持按执行逆序恢复。",
        "- 畸形输入、并发 apply、权限与攻击场景：不在本轮范围，未测。",
        "",
        "## 复现",
        "",
        "```bash",
        (
            "python3 -I tools/rehearse_change.py --out <新目录> --report reports/2026-10-30.md "
            "--config ~/AI/temper/config/AGENTS.md"
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


def git(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *argv], capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="闭环切片 2 演练（只读真实报告与配置，工件留在 --out）")
    parser.add_argument("--out", type=Path, required=True, help="新建的工件根目录")
    parser.add_argument("--report", type=Path, required=True, help="切片 1 报告，只读")
    parser.add_argument("--config", type=Path, required=True, help="用户级 config/AGENTS.md，只读复制")
    args = parser.parse_args()
    out = Path(os.path.realpath(args.out))
    report = Path(os.path.realpath(args.report))
    config = Path(os.path.realpath(args.config))
    if out.exists():
        print(f"拒绝覆盖已存在的工件目录：{out}", file=sys.stderr)
        return 1
    for label, path in (("报告", report), ("配置", config)):
        if not path.is_file():
            print(f"{label}不是文件：{path}", file=sys.stderr)
            return 1
    out.mkdir(parents=True)
    head = git("rev-parse", "HEAD").stdout.decode().strip() or "unknown"
    porcelain = git("status", "--porcelain").stdout.decode().strip()
    status = "clean" if not porcelain else f"dirty（{len(porcelain.splitlines())} 项）"
    run = Rehearsal(out)
    try:
        run_rehearsal(run, config, report)
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
    write(out / "report.md", render_report(run, config, report, head, status))
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
