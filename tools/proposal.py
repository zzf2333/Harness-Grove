#!/usr/bin/env python3
"""M3a 的命令行入口：起草并只读检查经验候选提案（标准库）。

`draft` 只写本仓库 `experiments/<id>/proposal.json`；`check` 只读同一路径。仓库根由本文件的
源码位置推导，没有 `--root` 之类的绕过参数。候选结构与共用检查在 `tools/proposal_candidate.py`，
显式 Retired 输入与关系核对在 `tools/proposal_retired.py`；本模块只做模板装配、路径守卫和退出码。

`--retired` 在本轮仍是可选的：不带它时保留切片 A 的 `unavailable`。预测冻结与正式核验未实现，
`freeze_ready` 永远不是 true；报告只覆盖候选、Retired 结构和明显的未填项，不是完整协议验证。
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# 导入相邻模块前关掉字节码缓存：draft/check 不能在仓库副本里留下 __pycache__ 和 .pyc。
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from proposal_candidate import (
    DEFAULT_SOURCE_CHECK,
    DRAFT_SOURCE_CHECK,
    EXPERIMENTS_DIR,
    build_candidate,
    candidate_report,
    inspect_proposal,
    legacy_report,
    read_groups,
    read_host_candidate,
    select_groups,
)
from proposal_input import InputError, read_json_object
from proposal_retired import load_retired, unavailable_retired, verify_source

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "examples/m-1/proposal.template.json"
REFUSAL_EXIT = 2
INPUT_ERROR_EXIT = 1
RETIRED_NOT_PROVIDED_REASON = "未提供 --retired 文件，本工具不做自动检索或扫描"
NOT_APPLICABLE_SOURCE_CHECK = {
    "status": "not_performed",
    "reason": "该提案没有 candidate 段，未复核 Retired 来源",
    "proposal_updated": False,
}
EXPERIMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class Refusal(ValueError):
    """路径、覆盖或软链逃逸：退出 2，不写任何产物。"""


def load_template():
    template, _ = read_json_object(TEMPLATE_PATH, "proposal 模板")
    metrics = template.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise InputError(f"proposal 模板缺少 metrics 字典：{TEMPLATE_PATH}")
    return template


def build_proposal(template, experiment_id, candidate):
    """复用模板完整形状：只改实验身份与草稿状态，并追加 candidate 段。"""
    proposal = copy.deepcopy(template)
    proposal["experiment_id"] = experiment_id
    proposal["status"] = "draft"
    paths = proposal.get("paths")
    if not isinstance(paths, dict):
        raise InputError("proposal 模板缺少 paths 对象")
    paths["artifact_root_relative_to_phe_repo"] = f"{EXPERIMENTS_DIR}/{experiment_id}"
    proposal["candidate"] = candidate
    return proposal


def validate_experiment_id(experiment_id):
    if not EXPERIMENT_ID.fullmatch(experiment_id):
        raise Refusal(f"--experiment-id 只能是不含路径分隔符的新目录名（字母数字开头，允许 . _ -）：{experiment_id!r}")


def write_atomic(path, text):
    """同目录写完临时文件再替换：写到一半失败时不留半个文件。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ensure_experiments_dir(experiments):
    if os.path.islink(experiments):
        raise Refusal(f"{EXPERIMENTS_DIR} 是软链，拒绝写入：{experiments}")
    if os.path.lexists(experiments):
        if not experiments.is_dir():
            raise Refusal(f"{EXPERIMENTS_DIR} 不是目录，拒绝写入：{experiments}")
        return
    os.mkdir(experiments)


def write_new_experiment(experiment_id, proposal):
    """只创建新实验目录；已有目录（含冻结目录）不覆盖，写入失败时删掉本次新建的目录。"""
    experiments = REPO_ROOT / EXPERIMENTS_DIR
    ensure_experiments_dir(experiments)
    exp = experiments / experiment_id
    if os.path.lexists(exp):
        raise Refusal(f"实验目录已存在，不覆盖：{EXPERIMENTS_DIR}/{experiment_id}")
    for entry in os.scandir(experiments):
        if entry.name.casefold() == experiment_id.casefold():
            raise Refusal(f"实验目录已存在（大小写不同），不覆盖：{EXPERIMENTS_DIR}/{entry.name}")
    try:
        os.mkdir(exp)
    except FileExistsError as exc:
        raise Refusal(f"实验目录已存在，不覆盖：{EXPERIMENTS_DIR}/{experiment_id}") from exc
    try:
        if Path(os.path.realpath(exp)).parent != Path(os.path.realpath(experiments)):
            raise Refusal(f"实验目录越出 {EXPERIMENTS_DIR}：{exp}")
        document = json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
        write_atomic(exp / "proposal.json", document)
    except BaseException:
        shutil.rmtree(exp, ignore_errors=True)
        raise
    return exp


