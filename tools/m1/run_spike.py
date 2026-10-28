#!/usr/bin/env python3
"""M-1 最小 Runner：按 docs/M-1_PROTOCOL.md 启动一次隔离的 `claude -p` 运行并外置验收。

只用标准库。每次运行：从基线目录复制出新工作区 → 用该组的 Harness 重建只含一次提交的
Git 仓库 → 核对用户级文件 Hash → 启动 Target → 外置验收 → 追加一行到 runs.jsonl。
它不判定 go/pivot，只产出原始记录。
"""
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

HOME = Path.home()
# 用户级内容：排除不掉时作为常量核对；运行前后必须一致。
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
    """单调时钟计时；超时先 SIGTERM 进程组，宽限后 SIGKILL。"""
    start = time.monotonic()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
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
    """复制基线（含 node_modules），去掉原 .git，换上该组 Harness，重建单提交仓库。"""
    work = Path(tempfile.mkdtemp(prefix="phe-run-", dir="/tmp")) / "repo"
    subprocess.run(["cp", "-Rc", str(base), str(work)], check=True)
    shutil.rmtree(work / ".git")
    shutil.copyfile(harness, work / "AGENTS.md")
    git = ["git", "-c", "user.name=phe", "-c", "user.email=phe@local"]
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(git + ["commit", "-qm", "task start"], cwd=work, check=True)
    return work


def oracle(work: Path, cfg: dict) -> dict:
    """在工作区副本上覆盖可信测试文件再运行；Agent 改过的测试不算数。"""
    check_root = Path(tempfile.mkdtemp(prefix="phe-oracle-", dir="/tmp"))
    check = check_root / "repo"
    subprocess.run(["cp", "-Rc", str(work), str(check)], check=True)
    shutil.copyfile(cfg["oracle_test_src"], check / cfg["oracle_test_dest"])
    rc, out, err, dur, to = run_with_timeout(
        cfg["oracle_argv"], check / cfg["oracle_cwd"], os.environ.copy(),
        cfg["oracle_timeout_s"])
    text = (out + err).decode(errors="replace")
    tests_line = next((line.strip() for line in text.splitlines() if line.strip().startswith("Tests ")), None)
    receipt_path = work / ".phe-calibration-receipt"
    receipt = receipt_path.read_text().strip() if receipt_path.exists() else None
    # 配置只存回执值的 Hash，Agent 即使读到配置也拿不到明文。
    receipt_match = receipt is not None and hashlib.sha256(receipt.encode()).hexdigest() == cfg["nonce_sha256"]
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
        "contract_pass": None if test_pass is None else (test_pass and receipt_match),
        "_oracle_output": text,
    }


def usage_from(result: dict) -> dict:
    mu = result.get("modelUsage") or {}
    total = 0
    for v in mu.values():
        total += sum(v.get(k, 0) for k in
                     ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
    return {
        "model_usage": mu,
        "total_tokens_v1": total if mu else None,
        "estimated_cost_usd_v1": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--slot", type=int, required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text())
    exp = Path(cfg["experiment_dir"])
    art = exp / "artifacts" / f"slot-{a.slot:02d}"
    art.mkdir(parents=True, exist_ok=True)

    harness = Path(cfg["harness"][a.group])
    work = build_workdir(Path(cfg["base_dir"]), harness)
    argv = ["claude", "-p", cfg["prompt"], *cfg["claude_args"],
            "--settings", json.dumps(cfg["settings"]),
            "--debug-file", str(art / "debug.log")]
    env = os.environ.copy()
    env.update(cfg["env"])
    for k in cfg.get("env_unset", []):
        env.pop(k, None)

    record = {
        "experiment_id": cfg["experiment_id"], "slot": a.slot, "group": a.group,
        "harness_sha256": sha256_file(harness),
        "argv_sha256": hashlib.sha256(json.dumps(argv[3:]).encode()).hexdigest(),
        "workdir": str(work), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "user_files_before": user_fingerprint(),
    }
    (art / "argv.json").write_text(json.dumps(argv, ensure_ascii=False, indent=2))
    if a.dry_run:
        print(json.dumps({"argv": argv, "workdir": str(work)}, ensure_ascii=False, indent=2))
        return

    rc, out, err, dur, to = run_with_timeout(argv, work, env, cfg["process_timeout_s"])
    (art / "stdout.json").write_bytes(out)
    (art / "stderr.txt").write_bytes(err)
    record.update({"exit_code": rc, "wall_time_v1": round(dur, 2), "process_timed_out": to})
    try:
        result = json.loads(out)
        record.update(usage_from(result))
    except json.JSONDecodeError:
        record["result_parse_error"] = True

    diff = subprocess.run(["git", "diff", "HEAD", "--stat"], cwd=work, capture_output=True, text=True).stdout
    (art / "diff.patch").write_text(
        subprocess.run(["git", "diff", "HEAD"], cwd=work, capture_output=True, text=True).stdout)
    record["diff_stat"] = diff.strip().splitlines()[-1] if diff.strip() else ""
    status = subprocess.run(["git", "status", "--short"], cwd=work, capture_output=True, text=True).stdout
    record["untracked_or_changed"] = status.strip().splitlines()

    o = oracle(work, cfg)
    (art / "oracle.txt").write_text(o.pop("_oracle_output"))
    record.update(o)
    record["user_files_after"] = user_fingerprint()
    record["user_files_unchanged"] = record["user_files_before"] == record["user_files_after"]

    with open(exp / "runs.jsonl", "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in ("model_usage", "user_files_before", "user_files_after")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
