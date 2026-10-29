#!/usr/bin/env python3
"""M2 跨 Host 续作演练：在合成仓库里走真实的 `phe.py log` / `status`，不启动任何 Agent。

演练只做三件事：把 `tools/` 和两份协议文档复制成临时仓库；用真实 CLI 走一遍
"起草 → 冻结"以及两个反例；把命令、退出码、输出和断言写进仓库被忽略的
`experiments/m2-host-acceptance/<run-id>/report.md`。

边界：不调用 `run` / `judge`，不写 `approval.json`、`runs.jsonl`、`result.json`，
不发模型请求，不碰用户配置目录。它验证的是 CLI 的阶段推导，不是真实 Host 会话；
真实双 Host 执行消耗订阅额度，需用户另行批准（见 docs/M2_HOST_ACCEPTANCE.md）。
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORT_ROOT = REPO / "experiments/m2-host-acceptance"
# 报告里的命令用占位符，避免写入带本机用户名的绝对路径。
SANDBOX_LABEL = "$SANDBOX/repo"
FORBIDDEN = ("approval.json", "runs.jsonl", "result.json", "report.md", "stop.json", "config.json", "artifacts")
PROPOSAL = {
    "experiment_id": "synthetic-host-handoff",
    "note": "合成提案，只用于演练；不含真实路径、会话或用量数据",
}
# 结尾不带换行：冻结哈希对原始字节敏感，测试里也不补换行。
PROPOSAL_BYTES = json.dumps(PROPOSAL, ensure_ascii=False, indent=2).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(root: Path) -> dict:
    """目录下每个文件的相对路径 → SHA-256；目录记 `<dir>`。"""
    return {
        str(p.relative_to(root)): "<dir>" if p.is_dir() else sha256(p.read_bytes()) for p in sorted(root.rglob("*"))
    }


def git(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *argv], capture_output=True, check=False)


class Rehearsal:
    """收集命令、断言和场景状态，最后一起写报告。"""

    def __init__(self, sandbox: Path, run_dir: Path):
        self.sandbox = sandbox
        self.run_dir = run_dir
        self.calls = []
        self.scenarios = []
        self.problems = []

    def phe(self, *argv) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-I", str(self.sandbox / "tools/phe.py"), *map(str, argv)]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.calls.append(
            {"argv": cmd, "exit": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
        )
        return proc

    def scenario(self, sid: str, title: str, expect: str) -> dict:
        item = {"id": sid, "title": title, "expect": expect, "status": "pass", "checks": [], "notes": []}
        self.scenarios.append(item)
        return item

    def check(self, item: dict, ok: bool, message: str) -> None:
        item["checks"].append({"ok": bool(ok), "message": message})
        if not ok:
            item["status"] = "fail"
            self.problems.append(f"{item['id']}: {message}")

    def status_of(self, experiment: Path, item: dict, label: str) -> dict | None:
        """跑一次 status，核对退出码、JSON 输出和"只读"（实验目录前后快照一致）。"""
        experiments_root = self.sandbox / "experiments"
        before = snapshot(experiments_root)
        proc = self.phe("status", experiment)
        self.check(item, proc.returncode == 0, f"{label}：status 退出码 0（实际 {proc.returncode}）")
        self.check(item, snapshot(experiments_root) == before, f"{label}：status 前后文件快照一致（只读）")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.check(item, False, f"{label}：status 的 stdout 是 JSON")
            return None


def copy_repo(repo: Path) -> None:
    """复制 CLI 需要的模块和协议文档，让 `phe_hostlog.REPO_ROOT` 由 __file__ 落到合成仓库根。"""
    (repo / "tools").mkdir(parents=True)
    for path in sorted((REPO / "tools").glob("*.py")):
        shutil.copyfile(path, repo / "tools" / path.name)
    (repo / "docs").mkdir()
    shutil.copyfile(REPO / "docs/M-1_PROTOCOL.md", repo / "docs/M-1_PROTOCOL.md")
    (repo / "skill/harness-evolve").mkdir(parents=True)
    shutil.copyfile(REPO / "skill/harness-evolve/SKILL.md", repo / "skill/harness-evolve/SKILL.md")
    (repo / "experiments").mkdir()


def unexpected(experiment: Path) -> list:
    """实验目录里不该出现的未授权产物。"""
    return sorted(p.name for p in experiment.iterdir() if p.name in FORBIDDEN)


def last_step(report: dict) -> tuple:
    last = report.get("last_log") or {}
    return last.get("host"), last.get("step")


def scenario_normal_handoff(run: Rehearsal) -> None:
    item = run.scenario(
        "normal-handoff",
        "正常交接（Claude 起草 → 新 Codex 会话冻结）",
        "drafted → frozen；提案字节与哈希不变；status 只读；没有未授权产物",
    )
    exp = run.sandbox / "experiments" / "synthetic-handoff"
    exp.mkdir()
    (exp / "proposal.json").write_bytes(PROPOSAL_BYTES)

    drafted = run.phe("log", exp, "--host", "claude", "--step", "draft", "--note", "起草合成提案")
    run.check(item, drafted.returncode == 0, f"起草声明写入（退出码 {drafted.returncode}）")
    report = run.status_of(exp, item, "起草后")
    if report is not None:
        run.check(item, report["stage"] == "drafted", f"起草后 stage=drafted（实际 {report['stage']}）")
        run.check(
            item, last_step(report) == ("claude", "draft"), f"起草后 last_log=claude/draft（实际 {last_step(report)}）"
        )
        run.check(item, report["inconsistent"] is False, "起草后 inconsistent=false")

    digest = sha256(PROPOSAL_BYTES)
    (exp / "proposal.sha256").write_text(digest + "\n")
    frozen = run.phe("log", exp, "--host", "codex", "--step", "freeze", "--note", "按原始字节冻结")
    run.check(item, frozen.returncode == 0, f"冻结声明写入（退出码 {frozen.returncode}）")
    report = run.status_of(exp, item, "冻结后")
    if report is not None:
        run.check(item, report["stage"] == "frozen", f"冻结后 stage=frozen（实际 {report['stage']}）")
        run.check(
            item, last_step(report) == ("codex", "freeze"), f"冻结后 last_log=codex/freeze（实际 {last_step(report)}）"
        )
        run.check(item, report["inconsistent"] is False, "冻结后 inconsistent=false")
    run.check(item, (exp / "proposal.json").read_bytes() == PROPOSAL_BYTES, "冻结后 proposal.json 字节不变")
    run.check(
        item,
        sha256((exp / "proposal.json").read_bytes()) == (exp / "proposal.sha256").read_text().strip(),
        "proposal.sha256 与 proposal.json 原始字节匹配",
    )
    leftovers = unexpected(exp)
    run.check(item, not leftovers, f"没有未授权产物（实际 {leftovers or '无'}）")
    item["notes"].append(f"proposal.sha256 = {digest}")


def scenario_log_ahead(run: Rehearsal) -> None:
    item = run.scenario(
        "log-ahead",
        "日志声明超前（声称已 run，但没有 runs.jsonl）",
        "status 退出 0；stage 以文件为准为 frozen；inconsistent=true；不补文件",
    )
    exp = run.sandbox / "experiments" / "synthetic-log-ahead"
    exp.mkdir()
    (exp / "proposal.json").write_bytes(PROPOSAL_BYTES)
    (exp / "proposal.sha256").write_text(sha256(PROPOSAL_BYTES) + "\n")
    claimed = run.phe("log", exp, "--host", "codex", "--step", "run", "--note", "声称已开始运行")
    run.check(item, claimed.returncode == 0, f"超前声明写入（退出码 {claimed.returncode}）")
    report = run.status_of(exp, item, "声明 run 后")
    if report is not None:
        run.check(item, report["stage"] == "frozen", f"stage 仍以文件为准=frozen（实际 {report['stage']}）")
        run.check(item, report["inconsistent"] is True, "inconsistent=true")
        run.check(item, last_step(report) == ("codex", "run"), f"last_log=codex/run（实际 {last_step(report)}）")
        run.check(item, report["evidence"].get("runs.jsonl") is False, "runs.jsonl 证据为 false")
    leftovers = unexpected(exp)
    run.check(item, not leftovers, f"没有未授权产物（实际 {leftovers or '无'}）")


def scenario_no_approval(run: Rehearsal) -> None:
    item = run.scenario(
        "no-approval",
        "无用户批准（日志声称 approve，但没有 approval.json）",
        "stage 停在 frozen；inconsistent=true；不写 approval.json，不调用 run",
    )
    exp = run.sandbox / "experiments" / "synthetic-no-approval"
    exp.mkdir()
    (exp / "proposal.json").write_bytes(PROPOSAL_BYTES)
    (exp / "proposal.sha256").write_text(sha256(PROPOSAL_BYTES) + "\n")
    claimed = run.phe("log", exp, "--host", "codex", "--step", "approve", "--note", "等用户批准")
    run.check(item, claimed.returncode == 0, f"approve 声明写入（退出码 {claimed.returncode}）")
    report = run.status_of(exp, item, "声明 approve 后")
    if report is not None:
        run.check(item, report["stage"] == "frozen", f"stage=frozen（实际 {report['stage']}）")
        run.check(item, report["inconsistent"] is True, "inconsistent=true")
        run.check(item, report["evidence"].get("approval.json") is False, "approval.json 证据为 false")
    run.check(item, not (exp / "approval.json").exists(), "approval.json 不存在")
    leftovers = unexpected(exp)
    run.check(item, not leftovers, f"没有未授权产物（实际 {leftovers or '无'}）")
    item["notes"].append("按 SKILL §8 表格，stage=frozen 的下一步是请用户批准；演练不代写批准，也不调用 run")


def scenario_no_unauthorized_artifacts(run: Rehearsal) -> None:
    item = run.scenario(
        "no-unauthorized-artifacts",
        "全程没有未授权产物或越界调用",
        "只有三个合成实验目录；没有 approval/runs/result/artifacts；只调用 log 和 status",
    )
    experiments_root = run.sandbox / "experiments"
    names = sorted(p.name for p in experiments_root.iterdir())
    run.check(
        item,
        names == ["synthetic-handoff", "synthetic-log-ahead", "synthetic-no-approval"],
        f"只有三个合成实验目录（实际 {names}）",
    )
    leftovers = sorted(f"{p.parent.name}/{p.name}" for p in experiments_root.rglob("*") if p.name in FORBIDDEN)
    run.check(item, not leftovers, f"没有 approval.json/runs.jsonl/result.json/artifacts（实际 {leftovers or '无'}）")
    subcommands = sorted({call["argv"][3] for call in run.calls})
    run.check(item, set(subcommands) <= {"log", "status"}, f"只调用 log 和 status（实际 {subcommands}）")


def render_argv(argv: list, sandbox: Path) -> str:
    """命令里的解释器和合成仓库路径换成占位符，报告不写本机绝对路径。"""
    shown = ["python3", *argv[1:]]
    return " ".join(str(a).replace(str(sandbox / "repo"), SANDBOX_LABEL) for a in shown)


def render_report(run: Rehearsal, run_id: str, sandbox: Path, head: str, status: str, diff_sha: str) -> str:
    passed = [s for s in run.scenarios if s["status"] == "pass"]
    failed = [s for s in run.scenarios if s["status"] == "fail"]
    sandbox_note = (
        f"- 合成仓库：`$TMPDIR/{sandbox.name}/repo`，演练结束后删除；"
        f"`tools/*.py`、`docs/M-1_PROTOCOL.md`、`skill/harness-evolve/SKILL.md` 由本仓库复制，"
        "`phe_hostlog.REPO_ROOT` 由 `__file__` 推导为合成仓库根。"
    )
    verdict = (
        f"合成演练 {len(passed)}/{len(run.scenarios)} 场景通过。真实双 Host 场景 not-tested：需用户单独批准。"
        if not failed
        else f"合成演练有 {len(failed)} 个场景失败，见「断言明细」。真实双 Host 场景 not-tested。"
    )
    lines = [
        f"# M2 跨 Host 续作演练报告：{run_id}",
        "",
        f"**结论：** {verdict}",
        "",
        "## 目标、版本与环境",
        "",
        "- 演练对象：`tools/phe.py log` / `status` 的续作阶段推导（M2 切片 C）。",
        f"- 代码版本：commit `{head}`，工作树 {status}，`git diff` 的 SHA-256 `{diff_sha}`。",
        f"- 演练脚本：`tools/m2/rehearse_host_handoff.py`，SHA-256 `{sha256(Path(__file__).read_bytes())}`。",
        f"- 解释器：Python `{sys.version.split()[0]}`，命令里的 `python3` 按 PATH 解析。",
        sandbox_note,
        "- 真实 Host：未启动，没有模型请求；没有调用 `run` / `judge`；没有写用户配置目录。",
        "",
        "## 场景",
        "",
        "| 场景 | 期望 | 观察 | 状态 |",
        "|---|---|---|---|",
    ]
    for item in run.scenarios:
        failed_checks = [c["message"] for c in item["checks"] if not c["ok"]]
        observed = failed_checks[0] if failed_checks else "全部断言成立"
        lines.append(f"| {item['id']}：{item['title']} | {item['expect']} | {observed} | {item['status']} |")
    lines += ["", "## 断言明细", ""]
    for item in run.scenarios:
        lines.append(f"### {item['id']}：{item['title']}")
        lines.append("")
        lines += [f"- [{'x' if c['ok'] else ' '}] {c['message']}" for c in item["checks"]]
        lines += [f"- 备注：{note}" for note in item["notes"]]
        lines.append("")
    lines += ["## 命令与退出码", ""]
    for call in run.calls:
        lines += ["```bash", f"$ {render_argv(call['argv'], sandbox)}", f"exit {call['exit']}"]
        if call["stdout"]:
            lines += [call["stdout"], "```", ""]
        else:
            lines += ["```", ""]
        if call["stderr"]:
            lines += ["stderr：", "", "```", call["stderr"], "```", ""]
    lines += [
        "## 未测边界",
        "",
        "- 真实双 Host 执行（Claude 起草 → 新 Codex 会话冻结）：**not-tested**，需用户单独批准，消耗订阅额度。",
        "- 两个 Host 是否真的在批准边界停下：**not-tested**。演练只验证 CLI 的阶段推导；提示词不是强制权限。",
        "- 无批准时 `run` 是否被拒绝：**not-tested**。演练不调用 `run` / `judge`，也不写 `approval.json`。",
        "- 会话隔离与配置加载：**not-tested**。真实会话会加载工作目录和用户级配置，隔离办法尚未验证。",
        "- Skill 发现：**not-tested**。演练没有启动 Host 会话，`skill/harness-evolve` 是否被加载未验证。",
        "- 报告与输入都是合成数据；真实实验的提案、日志和会话内容不写进本文件。",
        "",
        "## 复现",
        "",
        "```bash",
        "python3 -I tools/m2/rehearse_host_handoff.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    head = git("rev-parse", "HEAD").stdout.decode().strip() or "unknown"
    porcelain = git("status", "--porcelain").stdout.decode().strip()
    diff_sha = sha256(git("diff").stdout)
    porcelain_sha = sha256(porcelain.encode())
    status = (
        "clean"
        if not porcelain
        else f"dirty（{len(porcelain.splitlines())} 项，`git status --porcelain` 的 SHA-256 `{porcelain_sha}`）"
    )
    sandbox = Path(tempfile.mkdtemp(prefix="phe-m2-handoff-"))
    run = Rehearsal(sandbox / "repo", REPORT_ROOT / run_id)
    try:
        copy_repo(run.sandbox)
        scenario_normal_handoff(run)
        scenario_log_ahead(run)
        scenario_no_approval(run)
        scenario_no_unauthorized_artifacts(run)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    run.run_dir.mkdir(parents=True, exist_ok=True)
    (run.run_dir / "report.md").write_text(render_report(run, run_id, sandbox, head, status, diff_sha))
    for item in run.scenarios:
        print(f"{item['status']}: {item['id']} - {item['title']}")
    print(f"report: {run.run_dir / 'report.md'}")
    if run.problems:
        print("失败：", *run.problems, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
