"""M3a 候选段：从 M2 分组产物解析证据，规范化 Host 判断，并做 draft/check 共用检查。

这里只处理数据：选组、绑定指针、校验候选结构、Retired 结构与 Host 判断的一致性，并报告缺项。
写盘和路径守卫在 `tools/proposal.py`；显式 Retired 输入在 `tools/proposal_retired.py`。证据只复制
`source_path`、`line`、`ts`，不复制 `excerpt`，也不读会话原文。

`freeze_ready` 永远不是 true：预测冻结与正式核验未实现。报告只覆盖候选段、Retired 结构和模板块明显的
null 字段，不是完整协议验证。
"""

import json

from proposal_input import (
    InputError,
    check_keys,
    non_empty_string,
    parse_timestamp,
    positive_int,
    read_json_object,
    sha256_hex,
)
from proposal_retired import (
    REVIEW_PATH,
    dedupe,
    inspect_retired,
    normalize_review,
)

EXPERIMENTS_DIR = "experiments"
HYPOTHESIS_FIELDS = ("observation", "change", "expected_effect")
PREDICTION_FIELDS = ("expected_change", "falsified_when")
HOST_INPUT_KEYS = {"groups", "hypothesis", "predictions", "retired_review"}
CANDIDATE_KEYS = {"origin", "evidence", "host_judgment", "retired"}
EVIDENCE_KEYS = {"groups_file", "selected_group_ids", "groups"}
GROUPS_FILE_KEYS = {"path", "sha256"}
GROUP_KEYS = {"correlation_group", "host", "session_id", "project", "kind", "count", "pointers"}
POINTER_KEYS = {"source_path", "line", "ts"}
JUDGMENT_KEYS = {"source", "hypothesis", "predictions"}
CANDIDATE_ORIGIN = "experience_scan"
HOST_JUDGMENT_SOURCE = "host_judgment_not_measurement"
BLOCK_PREDICTION_FREEZE = "prediction_freeze_not_implemented"
SCOPE = "candidate_evidence_and_obvious_unfilled_fields_only"
LEGACY_SCOPE = "legacy_proposal_out_of_scope"
NOTES = (
    "报告只覆盖候选段、Retired 结构和明显的未填 null 字段，不是完整协议验证",
    "Retired 输入是显式提供的有限文件，按整份逐条列举；不推断语义相关、不截断、不选 topK",
    "candidate.retired 的 completed 只表示该文件已完整列举；entries 为空只表示该文件为空，不是完整历史无退役",
    "unrelated / alternative / reopen 是 Host 判断，不是检索无命中的证据",
    "分组与 Retired 文件的 sha256 只绑定输入字节，不验证语义质量、证据独立性或预测是否合理",
    "预测冻结与正式核验未实现，freeze_ready 永远不是 true",
)
DRAFT_SOURCE_CHECK = {
    "status": "not_performed",
    "reason": "draft 只处理本次输入，不复核既有草稿",
    "proposal_updated": False,
}
DEFAULT_SOURCE_CHECK = {
    "status": "not_performed",
    "reason": "默认 check 不回读外部 Retired 文件，无法检测外部变化；需要复核时用 check --retired <file>",
    "proposal_updated": False,
}


def normalize_group(where, group):
    """只保留绑定证据需要的字段：不复制 excerpt，也不信任输入里的其他声明。"""
    if not isinstance(group, dict):
        raise InputError(f"{where} 不是对象")
    for field in ("correlation_group", "host", "session_id", "kind"):
        if field not in group:
            raise InputError(f"{where}.{field} 缺失")
    gid = non_empty_string(f"{where}.correlation_group", group["correlation_group"])
    host = non_empty_string(f"{where}.host", group["host"])
    session_id = non_empty_string(f"{where}.session_id", group["session_id"])
    kind = non_empty_string(f"{where}.kind", group["kind"])
    project = group.get("project", "")
    if project is None:
        project = ""
    if not isinstance(project, str):
        raise InputError(f"{where}.project 不是字符串")
    episodes = group.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise InputError(f"{where}.episodes 不是非空列表")
    pointers = []
    for index, episode in enumerate(episodes):
        pointer_where = f"{where}.episodes[{index}]"
        if not isinstance(episode, dict):
            raise InputError(f"{pointer_where} 不是对象")
        source_path = non_empty_string(f"{pointer_where}.source_path", episode.get("source_path"))
        line = positive_int(f"{pointer_where}.line", episode.get("line"))
        ts = episode.get("ts")
        if ts is not None and not isinstance(ts, str):
            raise InputError(f"{pointer_where}.ts 不是字符串或 null")
        pointers.append({"source_path": source_path, "line": line, "ts": ts})
    return {
        "correlation_group": gid,
        "host": host,
        "session_id": session_id,
        "project": project,
        "kind": kind,
        "count": len(pointers),
        "pointers": pointers,
    }


