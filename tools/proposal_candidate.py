"""M3a 切片 A 的候选段：从 M2 分组产物解析证据，规范化 Host 判断，并做 draft/check 共用检查。

这里只处理数据：选组、绑定指针、校验候选结构与报告缺项。写盘和路径守卫在
`tools/proposal.py`。证据只复制 `source_path`、`line`、`ts`，不复制 `excerpt`，也不读会话原文。

本切片没有 Retired 数据源与检索：`candidate.retired` 固定为 unavailable，拒绝 Host 声明已检索，
`freeze_ready` 永远不是 true。报告只覆盖候选段和模板区明显的 null 字段，不是完整协议验证。
"""

import json
import re

EXPERIMENTS_DIR = "experiments"
HASH = re.compile(r"^[0-9a-f]{64}$")
HYPOTHESIS_FIELDS = ("observation", "change", "expected_effect")
PREDICTION_FIELDS = ("expected_change", "falsified_when")
HOST_INPUT_KEYS = {"groups", "hypothesis", "predictions"}
CANDIDATE_KEYS = {"origin", "evidence", "host_judgment", "retired"}
EVIDENCE_KEYS = {"groups_file", "selected_group_ids", "groups"}
GROUPS_FILE_KEYS = {"path", "sha256"}
GROUP_KEYS = {"correlation_group", "host", "session_id", "project", "kind", "count", "pointers"}
POINTER_KEYS = {"source_path", "line", "ts"}
JUDGMENT_KEYS = {"source", "hypothesis", "predictions"}
RETIRED_KEYS = {"status", "reason"}

CANDIDATE_ORIGIN = "experience_scan"
HOST_JUDGMENT_SOURCE = "host_judgment_not_measurement"
RETIRED_STATUS = "unavailable"
RETIRED_REASON = "尚无Retired数据源及检索实现"
SCOPE = "candidate_evidence_and_obvious_unfilled_fields_only"
LEGACY_SCOPE = "legacy_proposal_out_of_scope"
NOTES = (
    "报告只覆盖候选段和明显的未填 null 字段，不是完整协议验证",
    "分组文件 sha256 只绑定输入字节，不验证语义质量、证据强度或预测是否合理",
    "hypothesis 与 predictions 是 Host 判断，不是实测",
    "本切片没有 Retired 数据源与检索，freeze_ready 永远不是 true",
)


class InputError(ValueError):
    """输入或结构不合法：调用方退出 1，不写任何产物。"""


def read_json_object(path, label):
    """读一个 UTF-8 JSON 对象，同时返回原始字节（分组文件的 sha256 绑定用）。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"{label} 读不到：{path}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{label} 不是合法 UTF-8 JSON：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"{label} 顶层不是对象：{path}")
    return data, raw


def check_keys(where, value, required, allowed=None):
    """字段集合校验：缺必填或出现未支持字段都拒绝，不猜。"""
    present = set(value)
    missing = sorted(required - present)
    if missing:
        raise InputError(f"{where} 缺少字段：{'、'.join(missing)}")
    unexpected = sorted(present - (required if allowed is None else allowed))
    if unexpected:
        raise InputError(f"{where} 含未支持字段：{'、'.join(unexpected)}")


def non_empty_string(where, value):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} 不是非空字符串")
    return value


def positive_int(where, value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InputError(f"{where} 不是正整数")
    return value


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
    return selectors, judgment


def build_candidate(selected, groups_sha256, groups_path, judgment):
    return {
        "origin": CANDIDATE_ORIGIN,
        "evidence": {
            "groups_file": {"path": str(groups_path), "sha256": groups_sha256},
            "selected_group_ids": [group["correlation_group"] for group in selected],
            "groups": selected,
        },
        "host_judgment": judgment,
        "retired": {"status": RETIRED_STATUS, "reason": RETIRED_REASON},
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
    digest = groups_file.get("sha256")
    if not isinstance(digest, str) or not HASH.fullmatch(digest):
        raise InputError("candidate.evidence.groups_file.sha256 不是小写十六进制 SHA-256")
    selectors = evidence.get("selected_group_ids")
    if not isinstance(selectors, list) or not selectors:
        raise InputError("candidate.evidence.selected_group_ids 不是非空数组")
    for index, selector in enumerate(selectors):
        non_empty_string(f"candidate.evidence.selected_group_ids[{index}]", selector)
    groups = evidence.get("groups")
    if not isinstance(groups, list) or not groups:
        raise InputError("candidate.evidence.groups 不是非空数组")
    group_ids = []
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
        pointer_count += len(pointers)
    if group_ids != selectors:
        raise InputError("candidate.evidence.selected_group_ids 与 groups 的顺序或内容不一致")
    return selectors, len(groups), pointer_count


def inspect_judgment(candidate, metric_names):
    judgment = candidate.get("host_judgment")
    if not isinstance(judgment, dict):
        raise InputError("candidate.host_judgment 不是对象")
    check_keys("candidate.host_judgment", judgment, JUDGMENT_KEYS)
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
    return missing


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
    selectors, group_count, pointer_count = inspect_evidence(candidate.get("evidence"))
    missing = inspect_judgment(candidate, metric_names)
    retired = candidate.get("retired")
    if not isinstance(retired, dict):
        raise InputError("candidate.retired 不是对象")
    check_keys("candidate.retired", retired, RETIRED_KEYS)
    if retired.get("status") != RETIRED_STATUS:
        raise InputError(
            "candidate.retired.status 只能是 unavailable：本切片没有 Retired 数据源和检索，不接受 Host 声明已检索"
        )
    reason = retired.get("reason")
    if not isinstance(reason, str):
        raise InputError("candidate.retired.reason 不是字符串")
    if not reason.strip():
        missing.append("candidate.retired.reason")

    unfilled = null_fields({key: value for key, value in proposal.items() if key != "candidate"})
    blockers = ["retired_unavailable"]
    if missing:
        blockers.append("candidate_incomplete")
    if unfilled:
        blockers.append("template_null_fields")
    return {
        "candidate_present": True,
        "missing": missing,
        "null_fields": unfilled,
        "blockers": blockers,
        "retired": retired,
        "evidence": {
            "groups_file": candidate["evidence"]["groups_file"],
            "selected_group_ids": selectors,
            "group_count": group_count,
            "pointer_count": pointer_count,
        },
    }


def candidate_report(action, experiment_id, root, inspection):
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
        "evidence": inspection["evidence"],
        "notes": list(NOTES),
    }
    if action == "draft":
        report["saved"] = True
    else:
        report["checked"] = True
    return report


def legacy_report(experiment_id, root):
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
        "evidence": None,
        "notes": [
            "该提案没有 candidate 段：本检查只适用于 M3a 候选草稿，不使旧提案失效",
            "未检查、未写入任何文件",
        ],
    }
