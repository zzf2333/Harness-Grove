"""M3a 切片 B1 的显式 Retired 检索输入、Host 关系核对与来源复核。

输入是 Host 显式给出的一个有限 JSON 文件：工具按文件顺序逐条列举**全部** `entries`，不推断语义
相关、不排序、不截断、不选 topK，也不扫描用户目录或真实退役记录。`unrelated` / `alternative` /
`reopen` 是 Host 判断，不是检索结果；来源 sha256 只绑定原始字节，不验证内容是否真实、完整或
语义独立。这里不写盘：写路径和路径守卫在 `tools/proposal.py`。
"""

import hashlib
import json
from pathlib import Path

from proposal_input import InputError, check_keys, non_empty_string, parse_timestamp, sha256_hex

SCHEMA_VERSION = 1
RETRIEVAL_SCOPE = "all_entries_in_supplied_file"
STATUS_COMPLETED = "completed"
STATUS_UNAVAILABLE = "unavailable"
UNRELATED = "unrelated"
ALTERNATIVE = "alternative"
REOPEN = "reopen"
RELATIONS = (UNRELATED, ALTERNATIVE, REOPEN)
REVIEW_PATH = "candidate.host_judgment.retired_review"

SOURCE_KEYS = {"schema_version", "scope", "entries"}
ENTRY_KEYS = {"retired_id", "method", "rejected_reason", "retired_at", "evidence_groups"}
STORED_UNAVAILABLE_KEYS = {"status", "reason"}
STORED_COMPLETED_KEYS = {"status", "source", "scope", "supplied_scope", "entries"}
STORED_SOURCE_KEYS = {"path", "sha256"}
REVIEW_KEYS = {"relation", "reason", "entries"}
REVIEW_ENTRY_KEYS = {"retired_id", "relation", "reason", "new_evidence_groups"}

BLOCK_REVIEW_INCOMPLETE = "retired_review_incomplete"
BLOCK_NEW_EVIDENCE_REQUIRED = "retired_new_evidence_required"
BLOCK_GROUP_REUSED = "retired_evidence_group_reused"
BLOCK_TIME_UNKNOWN = "retired_evidence_time_unknown"
BLOCK_NOT_AFTER_RETIREMENT = "retired_evidence_not_after_retirement"


def unavailable_retired(reason):
    return {"status": STATUS_UNAVAILABLE, "reason": reason}


def normalize_entry(where, entry):
    """逐条规范化：只保留 schema 字段，不信任其他声明。"""
    if not isinstance(entry, dict):
        raise InputError(f"{where} 不是对象")
    check_keys(where, entry, ENTRY_KEYS)
    retired_id = non_empty_string(f"{where}.retired_id", entry.get("retired_id"))
    method = non_empty_string(f"{where}.method", entry.get("method"))
    rejected_reason = non_empty_string(f"{where}.rejected_reason", entry.get("rejected_reason"))
    parse_timestamp(f"{where}.retired_at", entry.get("retired_at"))
    groups = entry.get("evidence_groups")
    if not isinstance(groups, list):
        raise InputError(f"{where}.evidence_groups 不是数组")
    for index, group_id in enumerate(groups):
        non_empty_string(f"{where}.evidence_groups[{index}]", group_id)
    if len(set(groups)) != len(groups):
        raise InputError(f"{where}.evidence_groups 含重复 group ID")
    return {
        "retired_id": retired_id,
        "method": method,
        "rejected_reason": rejected_reason,
        "retired_at": entry["retired_at"].strip(),
        "evidence_groups": list(groups),
    }


def normalize_entries(where, entries):
    if not isinstance(entries, list):
        raise InputError(f"{where} 不是数组")
    normalized, seen = [], set()
    for index, entry in enumerate(entries):
        item = normalize_entry(f"{where}[{index}]", entry)
        if item["retired_id"] in seen:
            raise InputError(f"{where}[{index}].retired_id 重复：{item['retired_id']}")
        seen.add(item["retired_id"])
        normalized.append(item)
    return normalized


