#!/usr/bin/env python3
"""冻结 proposal 驱动的评测入口（标准库）。"""

import argparse
import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

TOK = "total_" + "tokens_v1"
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
    shutil.copyfile(cfg["oracle_test_src"], check / cfg["oracle_test_dest"])
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
        "argv_sha256": hashlib.sha256(json.dumps(argv[3:]).encode()).hexdigest(),
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

    diff = subprocess.run(
        ["git", "diff", "HEAD", "--stat"], cwd=work, capture_output=True, text=True, check=True
    ).stdout
    (art / "diff.patch").write_text(
        subprocess.run(["git", "diff", "HEAD"], cwd=work, capture_output=True, text=True, check=True).stdout
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


def validate_schedule(p: dict):
    experiment = p["experiment"]
    counts, schedule = experiment["groups"], experiment["schedule"]
    if not counts or any(type(n) is not int or n <= 0 for n in counts.values()):
        raise ValueError("组次数必须为正整数")
    total = sum(counts.values())
    if total != experiment["budget"]["max_target_invocations"] or total != len(schedule):
        raise ValueError("组次数之和、总次数与 schedule 长度不一致")
    if Counter(item["group"] for item in schedule) != Counter(counts):
        raise ValueError("schedule 的各组次数不一致")
    if (
        any(type(item["slot"]) is not int or item["slot"] <= 0 for item in schedule)
        or len({item["slot"] for item in schedule}) != total
    ):
        raise ValueError("schedule slot 必须为不重复的正整数")
    if len({(item["group"], item["block"]) for item in schedule}) != total:
        raise ValueError("schedule 每个组 × 区组只能运行一次")
    if p["gates"].get("version", "v1") not in ("v1", "v2"):
        raise ValueError("不支持的 gates.version")
    roles(p)


def roles(p: dict) -> dict:
    groups = p["experiment"]["groups"]
    variants = p["harness"]["variants"]
    baselines = [g for g in groups if variants[g]["remove"] == []]
    if len(baselines) != 2:
        raise ValueError("proposal 必须定义两个未删规则的基线组")
    result: dict[str, Any] = {"baseline": baselines}
    for role in ("real_rule", "required_control", "redundant_control"):
        matches = [g for g in groups if variants[g]["remove"] == [role]]
        if len(matches) > 1 or (role == "real_rule" and not matches):
            raise ValueError(f"proposal 的 {role} 角色不唯一或缺失")
        result[role] = matches[0] if matches else None
    if len(baselines) + sum(result[k] is not None for k in result if k != "baseline") != len(groups):
        raise ValueError("proposal 包含无法判定角色的组")
    return result


def integrity_problem(p, cfg, exp) -> str | None:
    if sha(exp / "config.json") != p["runner"]["config_sha256"]:
        return "config_hash_changed"
    for g in p["experiment"]["groups"]:
        if sha(Path(cfg["harness"][g])) != p["harness"]["variants"][g]["snapshot_sha256"]:
            return f"harness_hash_changed:{g}"
    return None


def record_problem(r, budget):
    if not r.get("user_files_unchanged"):
        return "invalid_run:user_files_changed"
    if r.get(TOK) is None or r.get("estimated_cost_usd_v1") is None:
        return "pivot:telemetry_missing"
    if r["estimated_cost_usd_v1"] > budget["max_estimated_cost_usd_per_invocation"]:
        return "pivot:per_invocation_cost_exceeded"
    return None


def schedule_matches(rs, schedule, complete=False):
    expected = {item["slot"]: item["group"] for item in schedule}
    slots = [r["slot"] for r in rs]
    return (
        len(set(slots)) == len(slots)
        and all(expected.get(r["slot"]) == r["group"] for r in rs)
        and (not complete or len(rs) == len(schedule))
    )


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
    stop = stop_reason(exp)
    if not schedule_matches(rs, p["experiment"]["schedule"]):
        stop = "invalid_run:records_schedule_mismatch"
    for r in rs:
        stop = stop or record_problem(r, budget)
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
            stop = "pivot:runner_crashed_no_record"
            break
        rs = records(exp)
        if len(rs) != before + 1:
            stop = "pivot:runner_crashed_no_record"
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


def valid(r) -> bool:
    return (
        r.get("exit_code") is not None
        and not r.get("process_timed_out")
        and r.get(TOK) is not None
        and r.get("estimated_cost_usd_v1") is not None
        and r.get("test_pass") is not None
    )


def evaluate(p: dict, rs: list, stop) -> dict:
    validate_schedule(p)
    role = roles(p)
    a1, a2 = role["baseline"]
    real, required, redundant = (role[k] for k in ("real_rule", "required_control", "redundant_control"))
    gq, sg = p["gates"]["go_requires"], p["real_rule_signal"]
    groups = p["experiment"]["groups"]
    schedule = p["experiment"]["schedule"]
    sched = {i["slot"]: i["block"] for i in schedule}
    by = {g: [r for r in rs if r["group"] == g] for g in groups}
    S = {g: sum(1 for r in by[g] if r.get("test_pass")) for g in groups}
    C = {g: sum(1 for r in by[g] if r.get("contract_pass")) for g in groups}
    T = {
        g: statistics.median([r[TOK] for r in by[g]]) if by[g] and all(r.get(TOK) for r in by[g]) else None
        for g in groups
    }
    a_tok = [r[TOK] for r in by[a1] + by[a2] if r.get(TOK)]
    TA = statistics.median(a_tok) if len(a_tok) == groups[a1] + groups[a2] else None
    t1, t2 = T[a1], T[a2]
    n_gap = abs(t1 - t2) / TA if TA and TA > 0 and t1 and t2 else None
    n_mad = statistics.median([abs(x - TA) for x in a_tok]) / TA if TA and TA > 0 else None
    n_cost = max(n_gap, 2 * n_mad) if n_gap is not None and n_mad is not None else None
    if p["gates"].get("version", "v1") == "v2" and redundant:
        neutral_tokens = T[redundant]
        n_cost = max(n_cost, abs(1 - neutral_tokens / TA)) if n_cost is not None and neutral_tokens and TA else None
    lo_c, lo_s = min(C[a1], C[a2]), min(S[a1], S[a2])
    gates = {
        "完整性": (
            schedule_matches(rs, schedule, complete=True)
            and len(rs) == gq["expected_valid_completed_runs"]
            and sum(valid(r) for r in rs) == gq["required_telemetry_complete_runs"]
            and all(valid(r) and r.get("user_files_unchanged") for r in rs)
        ),
        "基线能力": (
            min(S[a1], S[a2]) >= gq["baseline_task_min_passes_each"]
            and min(C[a1], C[a2]) >= gq["baseline_contract_min_passes_each"]
        ),
        "A/A 质量波动": (
            abs(S[a1] - S[a2]) <= gq["aa_task_max_pass_count_gap"]
            and abs(C[a1] - C[a2]) <= gq["aa_contract_max_pass_count_gap"]
        ),
        "A/A Token 波动": (
            n_gap is not None
            and n_gap <= gq["aa_token_median_max_relative_gap"]
            and n_mad <= gq["aa_pooled_tokens_max_relative_mad"]
        ),
        "必要信息可识别": (
            lo_c - C[required] >= gq["required_control_min_contract_drop_vs_weaker_baseline"]
            if required
            else "not_applicable"
        ),
        "冗余控制不误报": (
            S[redundant] >= gq["redundant_task_min_passes"]
            and C[redundant] >= gq["redundant_contract_min_passes"]
            and all(
                abs(X[redundant] - X[a]) <= gq["redundant_max_count_gap_to_each_baseline"]
                for X in (S, C)
                for a in (a1, a2)
            )
            if redundant
            else "not_applicable"
        ),
        "资源": stop is None,
    }
    if stop and stop.startswith("invalid_run"):
        decision = "invalid_run"
    elif all(v is True or v == "not_applicable" for v in gates.values()):
        decision = "go"
    else:
        decision = "pivot"

    signal, detail = "inconclusive", {}
    if decision == "go":
        if lo_s - S[real] >= sg["regression_min_task_pass_count_drop_vs_weaker_baseline"]:
            signal = "regression_signal"
        elif S[real] >= max(S[a1], S[a2]) and C[real] >= max(C[a1], C[a2]):
            cell = {(r["group"], sched[r["slot"]]): r for r in rs}
            blocks = {sched[r["slot"]] for r in by[real]}
            paired = all({sched[r["slot"]] for r in by[a]} == blocks for a in (a1, a2))

            def quality_drops(field):
                return sum(not cell[(real, k)][field] and cell[(a1, k)][field] and cell[(a2, k)][field] for k in blocks)

            quality_ok = (
                paired
                and quality_drops("test_pass") <= sg["max_blocks_with_task_quality_drop_vs_both_baselines"]
                and quality_drops("contract_pass") <= sg["max_blocks_with_contract_drop_vs_both_baselines"]
            )
            saving = 1 - float(T[real] or 0) / float(TA or 1)
            wins = (
                sum(cell[(real, k)][TOK] < statistics.median([cell[(a1, k)][TOK], cell[(a2, k)][TOK]]) for k in blocks)
                if paired
                else 0
            )
            detail = {"saving": round(saving, 4), "block_wins": wins, "per_block_quality_ok": quality_ok}
            if (
                quality_ok
                and saving >= sg["min_token_saving_fraction"]
                and n_cost is not None
                and saving > n_cost
                and wins >= sg["min_blocks_with_token_saving"]
            ):
                signal = "follow_up_candidate"

    return {
        "probe_decision": decision,
        "stop_reason": stop,
        "runs": len(rs),
        "S": S,
        "C": C,
        "T_median": T,
        "T_A": TA,
        "N_gap": n_gap,
        "N_mad": n_mad,
        "N_cost": n_cost,
        "gates": gates,
        "real_rule_signal": signal,
        "real_rule_detail": detail,
        "estimated_cost_usd_total": round(sum(r.get("estimated_cost_usd_v1") or 0 for r in rs), 4),
        "production_retire_allowed": False,
    }


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
    a = ap.parse_args()
    try:
        if a.cmd == "run":
            run(a.experiment_dir.resolve())
        else:
            judge(a.experiment_dir.resolve(), a.out)
    except (OSError, ValueError, KeyError) as exc:
        ap.exit(1, f"phe: {exc}\n")


if __name__ == "__main__":
    main()
