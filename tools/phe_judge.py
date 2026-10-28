"""PHE 判定：从 proposal 推出组角色，按版本化门槛计算 probe_decision 与 real_rule_signal（标准库）。"""

import statistics
from collections import Counter
from typing import Any

TOK = "total_" + "tokens_v1"


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


def schedule_matches(rs, schedule, complete=False):
    expected = {item["slot"]: item["group"] for item in schedule}
    slots = [r["slot"] for r in rs]
    return (
        len(set(slots)) == len(slots)
        and all(expected.get(r["slot"]) == r["group"] for r in rs)
        and (not complete or len(rs) == len(schedule))
    )


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
