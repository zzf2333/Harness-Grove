#!/usr/bin/env python3
"""冻结 proposal 驱动的评测入口（标准库）。"""

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phe_hostlog import log as host_log
from phe_hostlog import status as host_status
from phe_judge import (  # noqa: F401
    TOK,
    evaluate,
    roles,
    schedule_matches,
    valid,
    validate_schedule,
)

# 按源码字节加载沙箱模块：import 可能执行内容不同但 timestamp/size 匹配的 pyc。
PHE_SANDBOX_PATH = Path(__file__).resolve().parent / "phe_sandbox.py"
PHE_SANDBOX_SRC = PHE_SANDBOX_PATH.read_bytes()
phe_sandbox = types.ModuleType("phe_sandbox")
sys.modules["phe_sandbox"] = phe_sandbox
exec(compile(PHE_SANDBOX_SRC, str(PHE_SANDBOX_PATH), "exec"), phe_sandbox.__dict__)  # noqa: S102
PHE_SANDBOX_SHA256 = hashlib.sha256(PHE_SANDBOX_SRC).hexdigest()
git_collect, git_env = phe_sandbox.git_collect, phe_sandbox.git_env

HOME = Path.home()
USER_FILES = [
    HOME / ".claude/CLAUDE.md",
    HOME / ".claude/RTK.md",
    HOME / ".claude/settings.json",
]


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def user_fingerprint() -> dict:
    return {str(p): sha256_file(p.resolve()) for p in USER_FILES}


def argv_digest(argv) -> str:
    return hashlib.sha256(json.dumps(argv).encode()).hexdigest()


def runner_file_hashes() -> dict[str, str | None]:
    """实际加载的 runner 模块 Hash；新 sandbox 实验冻结它们，校验的是这里加载的副本。

    `phe_sandbox.py` 取自已执行的源码字节，不是可能被绕过执行的 `.py` 文件或 `__pycache__`。
    """
    return {
        "phe.py": sha256_file(Path(__file__).resolve()),
        "phe_sandbox.py": PHE_SANDBOX_SHA256,
    }


def build_workdir(base: Path, harness: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix="phe-run-", dir="/tmp")) / "repo"
    subprocess.run(["cp", "-Rc", str(base), str(work)], check=True)
    shutil.rmtree(work / ".git")
    shutil.copyfile(harness, work / "AGENTS.md")
    git = ["git", "-c", "user.name=phe", "-c", "user.email=phe@local"]
    subprocess.run(["git", "init", "-q"], cwd=work, env=git_env(), check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, env=git_env(), check=True)
    subprocess.run(git + ["commit", "-qm", "task start"], cwd=work, env=git_env(), check=True)
    return work


