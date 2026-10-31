"""闭环切片 3：复盘账本、对照原预测并记录宿主的判断（标准库）。

`review-plan` 只读列出每个执行的观察窗、原预测、证据、报告引用和全部 review 历史；`review` 追加一条
`kind=review` 事实，记下宿主的 verdict、prediction_met、理由、证据指针和下一步。工具只记录、不判断
效果：不改执行状态，不调用 restore，不碰配置和发布。同一执行可以多次 review，旧行不改写。
"""

import argparse
import json
import os
from pathlib import Path

from change_ledger import append_ledger, executions, read_ledger, require, sha256_bytes
from release_deploy import GuardError, Sandbox, refusal_reason, utc_now

# 复盘只记录宿主的判断，取值固定，便于事后按同一口径统计。
REVIEW_VERDICTS = ("keep", "adjust", "revert", "observe")
PREDICTION_RESULTS = ("met", "not_met", "unknown")
REVIEW_NOTE = (
    "观察窗只是候选取样范围（应用到恢复或至今），不证明 Host 加载过配置；"
    "review 只记录宿主的判断和证据指针，不判断效果。"
)


def review_plan(facts: list[dict]) -> list[dict]:
    """每个执行列出观察窗、原预测、证据、报告引用和全部 review 历史；状态仍从事实推导。"""
    applies = {fact["execution_id"]: fact for fact in facts if fact["kind"] == "apply"}
    history: dict[str, list[dict]] = {}
    for fact in facts:
        if fact["kind"] == "review":
            history.setdefault(fact["execution_id"], []).append(fact)
    plan = []
    for execution in executions(facts):
        apply_fact = applies[execution["execution_id"]]
        reviews = history.get(execution["execution_id"], [])
        plan.append(
            {
                "execution_id": execution["execution_id"],
                "proposal_id": execution["proposal_id"],
                "state": execution["state"],
                "applied_at": execution["applied_at"],
                "restored_at": execution["restored_at"],
                "prediction": apply_fact["prediction"],
                "evidence": apply_fact["evidence"],
                "report": apply_fact["report"],
                "observation_window": {"from": execution["applied_at"], "to": execution["restored_at"]},
                "reviews": reviews,
                "latest_review": reviews[-1] if reviews else None,
            }
        )
    return plan


def cmd_review_plan(args: argparse.Namespace) -> int:
    phe = args.phe_home
    print(
        json.dumps(
            {"phe_home": str(phe), "note": REVIEW_NOTE, "executions": review_plan(read_ledger(phe))},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def read_review(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    record = json.loads(data.decode("utf-8"))
    verdict = require(record, "verdict", "review 记录")
    if verdict not in REVIEW_VERDICTS:
        raise ValueError(f"review 记录的 verdict 只能是 {' / '.join(REVIEW_VERDICTS)}：{verdict}")
    prediction_met = require(record, "prediction_met", "review 记录")
    if prediction_met not in PREDICTION_RESULTS:
        raise ValueError(f"review 记录的 prediction_met 只能是 {' / '.join(PREDICTION_RESULTS)}：{prediction_met}")
    for key in ("reason", "observations", "next"):
        require(record, key, "review 记录")
    return record, sha256_bytes(data)


def build_review_row(facts: list[dict], execution_id: str, record: dict, record_sha: str, record_path: str) -> dict:
    """一条 review 事实的字段：沙箱入口与产品批准入口共用同一账本行形状。"""
    execution = next((item for item in executions(facts) if item["execution_id"] == execution_id), None)
    if execution is None:
        raise ValueError(f"账本里没有执行：{execution_id}")
    return {
        "kind": "review",
        "at": utc_now(),
        "execution_id": execution_id,
        "proposal_id": execution["proposal_id"],
        "record": record,
        "record_path": record_path,
        "record_sha256": record_sha,
    }


def cmd_review(args: argparse.Namespace, roots: list[Path]) -> int:
    """只追加一条 review 事实：不改写旧行，不改执行状态，不调用 restore，不碰配置和发布。"""
    sandbox = Sandbox(roots)
    phe = args.phe_home
    record, record_sha = read_review(args.record)
    row = build_review_row(read_ledger(phe), args.execution, record, record_sha, str(args.record))
    append_ledger(sandbox, phe, row)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def phe_roots(phe_home: Path) -> list[Path]:
    """review 只写账本，因此只对 --phe-home 做与 release 相同的真实环境守卫。"""
    reason = refusal_reason(phe_home)
    if reason is not None:
        raise GuardError(reason)
    return [Path(os.path.realpath(phe_home))]
