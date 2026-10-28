#!/usr/bin/env python3
"""冻结 proposal 驱动的评测入口（标准库）。"""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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


def run_with_timeout(argv, cwd, env, timeout_s, grace_s=3):
    start = time.monotonic()
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=grace_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
    return proc.returncode, out, err, time.monotonic() - start, timed_out


def build_workdir(base: Path, harness: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix="phe-run-", dir="/tmp")) / "repo"
    subprocess.run(["cp", "-Rc", str(base), str(work)], check=True)
    shutil.rmtree(work / ".git")
    shutil.copyfile(harness, work / "AGENTS.md")
    git = ["git", "-c", "user.name=phe", "-c", "user.email=phe@local"]
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(git + ["commit", "-qm", "task start"], cwd=work, check=True)
    return work


def oracle(work: Path, cfg: dict, receipt_required=True) -> dict:
    check_root = Path(tempfile.mkdtemp(prefix="phe-oracle-", dir="/tmp"))
    check = check_root / "repo"
    subprocess.run(["cp", "-Rc", str(work), str(check)], check=True)
    # 可信测试覆盖 Agent 改过的同名文件；oracle_tests 支持跨包的多个文件。
    tests = cfg.get("oracle_tests") or [{"src": cfg["oracle_test_src"], "dest": cfg["oracle_test_dest"]}]
    for t in tests:
        shutil.copyfile(t["src"], check / t["dest"])
    rc, out, err, dur, to = run_with_timeout(
        cfg["oracle_argv"],
        check / cfg["oracle_cwd"],
        os.environ.copy(),
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


def run_slot(exp: Path, cfg: dict, item: dict, budget: dict, receipt_required=True):
    art = exp / "artifacts" / f"slot-{item['slot']:02d}"
    art.mkdir(parents=True, exist_ok=True)
    harness = Path(cfg["harness"][item["group"]])
    work = build_workdir(Path(cfg["base_dir"]), harness)
    # 启动前记下任务起点；Agent 可能在临时仓库里提交甚至改写历史，diff 一律对比这个 SHA。
    root = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
    ).stdout.strip()
    argv = cfg.get("target_argv") or [
        "claude",
        "-p",
        cfg["prompt"],
        *cfg["claude_args"],
        "--settings",
        json.dumps(cfg["settings"]),
        "--debug-file",
        str(art / "debug.log"),
    ]
    env = os.environ.copy()
    env.update(cfg["env"])
    for k in cfg.get("env_unset", []):
        env.pop(k, None)

    record = {
        "experiment_id": cfg["experiment_id"],
        "slot": item["slot"],
        "group": item["group"],
        "harness_sha256": sha256_file(harness),
        # 默认命令沿用旧口径（跳过 claude -p <prompt>）；自定义 target_argv 记录完整命令。
        "argv_sha256": hashlib.sha256(json.dumps(argv if cfg.get("target_argv") else argv[3:]).encode()).hexdigest(),
        "workdir": str(work),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "user_files_before": user_fingerprint(),
    }
    (art / "argv.json").write_text(json.dumps(argv, ensure_ascii=False, indent=2))
    rc, out, err, dur, to = run_with_timeout(
        argv, work, env, budget["max_process_seconds_per_invocation"], budget["process_kill_grace_seconds"]
    )
    (art / "stdout.json").write_bytes(out)
    (art / "stderr.txt").write_bytes(err)
    record.update({"exit_code": rc, "wall_time_v1": round(dur, 2), "process_timed_out": to})
    try:
        result = json.loads(out)
        record.update(usage_from(result))
    except json.JSONDecodeError:
        record["result_parse_error"] = True

    diff = subprocess.run(["git", "diff", root, "--stat"], cwd=work, capture_output=True, text=True, check=True).stdout
    (art / "diff.patch").write_text(
        subprocess.run(["git", "diff", root], cwd=work, capture_output=True, text=True, check=True).stdout
    )
    record["diff_stat"] = diff.strip().splitlines()[-1] if diff.strip() else ""
    status = subprocess.run(["git", "status", "--short"], cwd=work, capture_output=True, text=True, check=True).stdout
    record["untracked_or_changed"] = status.strip().splitlines()

    oracle_cfg = dict(
        cfg,
        oracle_timeout_s=budget["max_oracle_seconds_per_invocation"],
        process_kill_grace_seconds=budget["process_kill_grace_seconds"],
    )
    o = oracle(work, oracle_cfg, receipt_required)
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


def run(exp: Path):
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
    start = time.monotonic()
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
        if time.monotonic() - start + slot_wall > budget["max_experiment_wall_seconds"]:
            stop = "pivot:wall_budget_exhausted"
            break
        if spent + budget["max_estimated_cost_usd_per_invocation"] > budget["max_estimated_cost_usd_total"]:
            stop = "pivot:cost_budget_exhausted"
            break
        art = exp / "artifacts" / f"slot-{item['slot']:02d}"
        if art.exists():
            stop = "pivot:runner_crashed_no_record"
            break
        before = len(rs)
        try:
            run_slot(exp, cfg, item, budget, receipt_required="required_control" in p["rules"])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"slot {item['slot']} runner failed: {exc}", file=sys.stderr)
            stop = crash_stop(p, cfg, exp, proposal_digest)
            break
        rs = records(exp)
        if len(rs) != before + 1:
            stop = crash_stop(p, cfg, exp, proposal_digest)
            break
        r = rs[-1]
        print(f"slot {r['slot']} {r['group']} complete", flush=True)
        stop = record_problem(r, budget)
        if sha(exp / "proposal.json") != proposal_digest:
            stop = "invalid_run:proposal_hash_changed"
        else:
            problem = integrity_problem(p, cfg, exp)
            if problem:
                stop = "invalid_run:" + problem
    (exp / "stop.json").write_text(json.dumps({"stop_reason": stop}, ensure_ascii=False))
    print("stop_reason:", stop)


def judge(exp: Path, out: Path | None = None):
    p, _ = load(exp)
    result = evaluate(p, records(exp), stop_reason(exp))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (out if out is not None else exp / "result.json").write_text(text)
    print(text)


def main():
    ap = argparse.ArgumentParser(description="PHE")
    commands = ap.add_subparsers(dest="cmd", required=True)
    commands.add_parser("run").add_argument("experiment_dir", type=Path)
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
            run(a.experiment_dir.resolve())
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