def oracle(work: Path, cfg: dict, art: Path, receipt_required=True, sandbox=None) -> dict:
    check_root = Path(tempfile.mkdtemp(prefix="phe-oracle-", dir="/tmp"))
    check = check_root / "repo"
    subprocess.run(["cp", "-Rc", str(work), str(check)], check=True)
    # 可信测试覆盖 Agent 改过的同名文件；oracle_tests 支持跨包的多个文件。
    tests = cfg.get("oracle_tests") or [{"src": cfg["oracle_test_src"], "dest": cfg["oracle_test_dest"]}]
    try:
        for t in tests:
            shutil.copyfile(t["src"], phe_sandbox.oracle_dest(check, t["dest"]))
    except ValueError as exc:
        # 准备阶段被拒也要留证据：原因落盘后再交给 run 记 stop，不静默中断。
        shutil.rmtree(check_root, ignore_errors=True)
        (art / "oracle-prep-error.txt").write_text(str(exc))
        raise
    # 验收器可能执行 Target 改过的项目代码/测试，和 Target 用同一写限制；policy 同样放 art。
    tmp = check_root / "tmp"
    tmp.mkdir()
    policy = art / phe_sandbox.ORACLE_POLICY
    policy_sha = phe_sandbox.write_policy(sandbox, policy, [check, tmp])
    wrapped = phe_sandbox.wrap(sandbox, policy) + cfg["oracle_argv"]
    env = os.environ.copy()
    if sandbox is not None:
        env["TMPDIR"] = str(tmp)
    rc, out, err, dur, to = phe_sandbox.run_with_timeout(
        wrapped,
        check / cfg["oracle_cwd"],
        env,
        cfg["oracle_timeout_s"],
        cfg["process_kill_grace_seconds"],
    )
    text = (out + err).decode(errors="replace")
    tests_line = next((line.strip() for line in text.splitlines() if line.strip().startswith("Tests ")), None)
    receipt_path = work / ".phe-calibration-receipt"
    receipt = receipt_path.read_text().strip() if receipt_path.exists() else None
    receipt_match = receipt is not None and hashlib.sha256(receipt.encode()).hexdigest() == cfg.get("nonce_sha256")
    shutil.rmtree(check_root, ignore_errors=True)
    test_pass = None if to else (rc == 0)
    return {
        "test_pass": test_pass,
        "oracle_exit": rc,
        "oracle_timed_out": to,
        "oracle_seconds": round(dur, 2),
        "oracle_tests_line": tests_line,
        "receipt_present": receipt is not None,
        "receipt_match": receipt_match,
        "contract_pass": None if test_pass is None else (test_pass and (receipt_match or not receipt_required)),
        "oracle_wrapped_argv_sha256": argv_digest(wrapped),
        "oracle_policy_sha256": policy_sha,
        "_oracle_output": text,
    }


def usage_from(result: dict) -> dict:
    mu = result.get("modelUsage") or {}
    total = 0
    for v in mu.values():
        total += sum(
            v.get(k, 0) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        )
    return {
        "model_usage": mu,
        TOK: total if mu else None,
        "estimated_cost_usd_v1": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
    }


