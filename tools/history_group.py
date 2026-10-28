"""M2 切片 B：把 Episode JSON 按 host + session_id + kind 做确定性结构分组（标准库）。

输入是 `tools/history.py collect` 产出的一份或多份 Episode JSON，输出
`{"groups": [...], "sources_unavailable": [...]}`。这里只做结构分组，不判断两条记录「是不是同一件事」
——那是 Host 的语义判断。

`correlation_group` 是 host、session_id、kind 的 sha256 前 12 位：同输入同输出。不同 Host 的同名
session_id 不合并；同会话里反复出现的同类信号算一组，不拆成多份独立证据。跨会话、同 project 同 kind
的组也不合并，只在 `same_project` 里互相列出组 id。

每组只留指针（`source_path` + `line`）、时间戳和已脱敏的 `excerpt`，不复制会话原文，也不重新拼接。
同一指针重复出现（例如同时传入 7 天和 30 天的产物）只计一次。`available: false` 的来源按
host、source_path、missing_reason 汇总进 `sources_unavailable`，不静默丢弃。

输出排序固定：组按 host、project、session_id、kind；组内指针按 source_path、line。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import REFUSAL_EXIT, parse_ts, refusal_reason, write_atomic

GROUP_ID_LENGTH = 12
# 组的输出顺序：同输入同输出，不依赖读入顺序。
GROUP_SORT_KEYS = ("host", "project", "session_id", "kind")
POINTER_FIELDS = ("source_path", "line", "ts", "excerpt")
UNAVAILABLE_FIELDS = ("source_path", "host", "missing_reason")
REQUIRED_TEXT_FIELDS = ("host", "session_id", "kind", "source_path")
OPTIONAL_TEXT_FIELDS = ("ts", "project")


def correlation_group(host, session_id, kind):
    """host + session_id + kind 的确定性组 id。key 用 JSON 数组拼，字段里出现分隔符也不会撞。"""
    key = json.dumps([host, session_id, kind], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:GROUP_ID_LENGTH]


def check_episode(path, index, episode):
    where = f"{path}: episodes[{index}]"
    if not isinstance(episode, dict):
        raise TypeError(f"{where} 不是对象")
    for field in REQUIRED_TEXT_FIELDS:
        if not isinstance(episode.get(field), str) or not episode[field]:
            raise TypeError(f"{where}.{field} 不是非空字符串")
    line = episode.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise TypeError(f"{where}.line 不是正整数")
    if not isinstance(episode.get("excerpt"), str):
        raise TypeError(f"{where}.excerpt 不是字符串")
    for field in OPTIONAL_TEXT_FIELDS:
        if episode.get(field) is not None and not isinstance(episode[field], str):
            raise TypeError(f"{where}.{field} 不是字符串或 null")


def check_source(path, index, source):
    """来源也要能进输出：available 决定走汇总还是被忽略，不可用来源必须能定位、能说明原因。"""
    where = f"{path}: sources[{index}]"
    if not isinstance(source, dict):
        raise TypeError(f"{where} 不是对象")
    if not isinstance(source.get("available"), bool):
        raise TypeError(f"{where}.available 不是布尔值")
    if source["available"]:
        return
    for field in UNAVAILABLE_FIELDS:
        if not isinstance(source.get(field), str) or not source[field]:
            raise TypeError(f"{where}.{field} 不是非空字符串")


def read_episode_file(path):
    """读一份 collect 产物，返回 (episodes, sources)。schema 不对时报错，不拿半份数据继续分组。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path}: 顶层不是对象")
    episodes = data.get("episodes")
    if not isinstance(episodes, list):
        raise TypeError(f"{path}: 缺少 episodes 列表")
    for index, episode in enumerate(episodes):
        check_episode(path, index, episode)
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise TypeError(f"{path}: sources 不是列表")
    for index, source in enumerate(sources):
        check_source(path, index, source)
    return episodes, sources


def add_episode(buckets, episode):
    host, session_id, kind = (episode[field] for field in ("host", "session_id", "kind"))
    group_id = correlation_group(host, session_id, kind)
    bucket = buckets.setdefault(group_id, {"host": host, "session_id": session_id, "kind": kind, "episodes": {}})
    # 同一指针重复出现只计一次：同一份证据不因为出现在多份产物里变成多条。
    bucket["episodes"].setdefault((episode["source_path"], episode["line"]), episode)