def load_retired(path) -> dict:
    """读取显式 Retired 检索文件：读不到时返回 unavailable，内容不合法时拒绝。

    读不到（缺失、目录、无权限）必须保存为 unavailable 并带原因，不能冒充“检索无命中”。
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return unavailable_retired(f"Retired 检索文件读不到：{path}: {exc}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Retired 检索文件不是合法 UTF-8 JSON：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"Retired 检索文件顶层不是对象：{path}")
    check_keys("Retired 检索文件", data, SOURCE_KEYS)
    version = data.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise InputError(f"Retired 检索文件 schema_version 只能是整数 {SCHEMA_VERSION}")
    supplied_scope = non_empty_string("Retired 检索文件.scope", data.get("scope"))
    entries = normalize_entries("Retired 检索文件.entries", data.get("entries"))
    return {
        "status": STATUS_COMPLETED,
        "source": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
        "scope": RETRIEVAL_SCOPE,
        "supplied_scope": supplied_scope,
        "entries": entries,
    }


def optional_relation(where, value):
    if value is None:
        return None
    if not isinstance(value, str) or value not in RELATIONS:
        raise InputError(f"{where} 只能是 {' / '.join(RELATIONS)}，不接受 {value!r}")
    return value


def optional_text(where, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError(f"{where} 不是字符串")
    return value.strip() or None


def normalize_review(where, review):
    """规范化 Host 关系判断。关系与理由是 Host 判断，缺项保留 None 交由报告列为缺项。"""
    if not isinstance(review, dict):
        raise InputError(f"{where} 不是对象")
    check_keys(where, review, set(), REVIEW_KEYS)
    entries = review.get("entries", [])
    if not isinstance(entries, list):
        raise InputError(f"{where}.entries 不是数组")
    normalized, seen = [], set()
    for index, entry in enumerate(entries):
        item_where = f"{where}.entries[{index}]"
        if not isinstance(entry, dict):
            raise InputError(f"{item_where} 不是对象")
        check_keys(item_where, entry, {"retired_id"}, REVIEW_ENTRY_KEYS)
        retired_id = non_empty_string(f"{item_where}.retired_id", entry.get("retired_id"))
        if retired_id in seen:
            raise InputError(f"{item_where}.retired_id 重复：{retired_id}")
        seen.add(retired_id)
        groups = entry.get("new_evidence_groups", [])
        if not isinstance(groups, list):
            raise InputError(f"{item_where}.new_evidence_groups 不是数组")
        for group_index, group_id in enumerate(groups):
            non_empty_string(f"{item_where}.new_evidence_groups[{group_index}]", group_id)
        normalized.append(
            {
                "retired_id": retired_id,
                "relation": optional_relation(f"{item_where}.relation", entry.get("relation")),
                "reason": optional_text(f"{item_where}.reason", entry.get("reason")),
                "new_evidence_groups": list(groups),
            }
        )
    return {
        "relation": optional_relation(f"{where}.relation", review.get("relation")),
        "reason": optional_text(f"{where}.reason", review.get("reason")),
        "entries": normalized,
    }


def review_missing(review):
    missing = []
    if review["relation"] is None:
        missing.append(f"{REVIEW_PATH}.relation")
    if review["reason"] is None:
        missing.append(f"{REVIEW_PATH}.reason")
    for item in review["entries"]:
        prefix = f"{REVIEW_PATH}.entries[{item['retired_id']}]"
        if item["relation"] is None:
            missing.append(f"{prefix}.relation")
        if item["reason"] is None:
            missing.append(f"{prefix}.reason")
    return missing


def inspect_retired(retired, review, selected_group_ids, group_times):
    """校验存储的 Retired 结构与 Host 判断的一致性，返回报告片段；不读外部文件。

    只检查必要结构和时序：不判断新证据是否语义独立或充分，也不接受手动恢复授权字段。
    """
    if not isinstance(retired, dict):
        raise InputError("candidate.retired 不是对象")
    status = retired.get("status")
    if status == STATUS_UNAVAILABLE:
        return inspect_unavailable(retired, review)
    if status != STATUS_COMPLETED:
        raise InputError("candidate.retired.status 只能是 unavailable 或 completed")
    return inspect_completed(retired, review, selected_group_ids, group_times)


def inspect_unavailable(retired, review):
    check_keys("candidate.retired", retired, STORED_UNAVAILABLE_KEYS)
    reason = retired.get("reason")
    if not isinstance(reason, str):
        raise InputError("candidate.retired.reason 不是字符串")
    if review is not None:
        raise InputError("candidate.retired.status 为 unavailable 时不接收 retired_review：没有可引用的检索记录")
    # 旧 unavailable 草稿照常列阻塞，不迁移、不重写。
    return {
        "retired": retired,
        "review": None,
        "missing": [] if reason.strip() else ["candidate.retired.reason"],
        "blockers": ["retired_unavailable"],
        "findings": [],
    }


def inspect_completed(retired, review, selected_group_ids, group_times):
    check_keys("candidate.retired", retired, STORED_COMPLETED_KEYS)
    source = retired.get("source")
    if not isinstance(source, dict):
        raise InputError("candidate.retired.source 不是对象")
    check_keys("candidate.retired.source", source, STORED_SOURCE_KEYS)
    non_empty_string("candidate.retired.source.path", source.get("path"))
    sha256_hex("candidate.retired.source.sha256", source.get("sha256"))
    if retired.get("scope") != RETRIEVAL_SCOPE:
        raise InputError(f"candidate.retired.scope 只能是 {RETRIEVAL_SCOPE}")
    non_empty_string("candidate.retired.supplied_scope", retired.get("supplied_scope"))
    entries = normalize_entries("candidate.retired.entries", retired.get("entries"))
    known_ids = [entry["retired_id"] for entry in entries]
    blockers, findings = [], []

    if review is None:
        missing = [f"{REVIEW_PATH}.relation", f"{REVIEW_PATH}.reason"]
        missing.extend(f"{REVIEW_PATH}.entries[{retired_id}]" for retired_id in known_ids)
        blockers.append(BLOCK_REVIEW_INCOMPLETE)
        return {"retired": retired, "review": None, "missing": missing, "blockers": blockers, "findings": findings}

    missing = []
    reviewed = {}
    for item in review["entries"]:
        retired_id = item["retired_id"]
        if retired_id not in known_ids:
            raise InputError(f"{REVIEW_PATH}.entries[{retired_id}] 引用了不存在的 retired_id")
        reviewed[retired_id] = item
        for group_id in item["new_evidence_groups"]:
            if group_id not in selected_group_ids:
                raise InputError(
                    f"{REVIEW_PATH}.entries[{retired_id}].new_evidence_groups 引用了当前候选未选中的分组：{group_id}"
                )
    if not entries and review["relation"] not in (None, UNRELATED):
        raise InputError(f"{REVIEW_PATH}.relation 只能是 unrelated：这次检索没有任何记录可关联")
    if review["relation"] == UNRELATED and any(item["relation"] not in (None, UNRELATED) for item in review["entries"]):
        raise InputError(f"{REVIEW_PATH}.relation 为 unrelated 时，逐条关系不能是 {ALTERNATIVE} 或 {REOPEN}")

    missing.extend(review_missing(review))
    missing.extend(f"{REVIEW_PATH}.entries[{retired_id}]" for retired_id in known_ids if retired_id not in reviewed)
    if missing:
        blockers.append(BLOCK_REVIEW_INCOMPLETE)

    for entry in entries:
        item = reviewed.get(entry["retired_id"])
        if item is None or item["relation"] not in (ALTERNATIVE, REOPEN):
            continue
        retired_id = entry["retired_id"]
        retired_at = parse_timestamp(f"candidate.retired.entries[{retired_id}].retired_at", entry["retired_at"])
        if not item["new_evidence_groups"]:
            blockers.append(BLOCK_NEW_EVIDENCE_REQUIRED)
            findings.append(f"{retired_id}: 声明 {item['relation']} 但没有给出 new_evidence_groups")
            continue
        for group_id in item["new_evidence_groups"]:
            if group_id in entry["evidence_groups"]:
                blockers.append(BLOCK_GROUP_REUSED)
                findings.append(f"{retired_id}: 引用的分组 {group_id} 已在退役时的证据分组里")
            group_time = group_times.get(group_id)
            if group_time is None:
                blockers.append(BLOCK_TIME_UNKNOWN)
                findings.append(f"{retired_id}: 引用的分组 {group_id} 没有可解析的时间证据")
            elif not group_time > retired_at:
                blockers.append(BLOCK_NOT_AFTER_RETIREMENT)
                findings.append(f"{retired_id}: 引用的分组 {group_id} 不晚于 retired_at {entry['retired_at']}")
    return {
        "retired": retired,
        "review": review,
        "missing": missing,
        "blockers": dedupe(blockers),
        "findings": findings,
    }


def dedupe(values):
    seen, ordered = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def verify_source(retired, path):
    """显式复核草稿的 Retired 来源：只报告，不更新 proposal。"""
    if retired.get("status") != STATUS_COMPLETED:
        return {
            "status": "not_performed",
            "reason": "草稿没有 Retired 检索记录（status=unavailable），无需复核",
            "proposal_updated": False,
        }
    try:
        current = load_retired(path)
    except InputError as exc:
        return {
            "status": STATUS_UNAVAILABLE,
            "reason": str(exc),
            "action_required": "re_search_required",
            "proposal_updated": False,
        }
    if current["status"] != STATUS_COMPLETED:
        return {
            "status": STATUS_UNAVAILABLE,
            "reason": current["reason"],
            "action_required": "re_search_required",
            "proposal_updated": False,
        }
    stored_source = retired["source"]
    if current["source"]["sha256"] != stored_source["sha256"]:
        return {
            "status": "changed",
            "reason": "Retired 来源字节与草稿绑定的 sha256 不一致",
            "checked_path": str(path),
            "stored_sha256": stored_source["sha256"],
            "current_sha256": current["source"]["sha256"],
            "action_required": "re_search_required",
            "proposal_updated": False,
        }
    if current["entries"] != retired["entries"] or current["supplied_scope"] != retired["supplied_scope"]:
        return {
            "status": "changed",
            "reason": "来源 sha256 相同，但草稿里的 Retired 记录或范围已被编辑",
            "checked_path": str(path),
            "action_required": "re_search_required",
            "proposal_updated": False,
        }
    return {
        "status": "unchanged",
        "checked_path": str(path),
        "source": stored_source,
        "entry_count": len(retired["entries"]),
        "proposal_updated": False,
    }