def read_groups(path):
    data, raw = read_json_object(path, "分组 JSON")
    groups = data.get("groups")
    if not isinstance(groups, list):
        raise InputError(f"分组 JSON 缺少 groups 列表：{path}")
    return [normalize_group(f"{path}: groups[{index}]", group) for index, group in enumerate(groups)], raw


def select_groups(groups, selectors):
    """按 correlation_group 精确选组。同一 ID 对应多份不同内容时拒绝，不挑其中一份。"""
    by_id = {}
    for group in groups:
        by_id.setdefault(group["correlation_group"], []).append(group)
    selected, seen = [], set()
    for selector in selectors:
        if selector in seen:
            continue
        matches = by_id.get(selector)
        if not matches:
            raise InputError(f"选中的组不存在：{selector}")
        distinct = {}
        for group in matches:
            distinct.setdefault(json.dumps(group, ensure_ascii=False, sort_keys=True), group)
        if len(distinct) > 1:
            raise InputError(f"选中的组 ID 歧义（同一 ID 对应 {len(distinct)} 份不同内容）：{selector}")
        seen.add(selector)
        selected.append(next(iter(distinct.values())))
    return selected


def build_host_judgment(hypothesis, predictions, metric_names):
    """规范化 Host 判断：缺项记为 null 并报告，畸形类型直接拒绝。"""
    if hypothesis is None:
        hypothesis = {}
    if not isinstance(hypothesis, dict):
        raise InputError("hypothesis 不是对象")
    check_keys("hypothesis", hypothesis, set(), set(HYPOTHESIS_FIELDS))
    normalized_hypothesis: dict[str, str | None] = {}
    for field in HYPOTHESIS_FIELDS:
        value = hypothesis.get(field)
        if value is None:
            normalized_hypothesis[field] = None
        elif not isinstance(value, str):
            raise InputError(f"hypothesis.{field} 不是字符串")
        else:
            normalized_hypothesis[field] = value.strip() or None
    if predictions is None:
        predictions = []
    if not isinstance(predictions, list):
        raise InputError("predictions 不是数组")
    normalized_predictions: list[dict[str, str | None]] = []
    for index, prediction in enumerate(predictions):
        where = f"predictions[{index}]"
        if not isinstance(prediction, dict):
            raise InputError(f"{where} 不是对象")
        check_keys(where, prediction, {"metric"}, {"metric", *PREDICTION_FIELDS})
        metric = non_empty_string(f"{where}.metric", prediction.get("metric"))
        if metric not in metric_names:
            raise InputError(f"{where}.metric 未知：{metric}；只能用模板 metrics 已有键")
        item: dict[str, str | None] = {"metric": metric}
        for field in PREDICTION_FIELDS:
            value = prediction.get(field)
            if value is None:
                item[field] = None
            elif not isinstance(value, str):
                raise InputError(f"{where}.{field} 不是字符串")
            else:
                item[field] = value.strip() or None
        normalized_predictions.append(item)
    return {
        "source": HOST_JUDGMENT_SOURCE,
        "hypothesis": normalized_hypothesis,
        "predictions": normalized_predictions,
    }


def read_host_candidate(path, metric_names):
    data, _ = read_json_object(path, "Host 候选 JSON")
    check_keys("Host 候选 JSON", data, {"groups"}, HOST_INPUT_KEYS)
    selectors = data.get("groups")
    if not isinstance(selectors, list) or not selectors:
        raise InputError("Host 候选 JSON 的 groups 必须是非空数组（显式选中的 group ID）")
    for index, selector in enumerate(selectors):
        non_empty_string(f"groups[{index}]", selector)
    judgment = build_host_judgment(data.get("hypothesis"), data.get("predictions"), metric_names)
    if "retired_review" in data:
        judgment["retired_review"] = normalize_review("retired_review", data["retired_review"])
    return selectors, judgment


def build_candidate(selected, groups_sha256, groups_path, judgment, retired):
    return {
        "origin": CANDIDATE_ORIGIN,
        "evidence": {
            "groups_file": {"path": str(groups_path), "sha256": groups_sha256},
            "selected_group_ids": [group["correlation_group"] for group in selected],
            "groups": selected,
        },
        "host_judgment": judgment,
        "retired": retired,
    }


