#!/usr/bin/env python3
"""M-1 正式实验的调度与判定（docs/M-1_PROTOCOL.md「预算」「门槛」「判定」）。

run：按冻结 proposal 的 25 项顺序逐次调用 run_spike.py；每次启动前核对 proposal、配置、
Harness 的 Hash 和剩余预算，出现完整性事故或预算问题立即停止准入。
judge：只读 runs.jsonl，按写死的门槛输出 result.json 和 report.md。
"""

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

RUNNER = Path(__file__).with_name("run_spike.py")
TOK = "total_" + "tokens_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(exp: Path):
    raw = (exp / "proposal.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != (exp / "proposal.sha256").read_text().strip():
        raise SystemExit("proposal.sha256 不匹配，停止")
    p = json.loads(raw)
    if json.loads((exp / "approval.json").read_text())["proposal_sha256"] != hashlib.sha256(raw).hexdigest():
        raise SystemExit("approval 未绑定当前 proposal，停止")
    return p, json.loads((exp / "config.json").read_text())


def records(exp: Path) -> list:
    path = exp / "runs.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def integrity_problem(p, cfg, exp) -> str | None:
    if sha(exp / "config.json") != p["runner"]["config_sha256"]:
        return "config_hash_changed"
    for g, v in p["harness"]["variants"].items():
        if sha(Path(cfg["harness"][g])) != v["snapshot_sha256"]:
            return f"harness_hash_changed:{g}"
    return None


def run(exp: Path):
    p, cfg = load(exp)
    b = p["experiment"]["budget"]
    per_cost, total_cost = b["max_estimated_cost_usd_per_invocation"], b["max_estimated_cost_usd_total"]
    slot_wall = b["max_process_seconds_per_invocation"] + 33
    done = {r["slot"] for r in records(exp)}
    start = time.monotonic()
    stop = None
    for item in p["experiment"]["schedule"]:
        if item["slot"] in done:
            continue
        p, cfg = load(exp)
        stop = integrity_problem(p, cfg, exp)
        if stop:
            stop = "invalid_run:" + stop
            break
        spent = sum(r.get("estimated_cost_usd_v1") or 0 for r in records(exp))
        if time.monotonic() - start + slot_wall > b["max_experiment_wall_seconds"]:
            stop = "pivot:wall_budget_exhausted"
            break
        if spent + per_cost > total_cost:
            stop = "pivot:cost_budget_exhausted"
            break
        before = len(records(exp))
        subprocess.run(
            [
                sys.executable,
                "-I",
                str(RUNNER),
                str(exp / "config.json"),
                "--slot",
                str(item["slot"]),
                "--group",
                item["group"],
            ],
            stdout=subprocess.DEVNULL,
            check=False,
        )
        rs = records(exp)
        if len(rs) == before:
            stop = "pivot:runner_crashed_no_record"
            break
        r = rs[-1]
        print(
            f"slot {r['slot']:2d} {r['group']:<11} pass={r.get('test_pass')} receipt={r.get('receipt_match')} "
            f"tok={r.get(TOK)} cost={r.get('estimated_cost_usd_v1')} t={r.get('wall_time_v1')}",
            flush=True,
        )
        if not r.get("user_files_unchanged"):
            stop = "invalid_run:user_files_changed"
            break
        if r.get(TOK) is None or r.get("estimated_cost_usd_v1") is None:
            stop = "pivot:telemetry_missing"
            break
        if r["estimated_cost_usd_v1"] > per_cost:
            stop = "pivot:per_invocation_cost_exceeded"
            break
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


def judge(exp: Path):
    p, _ = load(exp)
    gq = p["gates"]["go_requires"]
    rs = records(exp)
    sched = {i["slot"]: i["block"] for i in p["experiment"]["schedule"]}
    cell = {(r["group"], sched[r["slot"]]): r for r in rs}
    stop = json.loads((exp / "stop.json").read_text())["stop_reason"] if (exp / "stop.json").exists() else None
    groups = ["A1", "A2", "B_real", "B_required", "B_redundant"]
    by = {g: [r for r in rs if r["group"] == g] for g in groups}
    S = {g: sum(1 for r in by[g] if r.get("test_pass")) for g in groups}
    C = {g: sum(1 for r in by[g] if r.get("contract_pass")) for g in groups}
    T = {
        g: statistics.median([r[TOK] for r in by[g]]) if by[g] and all(r.get(TOK) for r in by[g]) else None
        for g in groups
    }
    a_tok = [r[TOK] for r in by["A1"] + by["A2"] if r.get(TOK)]
    TA = statistics.median(a_tok) if len(a_tok) == 10 else None
    n_gap = abs(T["A1"] - T["A2"]) / TA if TA and T["A1"] and T["A2"] else None
    n_mad = statistics.median([abs(x - TA) for x in a_tok]) / TA if TA else None
    n_cost = max(n_gap, 2 * n_mad) if n_gap is not None and n_mad is not None else None
    lo_c, lo_s = min(C["A1"], C["A2"]), min(S["A1"], S["A2"])
    gates = {
        "完整性": len(rs) == 25 and all(valid(r) for r in rs) and all(r.get("user_files_unchanged") for r in rs),
        "基线能力": min(S["A1"], S["A2"], C["A1"], C["A2"]) >= 4,
        "A/A 质量波动": abs(S["A1"] - S["A2"]) <= 1 and abs(C["A1"] - C["A2"]) <= 1,
        "A/A Token 波动": n_gap is not None
        and n_gap <= gq["aa_token_median_max_relative_gap"]
        and n_mad <= gq["aa_pooled_tokens_max_relative_mad"],
        "必要信息可识别": lo_c - C["B_required"] >= 3,
        "冗余控制不误报": S["B_redundant"] >= 4
        and C["B_redundant"] >= 4
        and all(abs(X["B_redundant"] - X[a]) <= 1 for X in (S, C) for a in ("A1", "A2")),
        "资源": stop is None,
    }
    if stop and stop.startswith("invalid_run"):
        decision = "invalid_run"
    elif all(gates.values()):
        decision = "go"
    else:
        decision = "pivot"

    signal, detail = "inconclusive", {}
    if decision == "go":
        if lo_s - S["B_real"] >= 2:
            signal = "regression_signal"
        elif S["B_real"] >= max(S["A1"], S["A2"]) and C["B_real"] >= max(C["A1"], C["A2"]):
            # go 已保证 25 次全部有效，每个「组 × 区组」都有完整记录。
            blocks = sorted(set(sched.values()))

            def not_worse(k, field):
                return cell[("B_real", k)][field] or not (cell[("A1", k)][field] and cell[("A2", k)][field])

            quality_ok = all(not_worse(k, "test_pass") and not_worse(k, "contract_pass") for k in blocks)
            saving = 1 - float(T["B_real"] or 0) / float(TA or 1)
            wins = sum(
                1
                for k in blocks
                if cell[("B_real", k)][TOK] < statistics.median([cell[("A1", k)][TOK], cell[("A2", k)][TOK]])
            )
            detail = {"saving": round(saving, 4), "block_wins": wins, "per_block_quality_ok": quality_ok}
            if quality_ok and saving >= 0.15 and n_cost is not None and saving > n_cost and wins >= 4:
                signal = "follow_up_candidate"

    result = {
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
    (exp / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "judge"])
    ap.add_argument("experiment_dir")
    a = ap.parse_args()
    {"run": run, "judge": judge}[a.cmd](Path(a.experiment_dir).resolve())