def finalize(group_id, bucket):
    """把一组内部状态定成输出形状：指针按 source_path、line 排序，首末 ts 取可解析时间戳的极值。"""
    ordered = [bucket["episodes"][key] for key in sorted(bucket["episodes"])]
    first_ts = last_ts = None
    earliest = latest = None
    for episode in ordered:
        moment = parse_ts(episode.get("ts"))
        if moment is None:
            continue
        # 时间戳相同的记录保留排序靠前的一条，输出不随读入顺序变化。
        if earliest is None or moment < earliest:
            earliest, first_ts = moment, episode.get("ts")
        if latest is None or moment > latest:
            latest, last_ts = moment, episode.get("ts")
    return {
        "correlation_group": group_id,
        "host": bucket["host"],
        "session_id": bucket["session_id"],
        # 同一会话的 project 一般不变；万一变了取排序后第一条非空值。
        "project": next((episode.get("project") for episode in ordered if episode.get("project")), ""),
        "kind": bucket["kind"],
        "count": len(ordered),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "episodes": [{field: episode.get(field) for field in POINTER_FIELDS} for episode in ordered],
    }


def build_groups(episodes):
    buckets = {}
    for episode in episodes:
        add_episode(buckets, episode)
    groups = [finalize(group_id, bucket) for group_id, bucket in buckets.items()]
    groups.sort(key=lambda group: tuple(group[key] for key in GROUP_SORT_KEYS))
    by_project = {}
    for group in groups:
        # project 未知（空串或 null）的组不进索引：未知项目不是同一个项目，不能互相关联。
        if group["project"]:
            by_project.setdefault((group["project"], group["kind"]), []).append(group["correlation_group"])
    for group in groups:
        # 同 project 同 kind 的其他组只做标记：是不是同一件事留给 Host 判断。
        related = by_project.get((group["project"], group["kind"]), []) if group["project"] else []
        group["same_project"] = sorted(other for other in related if other != group["correlation_group"])
    return groups


def unavailable_sources(sources):
    """available: false 的来源按 host、source_path、missing_reason 汇总；重复出现只留一条。"""
    entries = {}
    for source in sources:
        if source.get("available", True):
            continue
        entry = {field: source.get(field) for field in UNAVAILABLE_FIELDS}
        entries[(str(entry["host"]), str(entry["source_path"]), str(entry["missing_reason"]))] = entry
    return [entries[key] for key in sorted(entries)]


def same_file(left, right):
    """realpath 比较，大小写不敏感；两边都存在时再用 samefile，因为 realpath 不化解硬链接。"""
    if os.path.realpath(left).casefold() == os.path.realpath(right).casefold():
        return True
    return os.path.exists(left) and os.path.exists(right) and os.path.samefile(left, right)


def session_source_paths(episodes, sources):
    """参与分组和汇总的会话源路径：--out 不得覆盖它们，也不得落进它们所在目录。"""
    paths = []
    for entry in (*episodes, *sources):
        value = entry.get("source_path")
        if isinstance(value, str) and value:
            paths.append(value)
    return paths


def out_in_session_dir(out, source_paths):
    """`--out` 与会话源冲突时返回原因，否则 None。

    目录比较取 `realpath(--out)` 的父目录：`--out` 常指向还不存在的文件，经软链时也能解析到目标目录。
    """
    out_dir = os.path.dirname(os.path.realpath(out))
    for source_path in source_paths:
        if same_file(out, source_path):
            return f"--out {out} 与会话源 {source_path} 是同一个文件"
        if os.path.realpath(os.path.dirname(source_path)) == out_dir:
            return f"--out {out} 与会话源 {source_path} 在同一目录"
    return None


def group(episode_paths, out):
    """读多份 Episode JSON，写一份分组 JSON。返回退出码。"""
    reason = refusal_reason(out)
    if reason is not None:
        print(f"history: 拒绝写入：{reason}", file=sys.stderr)
        return REFUSAL_EXIT
    for path in episode_paths:
        if same_file(out, path):
            print(f"history: 拒绝写入：--out {out} 与输入 {path} 是同一个文件", file=sys.stderr)
            return REFUSAL_EXIT
    episodes, sources = [], []
    for path in episode_paths:
        found, seen = read_episode_file(path)
        episodes.extend(found)
        sources.extend(seen)
    # 输入读齐并校验后才比会话源路径：写到会话文件或会话目录里就是覆盖原始证据。
    reason = out_in_session_dir(out, session_source_paths(episodes, sources))
    if reason is not None:
        print(f"history: 拒绝写入：{reason}", file=sys.stderr)
        return REFUSAL_EXIT
    groups = build_groups(episodes)
    unavailable = unavailable_sources(sources)
    document = {"groups": groups, "sources_unavailable": unavailable}
    write_atomic(out, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(f"history: {len(groups)} 组，{len(unavailable)} 个来源不可用", file=sys.stderr)
    return 0