def null_fields(value, prefix=""):
    """递归列出 null 字段路径；candidate 段由 missing 单独报告，不在这里重复。"""
    if value is None:
        return [prefix or "<root>"]
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(null_fields(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(null_fields(item, f"{prefix}[{index}]"))
    return found


def inspect_evidence(evidence):
    if not isinstance(evidence, dict):
        raise InputError("candidate.evidence 不是对象")
    check_keys("candidate.evidence", evidence, EVIDENCE_KEYS)
    groups_file = evidence.get("groups_file")
    if not isinstance(groups_file, dict):
        raise InputError("candidate.evidence.groups_file 不是对象")
    check_keys("candidate.evidence.groups_file", groups_file, GROUPS_FILE_KEYS)
    non_empty_string("candidate.evidence.groups_file.path", groups_file.get("path"))
    sha256_hex("candidate.evidence.groups_file.sha256", groups_file.get("sha256"))
    selectors = evidence.get("selected_group_ids")
    if not isinstance(selectors, list) or not selectors:
        raise InputError("candidate.evidence.selected_group_ids 不是非空数组")
    for index, selector in enumerate(selectors):
        non_empty_string(f"candidate.evidence.selected_group_ids[{index}]", selector)
    groups = evidence.get("groups")
    if not isinstance(groups, list) or not groups:
        raise InputError("candidate.evidence.groups 不是非空数组")
    group_ids = []
    group_times = {}
    pointer_count = 0
    for index, group in enumerate(groups):
        where = f"candidate.evidence.groups[{index}]"
        if not isinstance(group, dict):
            raise InputError(f"{where} 不是对象")
        check_keys(where, group, GROUP_KEYS)
        group_id = non_empty_string(f"{where}.correlation_group", group.get("correlation_group"))
        if group_id in group_ids:
            raise InputError(f"{where}.correlation_group 重复：{group_id}；选中组只能出现一次")
        group_ids.append(group_id)
        for field in ("host", "session_id", "kind"):
            non_empty_string(f"{where}.{field}", group.get(field))
        if not isinstance(group.get("project"), str):
            raise InputError(f"{where}.project 不是字符串")
        count = positive_int(f"{where}.count", group.get("count"))
        pointers = group.get("pointers")
        if not isinstance(pointers, list) or not pointers:
            raise InputError(f"{where}.pointers 不是非空数组")
        if count != len(pointers):
            raise InputError(f"{where}.count 与 pointers 数量不一致：{count} != {len(pointers)}")
        for pointer_index, pointer in enumerate(pointers):
            pointer_where = f"{where}.pointers[{pointer_index}]"
            if not isinstance(pointer, dict):
                raise InputError(f"{pointer_where} 不是对象")
            check_keys(pointer_where, pointer, POINTER_KEYS)
            non_empty_string(f"{pointer_where}.source_path", pointer.get("source_path"))
            positive_int(f"{pointer_where}.line", pointer.get("line"))
            ts = pointer.get("ts")
            if ts is not None and not isinstance(ts, str):
                raise InputError(f"{pointer_where}.ts 不是字符串或 null")
        group_times[group_id] = group_earliest_time(where, pointers)
        pointer_count += len(pointers)
    if group_ids != selectors:
        raise InputError("candidate.evidence.selected_group_ids 与 groups 的顺序或内容不一致")
    return selectors, len(groups), pointer_count, group_times


def group_earliest_time(where, pointers):
    """分组里每个指针都带可解析时间时取最早一条；任一未知就返回 None，不猜。"""
    times = []
    for index, pointer in enumerate(pointers):
        value = pointer.get("ts")
        if value is None:
            return None
        try:
            times.append(parse_timestamp(f"{where}.pointers[{index}].ts", value))
        except InputError:
            return None
    return min(times)


def inspect_judgment(candidate, metric_names):
    judgment = candidate.get("host_judgment")
    if not isinstance(judgment, dict):
        raise InputError("candidate.host_judgment 不是对象")
    check_keys("candidate.host_judgment", judgment, JUDGMENT_KEYS, {*JUDGMENT_KEYS, "retired_review"})
    missing = []
    if judgment.get("source") != HOST_JUDGMENT_SOURCE:
        missing.append("candidate.host_judgment.source")
    hypothesis = judgment.get("hypothesis")
    if not isinstance(hypothesis, dict):
        raise InputError("candidate.host_judgment.hypothesis 不是对象")
    check_keys("candidate.host_judgment.hypothesis", hypothesis, set(), set(HYPOTHESIS_FIELDS))
    for field in HYPOTHESIS_FIELDS:
        where = f"candidate.host_judgment.hypothesis.{field}"
        value = hypothesis.get(field)
        if value is None:
            missing.append(where)
        elif not isinstance(value, str):
            raise InputError(f"{where} 不是字符串或 null")
        elif not value.strip():
            missing.append(where)
    predictions = judgment.get("predictions")
    if not isinstance(predictions, list):
        raise InputError("candidate.host_judgment.predictions 不是数组")
    if not predictions:
        missing.append("candidate.host_judgment.predictions")
    for index, prediction in enumerate(predictions):
        where = f"candidate.host_judgment.predictions[{index}]"
        if not isinstance(prediction, dict):
            raise InputError(f"{where} 不是对象")
        check_keys(where, prediction, {"metric"}, {"metric", *PREDICTION_FIELDS})
        metric = non_empty_string(f"{where}.metric", prediction.get("metric"))
        if metric not in metric_names:
            raise InputError(f"{where}.metric 未知：{metric}")
        for field in PREDICTION_FIELDS:
            field_where = f"{where}.{field}"
            value = prediction.get(field)
            if value is None:
                missing.append(field_where)
            elif not isinstance(value, str):
                raise InputError(f"{field_where} 不是字符串或 null")
            elif not value.strip():
                missing.append(field_where)
    review = judgment.get("retired_review")
    if review is not None:
        review = normalize_review(REVIEW_PATH, review)
    return missing, review


def inspect_proposal(proposal, metric_names):
    """draft 和 check 共用的候选检查。没有 candidate 段的旧提案返回 None。

    `metric_names` 必须来自仓库可信模板：草稿自己的 `metrics` 被改过也不能放行未知指标。
    """
    if "candidate" not in proposal:
        return None
    candidate = proposal.get("candidate")
    if not isinstance(candidate, dict):
        raise InputError("candidate 不是对象")
    check_keys("candidate", candidate, CANDIDATE_KEYS)
    if candidate.get("origin") != CANDIDATE_ORIGIN:
        raise InputError(f"candidate.origin 只能是 {CANDIDATE_ORIGIN}")
    metrics = proposal.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise InputError("proposal 缺少 metrics 字典")
    selectors, group_count, pointer_count, group_times = inspect_evidence(candidate.get("evidence"))
    missing, review = inspect_judgment(candidate, metric_names)
    retired = inspect_retired(candidate.get("retired"), review, selectors, group_times)
    missing.extend(retired["missing"])

    unfilled = null_fields({key: value for key, value in proposal.items() if key != "candidate"})
    blockers = dedupe([*retired["blockers"], BLOCK_PREDICTION_FREEZE])
    if missing:
        blockers.append("candidate_incomplete")
    if unfilled:
        blockers.append("template_null_fields")
    return {
        "candidate_present": True,
        "missing": missing,
        "null_fields": unfilled,
        "blockers": blockers,
        "retired": retired["retired"],
        "retired_review": retired["review"],
        "retired_findings": retired["findings"],
        "evidence": {
            "groups_file": candidate["evidence"]["groups_file"],
            "selected_group_ids": selectors,
            "group_count": group_count,
            "pointer_count": pointer_count,
        },
    }


def candidate_report(action, experiment_id, root, inspection, source_check):
    report = {
        "action": action,
        "scope": SCOPE,
        "repo_root": str(root),
        "experiment_id": experiment_id,
        "proposal_path": f"{EXPERIMENTS_DIR}/{experiment_id}/proposal.json",
        "candidate_present": True,
        "freeze_ready": False,
        "freeze_ready_evaluated": True,
        "freeze_ready_blockers": inspection["blockers"],
        "missing": inspection["missing"],
        "null_fields": inspection["null_fields"],
        "retired": inspection["retired"],
        "retired_review": inspection["retired_review"],
        "retired_findings": inspection["retired_findings"],
        "retired_source_check": source_check,
        "evidence": inspection["evidence"],
        "notes": list(NOTES),
    }
    if action == "draft":
        report["saved"] = True
    else:
        report["checked"] = True
    return report


def legacy_report(experiment_id, root, source_check):
    return {
        "action": "check",
        "scope": LEGACY_SCOPE,
        "repo_root": str(root),
        "experiment_id": experiment_id,
        "proposal_path": f"{EXPERIMENTS_DIR}/{experiment_id}/proposal.json",
        "candidate_present": False,
        "checked": True,
        "freeze_ready": None,
        "freeze_ready_evaluated": False,
        "freeze_ready_blockers": [],
        "missing": [],
        "null_fields": [],
        "retired": None,
        "retired_review": None,
        "retired_findings": [],
        "retired_source_check": source_check,
        "evidence": None,
        "notes": [
            "该提案没有 candidate 段：本检查只适用于 M3a 候选草稿，不使旧提案失效",
            "未检查、未写入任何文件",
        ],
    }