def run_slot(exp: Path, cfg: dict, item: dict, budget: dict, receipt_required=True, sandbox=None):
    art = exp / "artifacts" / f"slot-{item['slot']:02d}"
    art.mkdir(parents=True, exist_ok=True)
    harness = Path(cfg["harness"][item["group"]])
    work = build_workdir(Path(cfg["base_dir"]), harness)
    # 专用临时目录和 Target 输出子目录由 runner 创建；art 根只放 Target 不可伪造的 runner 证据。
    slot_root = work.parent
    tmp, target_output = slot_root / "tmp", slot_root / "output"
    tmp.mkdir()
    target_output.mkdir()
    # 启动前记下任务起点；Agent 可能在临时仓库里提交甚至改写历史，diff 一律对比这个 SHA。
    root = git_collect(work, ["rev-parse", "HEAD"]).strip()
    argv = cfg.get("target_argv") or [
        "claude",
        "-p",
        cfg["prompt"],
        *cfg["claude_args"],
        "--settings",
        json.dumps(cfg["settings"]),
        "--debug-file",
        str(target_output / "debug.log"),
    ]
    env = os.environ.copy()
    env.update(cfg["env"])
    for k in cfg.get("env_unset", []):
        env.pop(k, None)
    # 本 slot 的 policy 写在 art（Target 不可写）；哨兵和 Target 用的是同一个文件。
    policy = art / phe_sandbox.TARGET_POLICY
    policy_sha = phe_sandbox.write_policy(sandbox, policy, [work, tmp, target_output])
    if sandbox is not None:
        env["TMPDIR"] = str(tmp)
    wrapped = phe_sandbox.wrap(sandbox, policy) + argv

    record = {
        "experiment_id": cfg["experiment_id"],
        "slot": item["slot"],
        "group": item["group"],
        "harness_sha256": sha256_file(harness),
        # 默认命令沿用旧口径（跳过 claude -p <prompt>）；自定义 target_argv 记录完整命令。
        "argv_sha256": hashlib.sha256(json.dumps(argv if cfg.get("target_argv") else argv[3:]).encode()).hexdigest(),
        "wrapped_argv_sha256": argv_digest(wrapped),
        "policy_sha256": policy_sha,
        "workdir": str(work),
        "target_output": str(target_output),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "user_files_before": user_fingerprint(),
    }
    launch = {
        "argv": argv,
        "wrapped_argv": wrapped,
        "policy": str(policy) if policy_sha else None,
        "policy_sha256": policy_sha,
    }
    (art / "argv.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2))
    # 哨兵在 Target 启动前用本 slot 实际 policy 和完整允许清单实测；只写 runner 自有 /tmp 目录。
    if sandbox is not None:
        try:
            phe_sandbox.verify(sandbox, policy, [work, tmp, target_output])
        except phe_sandbox.SandboxError:
            shutil.rmtree(art, ignore_errors=True)
            with contextlib.suppress(OSError):
                art.parent.rmdir()
            shutil.rmtree(slot_root, ignore_errors=True)
            raise
    rc, out, err, dur, to = phe_sandbox.run_with_timeout(
        wrapped, work, env, budget["max_process_seconds_per_invocation"], budget["process_kill_grace_seconds"]
    )
    (art / "stdout.json").write_bytes(out)
    (art / "stderr.txt").write_bytes(err)
    record.update({"exit_code": rc, "wall_time_v1": round(dur, 2), "process_timed_out": to})
    try:
        result = json.loads(out)
        record.update(usage_from(result))
    except json.JSONDecodeError:
        record["result_parse_error"] = True

    # 后续 Git 采集同策略包裹：Target 可写 .git/config，不能被其中配置的 helper 带出限制。
    git_policy = art / phe_sandbox.GIT_POLICY
    record["git_policy_sha256"] = phe_sandbox.write_policy(sandbox, git_policy, [tmp])
    diff_args = ["diff", "--no-ext-diff", "--no-textconv"]
    diff = git_collect(work, [*diff_args, root, "--stat"], sandbox, git_policy, tmp)
    (art / "diff.patch").write_text(git_collect(work, [*diff_args, root], sandbox, git_policy, tmp))
    record["diff_stat"] = diff.strip().splitlines()[-1] if diff.strip() else ""
    status = git_collect(work, ["status", "--short"], sandbox, git_policy, tmp)
    record["untracked_or_changed"] = status.strip().splitlines()

    oracle_cfg = dict(
        cfg,
        oracle_timeout_s=budget["max_oracle_seconds_per_invocation"],
        process_kill_grace_seconds=budget["process_kill_grace_seconds"],
    )
    o = oracle(work, oracle_cfg, art, receipt_required, sandbox=sandbox)
    (art / "oracle.txt").write_text(o.pop("_oracle_output"))
    record.update(o)
    record["user_files_after"] = user_fingerprint()
    record["user_files_unchanged"] = record["user_files_before"] == record["user_files_after"]

    with open(exp / "runs.jsonl", "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(exp: Path):
    raw = (exp / "proposal.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != (exp / "proposal.sha256").read_text().strip():
        raise ValueError("proposal.sha256 不匹配，停止")
    if json.loads((exp / "approval.json").read_text())["proposal_sha256"] != digest:
        raise ValueError("approval 未绑定当前 proposal，停止")
    return json.loads(raw), json.loads((exp / "config.json").read_text())


def records(exp: Path) -> list:
    path = exp / "runs.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def stop_reason(exp: Path):
    path = exp / "stop.json"
    return json.loads(path.read_text())["stop_reason"] if path.exists() else None


def integrity_problem(p, cfg, exp) -> str | None:
    if sha256_file(exp / "config.json") != p["runner"]["config_sha256"]:
        return "config_hash_changed"
    for g in p["experiment"]["groups"]:
        if sha256_file(Path(cfg["harness"][g])) != p["harness"]["variants"][g]["snapshot_sha256"]:
            return f"harness_hash_changed:{g}"
    # 文件缺失返回 None，同样视为被改写。
    for path, digest in p["task"].get("oracle_files_sha256", {}).items():
        if sha256_file(Path(path)) != digest:
            return "oracle_hash_changed"
    # 新 sandbox 实验必须绑定实际加载的 runner 模块；无 sandbox 的旧实验不要求。
    if cfg.get("sandbox") is not None:
        frozen = p.get("runner", {}).get("runner_files_sha256")
        if not isinstance(frozen, dict) or set(frozen) != {"phe.py", "phe_sandbox.py"}:
            return "sandbox_runner_hash_missing"
        if frozen != runner_file_hashes():
            return "sandbox_runner_hash_changed"
    return None


def crash_stop(p, cfg, exp, proposal_digest):
    # Runner 异常退出前也复核完整性：invalid_run 优先于 pivot。
    if sha256_file(exp / "proposal.json") != proposal_digest:
        return "invalid_run:proposal_hash_changed"
    problem = integrity_problem(p, cfg, exp)
    return "invalid_run:" + problem if problem else "pivot:runner_crashed_no_record"


def most_severe(reasons):
    reasons = [r for r in reasons if r]
    return next((r for r in reasons if r.startswith("invalid_run")), reasons[0] if reasons else None)


def record_problem(r, budget):
    if not r.get("user_files_unchanged"):
        return "invalid_run:user_files_changed"
    if r.get(TOK) is None or r.get("estimated_cost_usd_v1") is None:
        return "pivot:telemetry_missing"
    if r["estimated_cost_usd_v1"] > budget["max_estimated_cost_usd_per_invocation"]:
        return "pivot:per_invocation_cost_exceeded"
    return None


def active_seconds(rs) -> float:
    """已完成 slot 的实际执行时长（Target 加外置验收）。

    墙钟预算按这个累计，而不是每次 run 重新计时：续跑（含暂停后续跑）不能把已经用掉的
    时间丢掉，两次调用之间的人工等待也不计入。字段取自 runs.jsonl 现有记录，不新增状态文件。
    """
    return sum((r.get("wall_time_v1") or 0) + (r.get("oracle_seconds") or 0) for r in rs)


def run(exp: Path, pause_after_new_slots: int | None = None):
    p, cfg = load(exp)
    validate_schedule(p)
    budget = p["experiment"]["budget"]
    slot_wall = (
        budget["max_process_seconds_per_invocation"]
        + budget["process_kill_grace_seconds"]
        + budget["max_oracle_seconds_per_invocation"]
    )
    proposal_digest = sha(exp / "proposal.json")
    rs = records(exp)
    # 续跑时先核对完整性：invalid_run 优先于任何已有的 pivot（协议「判定」第 1 条）。
    problem = integrity_problem(p, cfg, exp)
    stop = most_severe(
        [
            stop_reason(exp),
            None if schedule_matches(rs, p["experiment"]["schedule"]) else "invalid_run:records_schedule_mismatch",
            "invalid_run:" + problem if problem else None,
            *(record_problem(r, budget) for r in rs),
        ]
    )
    done = {r["slot"] for r in rs}
    # 完整性优先：配置被改写时先记 invalid_run，不用损坏的 sandbox 段提前抛错。
    sandbox = phe_sandbox.load_settings(cfg) if not stop else None
    start = time.monotonic()
    prior_seconds = active_seconds(rs)
    new_slots = 0
    paused = False
    for item in p["experiment"]["schedule"]:
        if stop:
            break
        if item["slot"] in done:
            continue
        if sha(exp / "proposal.json") != proposal_digest:
            stop = "invalid_run:proposal_hash_changed"
            break
        p, cfg = load(exp)
        problem = integrity_problem(p, cfg, exp)
        if problem:
            stop = "invalid_run:" + problem
            break
        spent = sum(r.get("estimated_cost_usd_v1") or 0 for r in rs)
        elapsed = prior_seconds + time.monotonic() - start
        if elapsed + slot_wall > budget["max_experiment_wall_seconds"]:
            stop = "pivot:wall_budget_exhausted"
            break
        if spent + budget["max_estimated_cost_usd_per_invocation"] > budget["max_estimated_cost_usd_total"]:
            stop = "pivot:cost_budget_exhausted"
            break
        art = exp / "artifacts" / f"slot-{item['slot']:02d}"
        if art.exists():
            stop = "pivot:runner_crashed_no_record"
            break
        # 暂停排在其他准入检查之后：完成 N 个新 slot 后就不再启动，但任何停止条件先记账。
        if pause_after_new_slots is not None and new_slots >= pause_after_new_slots:
            paused = True
            break
        before = len(rs)
        try:
            run_slot(exp, cfg, item, budget, receipt_required="required_control" in p["rules"], sandbox=sandbox)
        except phe_sandbox.SandboxError:
            # 隔离不可用/不生效是 fail closed 的前置拒绝，不记成 slot 失败或 pivot。
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"slot {item['slot']} runner failed: {exc}", file=sys.stderr)
            stop = crash_stop(p, cfg, exp, proposal_digest)
            break
        rs = records(exp)
        if len(rs) != before + 1:
            stop = crash_stop(p, cfg, exp, proposal_digest)
            break
        new_slots += 1
        r = rs[-1]
        print(f"slot {r['slot']} {r['group']} complete", flush=True)
        stop = record_problem(r, budget)
        if sha(exp / "proposal.json") != proposal_digest:
            stop = "invalid_run:proposal_hash_changed"
        else:
            problem = integrity_problem(p, cfg, exp)
            if problem:
                stop = "invalid_run:" + problem
    if paused:
        # 主动暂停不是停止事件：新 slot 的完整记录和验收结果已落盘，不写 stop.json、不改记录，
        # 下一轮 run 按原 schedule 跳已完成。
        print(
            f"paused: 本次调用完成 {new_slots} 个新 slot，未启动下一个 Target；未写 stop.json，按原 schedule 续跑",
            flush=True,
        )
        return
    (exp / "stop.json").write_text(json.dumps({"stop_reason": stop}, ensure_ascii=False))
    print("stop_reason:", stop)


def judge(exp: Path, out: Path | None = None):
    p, _ = load(exp)
    result = evaluate(p, records(exp), stop_reason(exp))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (out if out is not None else exp / "result.json").write_text(text)
    print(text)


def positive_int(text: str) -> int:
    """命令行用的正整数：0 或负数没有意义，直接报参数错误。"""
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0:
        raise argparse.ArgumentTypeError(f"需要正整数：{text!r}")
    return value


def main():
    ap = argparse.ArgumentParser(description="PHE")
    commands = ap.add_subparsers(dest="cmd", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("experiment_dir", type=Path)
    run_parser.add_argument(
        "--pause-after-new-slots",
        type=positive_int,
        metavar="N",
        help="本次调用完成 N 个新 slot 后、启动下一个 Target 前主动暂停；不写 stop.json，按原 schedule 续跑",
    )
    judge_parser = commands.add_parser("judge")
    judge_parser.add_argument("experiment_dir", type=Path)
    judge_parser.add_argument("--out", type=Path)
    log_parser = commands.add_parser("log")
    log_parser.add_argument("experiment_dir", type=Path)
    log_parser.add_argument("--host", required=True)
    log_parser.add_argument("--step", required=True)
    log_parser.add_argument("--note", default="")
    commands.add_parser("status").add_argument("experiment_dir", type=Path)
    a = ap.parse_args()
    try:
        if a.cmd == "run":
            run(a.experiment_dir.resolve(), a.pause_after_new_slots)
        elif a.cmd == "judge":
            judge(a.experiment_dir.resolve(), a.out)
        elif a.cmd == "log":
            sys.exit(host_log(a.experiment_dir, a.host, a.step, a.note))
        else:
            sys.exit(host_status(a.experiment_dir))
    except (OSError, ValueError, KeyError) as exc:
        ap.exit(1, f"phe: {exc}\n")


if __name__ == "__main__":
    main()
