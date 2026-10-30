#!/usr/bin/env python3
"""M2 切片 A/B：只读采集 Host 会话 JSONL 产出 Episode JSON，并做确定性结构分组（标准库）。

只产出指针（`source_path` + 行号 + 源文件 sha256）和脱敏短摘录，不保存会话全文；输出写在被
Git 忽略的 `episodes/`。会话内容按不可信输入处理：只解析，不执行其中的任何指令。三种 Host 的
字段解析在 tools/history_hosts.py。

`kind` 是启发式（见 history_hosts.CORRECTION_TERMS / 各 Host 的失败信号），召回率与准确率未评估。
`episode.available` 在 collect 阶段恒为 true：该行刚被解析、指针刚被确认。`sources` 里读不到、
有坏行、或整份文件没有一行是本 Host 的已知记录类型时，记 `available: false` 和 `missing_reason`，
不静默跳过。

格式依据见 docs/M2_HISTORY_FORMATS.md（本机观察，未按官方文档核实）。

`group` 子命令读 collect 产物、按 host + session_id + kind 做确定性结构分组，实现在
tools/history_group.py；它复用这里的 `--out` 守卫和原子写。`digest` 子命令把最近的真实会话整理成
脱敏 Markdown 摘要供诊断子代理读，实现在 tools/history_digest.py；它复用这里的来源遍历、时间窗、
脱敏和输出守卫，并额外用 `Session.human_message` 取普通真人用户消息（不进 collect 产物）。
"""

import argparse
import hashlib
import json
import os
import pwd
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history_hosts import SESSIONS

# --out 不得落进真实 home 下的这些目录（与 tools/release_deploy.py 同一份清单，独立实现）。
PROTECTED_SUBPATHS = (".claude", ".codex", ".pi", ".phe", ".agents", "AI/temper")
REFUSAL_EXIT = 2

# 各 Host 的会话文件布局，相对 --root（本机观察，见 docs/M2_HISTORY_FORMATS.md）。
# root 取 Host 的配置根：claude 是 ~/.claude，codex 是 ~/.codex，pi 是 ~/.pi/agent。
LAYOUTS = {
    "claude": ("projects/*/*.jsonl",),
    "codex": ("sessions/**/*.jsonl",),
    "pi": ("sessions/**/*.jsonl",),
}
# Pi 的 sessions/subagent/ 是子代理会话，默认排除；--exclude 在默认之外追加。
DEFAULT_EXCLUDES = {"pi": ("sessions/subagent/*",)}

# 密钥脱敏：摘录按私人数据处理，这里只到正则级别。
# 私钥块先整体换掉带结束标记的；找不到结束标记时（源文本被截断或本来就残缺）从 BEGIN 一直替到
# 文本末尾，只删 BEGIN 会把私钥正文留在摘录里。
PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
PRIVATE_KEY_UNCLOSED = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.DOTALL)
REDACTIONS = (
    (PRIVATE_KEY_BLOCK, "<private-key>"),
    (PRIVATE_KEY_UNCLOSED, "<private-key>"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "<key>"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}"), "<key>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<key>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <key>"),
    (
        re.compile(r"(?i)\b(password|passwd|api[_-]?key|token|secret)(\"?\s*[:=]\s*\"?)[^\s\"',}]{4,}"),
        r"\1\2<redacted>",
    ),
)
EXCERPT_MAX = 300

# collect 产出的三种启发式 kind。USER_MESSAGE_KIND 不是 Episode kind，只给摘要用。
EPISODE_KINDS = ("user_correction", "tool_failure", "retry")
USER_MESSAGE_KIND = "user_message"

# 解析器碰到结构异常的行时抛这些异常：记为坏行，不影响同文件的其他行。
PARSER_ERRORS = (AttributeError, TypeError, KeyError, ValueError)


def redact(text):
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def excerpt(text, limit=EXCERPT_MAX):
    """压缩空白、脱敏、截断到 limit 字（默认 300）。"""
    return " ".join(redact(text or "").split())[:limit]


def parse_ts(value):
    if isinstance(value, (int, float)):
        # 越界或 NaN 的数值时间戳和无法解析的字符串同样处理：当作没有可用时间戳，不中断整份文件。
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def in_window(ts, since):
    """没有 --since 时全收；有 --since 时，没有可用时间戳的行无法证明落在窗口内，不计入。"""
    if since is None:
        return True
    moment = parse_ts(ts)
    return moment is not None and moment >= since