def draft(groups_path, candidate_path, retired_path, experiment_id):
    validate_experiment_id(experiment_id)
    template = load_template()
    metric_names = set(template["metrics"])
    selectors, judgment = read_host_candidate(candidate_path, metric_names)
    if judgment.get("retired_review") is not None and retired_path is None:
        raise InputError("Host 候选 JSON 带 retired_review 时必须同时提供 --retired：没有检索记录可引用")
    if retired_path is None:
        retired = unavailable_retired(RETIRED_NOT_PROVIDED_REASON)
    else:
        retired = load_retired(retired_path)
    groups, raw = read_groups(groups_path)
    selected = select_groups(groups, selectors)
    candidate = build_candidate(selected, hashlib.sha256(raw).hexdigest(), groups_path, judgment, retired)
    proposal = build_proposal(template, experiment_id, candidate)
    inspection = inspect_proposal(proposal, metric_names)
    if inspection is None:
        raise InputError("内部不变量被破坏：新提案缺少 candidate 段")
    write_new_experiment(experiment_id, proposal)
    report = candidate_report("draft", experiment_id, REPO_ROOT, inspection, DRAFT_SOURCE_CHECK)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    blockers = "、".join(inspection["blockers"])
    print(f"proposal: 已保存 {report['proposal_path']}；freeze_ready=false（{blockers}）", file=sys.stderr)
    return 0


def existing_experiment(experiment_id):
    experiments = REPO_ROOT / EXPERIMENTS_DIR
    if os.path.islink(experiments) or not experiments.is_dir():
        raise Refusal(f"{EXPERIMENTS_DIR} 不是可读目录：{experiments}")
    exp = experiments / experiment_id
    if os.path.islink(exp):
        raise Refusal(f"实验目录是软链，拒绝读取：{exp}")
    if not os.path.lexists(exp):
        raise InputError(f"实验目录不存在：{EXPERIMENTS_DIR}/{experiment_id}")
    if not exp.is_dir():
        raise InputError(f"实验路径不是目录：{EXPERIMENTS_DIR}/{experiment_id}")
    return exp


def check(experiment_id, retired_path):
    validate_experiment_id(experiment_id)
    exp = existing_experiment(experiment_id)
    proposal_path = exp / "proposal.json"
    if proposal_path.is_symlink():
        raise Refusal(f"proposal.json 是软链，拒绝读取：{proposal_path}")
    if not proposal_path.exists():
        raise InputError(f"proposal.json 不存在：{EXPERIMENTS_DIR}/{experiment_id}/proposal.json")
    if Path(os.path.realpath(proposal_path)).parent != Path(os.path.realpath(exp)):
        raise Refusal("proposal.json 越出实验目录，拒绝读取")
    proposal, _ = read_json_object(proposal_path, "proposal.json")
    # 白名单与 draft 同源，只取仓库模板：改草稿里的 metrics 不能放行未知指标。
    inspection = inspect_proposal(proposal, set(load_template()["metrics"]))
    if inspection is None:
        source_check = DEFAULT_SOURCE_CHECK if retired_path is None else NOT_APPLICABLE_SOURCE_CHECK
        report = legacy_report(experiment_id, REPO_ROOT, source_check)
        summary = "不在检查范围"
    else:
        source_check = (
            DEFAULT_SOURCE_CHECK if retired_path is None else verify_source(inspection["retired"], retired_path)
        )
        report = candidate_report("check", experiment_id, REPO_ROOT, inspection, source_check)
        summary = f"freeze_ready=false（{'、'.join(inspection['blockers'])}）"
        if source_check["status"] not in ("unchanged", "not_performed"):
            summary = f"{summary}；Retired 来源需重新检索（{source_check['status']}）"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"proposal: 已检查 {report['proposal_path']}；{summary}", file=sys.stderr)
    return 0 if source_check["status"] in ("unchanged", "not_performed") else INPUT_ERROR_EXIT


def main():
    parser = argparse.ArgumentParser(description="PHE proposal draft/check")
    commands = parser.add_subparsers(dest="cmd", required=True)
    draft_parser = commands.add_parser("draft", help="从分组 JSON 和 Host 候选 JSON 写新提案")
    draft_parser.add_argument("--groups", type=Path, required=True, help="tools/history.py group 的分组 JSON")
    draft_parser.add_argument("--candidate", type=Path, required=True, help="Host 手写的候选 JSON")
    draft_parser.add_argument("--retired", type=Path, help="Host 显式提供的 Retired 检索 JSON（可选）")
    draft_parser.add_argument("--experiment-id", required=True, help="新实验目录名")
    check_parser = commands.add_parser("check", help="只读检查带 candidate 的草稿")
    check_parser.add_argument("--experiment-id", required=True, help="已有实验目录名")
    check_parser.add_argument("--retired", type=Path, help="显式复核 Retired 来源与记录（可选，默认不回读）")
    args = parser.parse_args()
    try:
        if args.cmd == "draft":
            code = draft(args.groups, args.candidate, args.retired, args.experiment_id)
        else:
            code = check(args.experiment_id, args.retired)
    except Refusal as exc:
        print(f"proposal: {exc}", file=sys.stderr)
        code = REFUSAL_EXIT
    except InputError as exc:
        print(f"proposal: {exc}", file=sys.stderr)
        code = INPUT_ERROR_EXIT
    except OSError as exc:
        print(f"proposal: {exc}", file=sys.stderr)
        code = INPUT_ERROR_EXIT
    sys.exit(code)


if __name__ == "__main__":
    main()
