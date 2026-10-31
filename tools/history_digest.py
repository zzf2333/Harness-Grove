#!/usr/bin/env python3
"""闭环切片 1：把最近的真实会话整理成脱敏 Markdown 摘要，供诊断子代理阅读（标准库）。

产物只有两种：`<host>-<n>.md`（约 40KB 一块，会话不跨块）和 `<host>-index.json`（时间窗、会话
计数、块清单、不可用来源）。每条正文用 `L<行号>` 指向源 JSONL 的物理行（从 1 开始，空行也占一
行），节标题给出源文件绝对路径；两者配合才能回到原文。

只列真人用户发言、工具失败和一行 retry 标注，不含助手输出；命中纠正词的用户消息也只占一行
（`user_correction` 与它指向同一行）。自动注入的上下文、技能展开、压缩摘要
不算用户发言（判断沿用 `tools/history_hosts.py`）；Pi 子代理目录沿用默认排除。没有真人发言的会话整节跳过，只在
索引里计数。整份来源不可用时与 collect 同义（读不到、有坏行、schema 不支持），不进摘要，只列进
索引。摘录先脱敏再截断，产物按私人数据处理，写在被 Git 忽略的 `episodes/`。

`L<行号>` 只在源文件没被改写时可信：会话文件是追加写的，追加不影响已有行号，但行号无法自证内容没
被替换。源文件 `sha256` 记在 collect 产物里，本摘要不记录它。
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import (
    DEFAULT_EXCLUDES,
    REFUSAL_EXIT,
    USER_MESSAGE_KIND,
    excerpt,
    iter_sources,
    out_inside_root,
    parse_since,
    parse_ts,
    refusal_reason,
    scan_source,
    write_atomic,
)

# 一块摘要的目标大小。会话不跨块，单节超长时独占一块，所以单块可能超过这个值。
BLOCK_TARGET_BYTES = 40 * 1024
USER_MESSAGE_LIMIT = 300
TOOL_FAILURE_LIMIT = 200
ENTRY_LABELS = {USER_MESSAGE_KIND: "用户", "tool_failure": "失败", "retry": "重试"}


def session_section(host, source, project, rows):
    """一个会话一节 Markdown：标题给会话身份，正文按时间顺序列信号，每条带 `L<行号>` 指针。"""
    user_messages = sum(1 for row in rows for kind, _text in row["entries"] if kind == USER_MESSAGE_KIND)
    tool_failures = sum(1 for row in rows for kind, _text in row["entries"] if kind == "tool_failure")
    tool_calls = sum(row["tool_calls"] for row in rows)
    moments = [moment for moment in (parse_ts(row["ts"]) for row in rows) if moment is not None]
    first = min(moments).isoformat() if moments else "unknown"
    last = max(moments).isoformat() if moments else "unknown"
    lines = [
        f"## {source['session_id']} · {host} · {project or 'unknown project'}",
        "",
        f"- 源文件：`{source['source_path']}`",
        f"- 时间：{first} → {last}",
        f"- 统计：用户消息 {user_messages}；工具调用 {tool_calls}；工具失败 {tool_failures}",
        "",
    ]
    for row in sorted(
        rows, key=lambda row: (parse_ts(row["ts"]) or datetime.min.replace(tzinfo=timezone.utc), row["line"])
    ):
        for kind, text in row["entries"]:
            label = ENTRY_LABELS.get(kind)
            if label is None:
                # user_correction 与同一行的用户消息重复，摘要只列一次。
                continue
            limit = USER_MESSAGE_LIMIT if kind == USER_MESSAGE_KIND else TOOL_FAILURE_LIMIT
            lines.append(f"- L{row['line']} {label}：{excerpt(text, limit)}")
    return "\n".join(lines) + "\n\n"


def split_blocks(sections):
    """按约 40KB 分块，会话不跨块；单节超过目标也独占一块。"""
    blocks, current, size = [], [], 0
    for section in sections:
        length = len(section.encode("utf-8"))
        if current and size + length > BLOCK_TARGET_BYTES:
            blocks.append(current)
            current, size = [], 0
        current.append(section)
        size += length
    if current:
        blocks.append(current)
    return blocks


def block_markdown(host, index, total, window_text, sections):
    """块的头部自述身份，让任何一块都能单独读懂窗口和指针。"""
    header = (
        f"# 会话摘要 · {host} · 第 {index}/{total} 块\n\n"
        f"- 时间窗：{window_text}\n"
        "- 指针：`L<行号>` 是源 JSONL 的物理行号（从 1 开始，空行也占一行）；节标题给出源文件绝对路径。\n"
        "- 范围：只含真人用户发言、工具失败和 retry 标注，不含助手输出。\n\n"
    )
    return header + "".join(sections)


def build_digest(host, root, since_value, extra_excludes):
    """生成摘要块与索引文档，不写盘也不做输出守卫；返回 `(blocks, index_document)`。

    `blocks` 是 `[(文件名, Markdown 文本)]`，索引里的 `blocks` 与它一一对应。沙箱入口
    `digest` 与产品入口 `tools/personalize.py digest` 共用这份纯生成逻辑，落盘位置各管各的。
    """
    since = parse_since(since_value)
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"--root 不是可用的目录：{root}")
    excludes = tuple(DEFAULT_EXCLUDES.get(host, ())) + tuple(extra_excludes)
    sections, unavailable = [], []
    total = skipped = 0
    for path in iter_sources(root, host, excludes):
        total += 1
        rows, source, project = scan_source(host, path, since)
        if not source["available"]:
            unavailable.append({"source_path": source["source_path"], "missing_reason": source["missing_reason"]})
            continue
        if not any(kind == USER_MESSAGE_KIND for row in rows for kind, _text in row["entries"]):
            skipped += 1
            continue
        sections.append(session_section(host, source, project, rows))
    blocks = split_blocks(sections)
    window_start = since.isoformat() if since is not None else None
    window_text = f"{window_start} 起" if window_start else "不限（未给 --since）"
    rendered, block_entries = [], []
    for index, block in enumerate(blocks, 1):
        name = f"{host}-{index}.md"
        text = block_markdown(host, index, len(blocks), window_text, block)
        rendered.append((name, text))
        block_entries.append({"file": name, "sessions": len(block), "bytes": len(text.encode("utf-8"))})
    index_document = {
        "host": host,
        "root": str(root),
        "since": since_value,
        "window_start": window_start,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions_total": total,
        "sessions_included": len(sections),
        "sessions_skipped": skipped,
        "sessions_unavailable": len(unavailable),
        "blocks": block_entries,
        "unavailable_sources": unavailable,
    }
    return rendered, index_document


def digest(host, root, out_dir, since_value, extra_excludes):
    """读时间窗内的会话，写 `<host>-<n>.md` 与 `<host>-index.json`。返回退出码。"""
    reason = refusal_reason(out_dir)
    if reason is not None:
        print(f"history: 拒绝写入：{reason}", file=sys.stderr)
        return REFUSAL_EXIT
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"--root 不是可用的目录：{root}")
    if out_inside_root(out_dir, root):
        print(f"history: 拒绝写入：{out_dir} 位于 --root {root} 之内", file=sys.stderr)
        return REFUSAL_EXIT
    rendered, index_document = build_digest(host, root, since_value, extra_excludes)
    for name, text in rendered:
        write_atomic(out_dir / name, text)
    write_atomic(out_dir / f"{host}-index.json", json.dumps(index_document, ensure_ascii=False, indent=2) + "\n")
    print(
        f"history: {host} 入选 {index_document['sessions_included']}/{index_document['sessions_total']} 个会话，"
        f"{len(rendered)} 块，跳过 {index_document['sessions_skipped']}，"
        f"不可用 {index_document['sessions_unavailable']}",
        file=sys.stderr,
    )
    return 0