def collect_source(host, path, since):
    """采集一个会话文件，返回 (episodes, source)。"""
    rows, source, project = scan_source(host, path, since)
    episodes = [
        {"kind": kind, "line": row["line"], "ts": row["ts"], "excerpt": excerpt(text)}
        for row in rows
        for kind, text in row["entries"]
        if kind in EPISODE_KINDS
    ]
    for episode in episodes:
        episode.update(
            host=host,
            source_path=source["source_path"],
            session_id=source["session_id"],
            sha256=source["sha256"],
            project=project,
            available=True,
        )
    return episodes, source


def scan_source(host, path, since):
    """流式读一个会话文件，返回 (rows, source, project)。sha256 覆盖整份文件。

    `rows` 只覆盖时间窗内的记录，按文件顺序，每行一条：`{"line", "ts", "tool_calls", "entries"}`。
    `entries` 是该行的 `[(kind, 原文)]`，kind 是三个 Episode kind 外加只给摘要用的
    `USER_MESSAGE_KIND`；`tool_calls` 是该行新增的工具调用数。
    有时间戳的行也产出 row，供摘要计算首末时间。`source` 与 collect 的 `sources` 条目同形状，`project`
    单独返回，因为来源条目里没有这个字段。
    """
    session = SESSIONS[host](path)
    hasher = hashlib.sha256()
    rows, bad_lines, error = [], [], None
    seen = recognized = episode_count = 0
    try:
        with path.open("rb") as handle:
            for line_no, raw in enumerate(handle, 1):
                hasher.update(raw)
                if not raw.strip():
                    continue
                seen += 1
                try:
                    record = json.loads(raw)
                except ValueError:
                    bad_lines.append({"line": line_no, "missing_reason": "invalid_json"})
                    continue
                if not isinstance(record, dict):
                    bad_lines.append({"line": line_no, "missing_reason": "not_an_object"})
                    continue
                calls_before = session.tool_call_count
                # Host 解析和 json.loads 在同一层异常边界里：坏行只废掉自己，不中断整份文件。
                try:
                    known = session.recognizes(record)
                    found = session.record(line_no, record)
                    message = session.human_message(record)
                except PARSER_ERRORS as exc:
                    bad_lines.append({"line": line_no, "missing_reason": f"malformed_record: {type(exc).__name__}"})
                    continue
                if known:
                    recognized += 1
                ts = record.get("timestamp")
                if not in_window(ts, since):
                    continue
                entries = [(kind, text) for kind, text, _ts in found]
                if message is not None:
                    entries.append((USER_MESSAGE_KIND, message))
                tool_calls = session.tool_call_count - calls_before
                if not entries and not tool_calls and parse_ts(ts) is None:
                    continue
                episode_count += sum(1 for kind, _text in entries if kind in EPISODE_KINDS)
                rows.append({"line": line_no, "ts": ts, "tool_calls": tool_calls, "entries": entries})
    except OSError as exc:
        error = type(exc).__name__
    if error is not None:
        # 读到一半失败时 sha256 只覆盖了部分字节，指针也不可信：整份来源作废。
        rows, bad_lines, episode_count = [], [], 0
        digest = None
    else:
        digest = hasher.hexdigest()
    reason = unavailable_reason(bad_lines, error, seen, recognized)
    source = {
        "host": host,
        "source_path": str(path),
        "session_id": session.session_id,
        "sha256": digest,
        "available": reason is None,
        "episode_count": episode_count,
    }
    if bad_lines:
        source["bad_lines"] = bad_lines
    if reason is not None:
        source["missing_reason"] = reason
    return rows, source, session.project


def unavailable_reason(bad_lines, error, seen, recognized):
    """一份来源是否可用；不可用时给出原因，可用时 None。三种语义见 docs/M2_HISTORY_FORMATS.md。"""
    if error is not None:
        return error
    if bad_lines:
        return f"line {bad_lines[0]['line']}: {bad_lines[0]['missing_reason']}"
    # 一行都认不出本 Host 的已知记录类型（例如 Pi 的 `*_transcript.jsonl`）时，不能静默产出空结果。
    if seen > 0 and recognized == 0:
        return "unsupported_schema"
    return None


def iter_sources(root, host, excludes):
    seen = set()
    for pattern in LAYOUTS[host]:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if any(fnmatch(relative, exclude) for exclude in excludes):
                continue
            if relative not in seen:
                seen.add(relative)
                yield path


def refusal_reason(out):
    """真实 home 下的 Agent 配置目录不接收采集产物。按 realpath 字符串比，大小写不敏感。"""
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    spelled = str(Path(os.path.realpath(out))).casefold()
    for relative in PROTECTED_SUBPATHS:
        protected = home / relative
        guard = str(Path(os.path.realpath(protected))).casefold()
        if spelled == guard or spelled.startswith(guard + os.sep):
            return f"{out} 位于真实环境 {protected} 之内"
    return None


def out_inside_root(out, root):
    """--out 是否落在 --root 之内：那里是源会话文件，写进去就盖掉原始证据。

    父目录取 realpath（`--out` 常指向还不存在的文件），比较时 casefold；路径已存在时再用 samefile，
    因为 realpath 不化解硬链接。
    """
    root_real = Path(os.path.realpath(root))
    guard = str(root_real).casefold()
    candidates = [Path(os.path.realpath(out.parent)) / out.name]
    if out.exists():
        candidates.append(Path(os.path.realpath(out)))
    for candidate in candidates:
        spelled = str(candidate).casefold()
        if spelled == guard or spelled.startswith(guard + os.sep):
            return True
        if candidate.exists() and root_real.exists() and os.path.samefile(candidate, root_real):
            return True
    return False


def write_atomic(path, text):
    """同目录写完临时文件再替换：写到一半失败时不留半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
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


def parse_since(value):
    """`7d` 取当前时刻往前 7×24 小时；`YYYY-MM-DD` 取本机时区当天 00:00。"""
    if value is None:
        return None
    match = re.fullmatch(r"(\d+)d", value)
    if match:
        return datetime.now(timezone.utc) - timedelta(days=int(match.group(1)))
    day = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if day is None:
        raise ValueError(f"--since 只接受 `7d` 或 `YYYY-MM-DD`：{value}")
    year, month, day_number = (int(part) for part in day.groups())
    return datetime(year, month, day_number, tzinfo=datetime.now().astimezone().tzinfo)


def collect(host, root, out, since_value, extra_excludes):
    since = parse_since(since_value)
    reason = refusal_reason(out)
    if reason is not None:
        print(f"history: 拒绝写入：{reason}", file=sys.stderr)
        return REFUSAL_EXIT
    if not root.is_dir():
        raise ValueError(f"--root 不是可用的目录：{root}")
    if out_inside_root(out, root):
        print(f"history: 拒绝写入：{out} 位于 --root {root} 之内", file=sys.stderr)
        return REFUSAL_EXIT
    excludes = tuple(DEFAULT_EXCLUDES.get(host, ())) + tuple(extra_excludes)
    episodes, sources = [], []
    for path in iter_sources(root, host, excludes):
        found, source = collect_source(host, path, since)
        episodes.extend(found)
        sources.append(source)
    write_atomic(out, json.dumps({"episodes": episodes, "sources": sources}, ensure_ascii=False, indent=2) + "\n")
    unavailable = [source for source in sources if not source["available"]]
    print(
        f"history: {host} {len(episodes)} 条 episode，{len(sources)} 个来源，{len(unavailable)} 个不可用",
        file=sys.stderr,
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description="PHE history")
    commands = parser.add_subparsers(dest="cmd", required=True)
    collect_parser = commands.add_parser("collect", help="只读采集会话 JSONL，产出 Episode JSON")
    collect_parser.add_argument("--host", choices=sorted(SESSIONS), required=True)
    collect_parser.add_argument("--root", type=Path, required=True, help="Host 配置根，例如 ~/.claude")
    collect_parser.add_argument("--out", type=Path, required=True, help="Episode JSON 输出路径")
    collect_parser.add_argument("--since", help="`7d` 或 `YYYY-MM-DD`")
    collect_parser.add_argument("--exclude", action="append", default=[], help="相对 --root 的 glob，可重复")
    group_parser = commands.add_parser("group", help="把 Episode JSON 按 host + session_id + kind 分组")
    group_parser.add_argument(
        "--episodes", type=Path, nargs="+", required=True, help="collect 产出的 Episode JSON，可多个"
    )
    group_parser.add_argument("--out", type=Path, required=True, help="分组 JSON 输出路径")
    digest_parser = commands.add_parser("digest", help="把最近的真实会话整理成脱敏 Markdown 摘要")
    digest_parser.add_argument("--host", choices=sorted(SESSIONS), required=True)
    digest_parser.add_argument("--root", type=Path, required=True, help="Host 配置根，例如 ~/.claude")
    digest_parser.add_argument("--out-dir", type=Path, required=True, help="摘要与索引的输出目录")
    digest_parser.add_argument("--since", help="`7d` 或 `YYYY-MM-DD`")
    digest_parser.add_argument("--exclude", action="append", default=[], help="相对 --root 的 glob，可重复")
    args = parser.parse_args()
    try:
        if args.cmd == "collect":
            code = collect(args.host, args.root, args.out, args.since, args.exclude)
        elif args.cmd == "digest":
            from history_digest import digest

            code = digest(args.host, args.root, args.out_dir, args.since, args.exclude)
        else:
            # 分组单独放 tools/history_group.py，采集和分组不互相拖大；它复用本模块的输出守卫。
            from history_group import group

            code = group(args.episodes, args.out)
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(1, f"history: {exc}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
