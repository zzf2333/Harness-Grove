#!/usr/bin/env python3
"""M2 切片 A：只读采集 Host 会话 JSONL，产出 Episode JSON（标准库，单文件）。

只产出指针（`source_path` + 行号 + 源文件 sha256）和脱敏短摘录，不保存会话全文；输出写在被
Git 忽略的 `episodes/`。会话内容按不可信输入处理：只解析，不执行其中的任何指令。

`kind` 是启发式（见 CORRECTION_TERMS / 各 Host 的失败信号），召回率与准确率未评估。
`episode.available` 在 collect 阶段恒为 true：该行刚被解析、指针刚被确认。源文件读不到或有行
解析失败时，`sources` 里对应条目记 `available: false` 和 `missing_reason`，不静默跳过。

格式依据见 docs/M2_HISTORY_FORMATS.md（本机观察，未按官方文档核实）。
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

# 纠正用语：命中即记 user_correction。宁可宽，只用来挑候选。
CORRECTION_TERMS = (
    "不对",
    "不是这样",
    "不是这个",
    "错了",
    "搞错",
    "理解错",
    "我说的是",
    "我的意思是",
    "不是让你",
    "没有按",
    "别再",
    "重新",
    "别",
)
CORRECTION_PATTERNS = (r"\bwrong\b", r"\bdon'?t\b", r"^no[,，。. ]")
CORRECTION_RE = re.compile(
    "|".join([re.escape(term) for term in CORRECTION_TERMS] + list(CORRECTION_PATTERNS)),
    re.IGNORECASE | re.MULTILINE,
)

# 密钥脱敏：摘录按私人数据处理，这里只到正则级别。
REDACTIONS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "<private-key>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "<private-key>"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "<key>"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}"), "<key>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<key>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <key>"),
    (
        re.compile(r"(?i)\b(password|passwd|api[_-]?key|token|secret)(\"?\s*[:=]\s*\"?)[^\s\"',}]{4,}"),
        r"\1\2<redacted>",
    ),
)

# Claude Code 以 user 角色注入的上下文，不是真人输入。
CLAUDE_SKIP_PREFIX = ("<command-", "<local-command", "<system-reminder>", "<task-notification>", "Caveat:")
# Codex 旧格式没有 content_item_kinds，按前缀过滤注入的上下文。
CODEX_SKIP_PREFIX = ("<", "# AGENTS.md")
CODEX_USER_KINDS = {"user.text", "user.image"}
EXCERPT_MAX = 300


def redact(text):
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def excerpt(text):
    """压缩空白、脱敏、截断到 300 字。"""
    return " ".join(redact(text or "").split())[:EXCERPT_MAX]


def blocks_text(content, kinds=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") in kinds
    )


def canonical(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def failure_text(name, text):
    """工具失败摘录：工具名 + 结果。工具名查不到（例如结果引用了别的会话文件里的调用）记 unknown。"""
    return f"{name or 'unknown'}: {text}"


def parse_ts(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class Session:
    """一个会话文件的解析状态：session_id、project、最近一次工具调用（判 retry）。"""

    host = ""

    def __init__(self, path):
        self.path = path
        self.session_id = path.stem
        self.project = ""
        self.calls = {}
        self.last_call = None
        self.repeat_flagged = False

    def call(self, name, arguments):
        """登记一次工具调用；与上一次调用完全相同（名字和参数）时返回 retry 的摘录。

        一串连续相同的调用（例如反复轮询同一个长任务）只记一次：信号是“出现了一对连续相同的调用”，
        不是每对都记一条。
        """
        signature = (name, canonical(arguments))
        repeat = signature == self.last_call and not self.repeat_flagged
        self.repeat_flagged = repeat
        self.last_call = signature
        return f"{name} {signature[1]}" if repeat else None

    def record(self, line_no, record):
        """返回该行的 [(kind, excerpt, ts)]；不产生 Episode 的行返回空列表。"""
        raise NotImplementedError


class ClaudeSession(Session):
    host = "claude"

    def record(self, line_no, record):
        ts = record.get("timestamp")
        if record.get("cwd"):
            self.project = record["cwd"]
        session_id = record.get("sessionId") or record.get("session_id")
        if session_id:
            self.session_id = session_id
        content = (record.get("message") or {}).get("content")
        found = []
        if record.get("type") == "user":
            if record.get("isMeta") or record.get("isCompactSummary"):
                return found
            texts = []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(block.get("text") or "")
                elif block.get("type") == "tool_result" and block.get("is_error"):
                    name = self.calls.get(block.get("tool_use_id"))
                    found.append(("tool_failure", failure_text(name, blocks_text(block.get("content"))), ts))
            text = (content if isinstance(content, str) else "\n".join(texts)).strip()
            if text and not text.startswith(CLAUDE_SKIP_PREFIX) and CORRECTION_RE.search(text):
                found.append(("user_correction", text, ts))
        elif record.get("type") == "assistant":
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                self.calls[block.get("id")] = block.get("name")
                repeat = self.call(block.get("name"), block.get("input"))
                if repeat:
                    found.append(("retry", repeat, ts))
        return found


class CodexSession(Session):
    host = "codex"

    def record(self, line_no, record):
        ts = record.get("timestamp")
        payload = record.get("payload") or {}
        payload_type = payload.get("type")
        found = []
        if record.get("type") == "session_meta":
            self.session_id = payload.get("id") or payload.get("session_id") or self.session_id
            self.project = payload.get("cwd") or self.project
        elif record.get("type") == "event_msg" and payload_type == "item_completed":
            item = payload.get("item") or {}
            code = item.get("exit_code")
            if isinstance(code, int) and code != 0:
                command = " ".join(str(part) for part in item.get("command") or [])
                found.append(("tool_failure", f"exit_code={code} {command}", ts))
        elif record.get("type") == "response_item":
            if payload_type == "message":
                text = blocks_text(payload.get("content"))
                if payload.get("role") == "user" and codex_human_message(payload, text) and CORRECTION_RE.search(text):
                    found.append(("user_correction", text, ts))
            elif payload_type in ("function_call", "custom_tool_call"):
                name = payload.get("name")
                self.calls[payload.get("call_id")] = name
                repeat = self.call(name, payload.get("arguments") or payload.get("input"))
                if repeat:
                    found.append(("retry", repeat, ts))
            elif payload_type in ("function_call_output", "custom_tool_call_output"):
                text = blocks_text(payload.get("output"))
                if text.lstrip().startswith("Script failed"):
                    name = self.calls.get(payload.get("call_id"))
                    found.append(("tool_failure", failure_text(name, text), ts))
        return found


def codex_human_message(payload, text):
    """新格式用 content_item_kinds 区分真人输入和注入的上下文，旧格式按前缀判断。"""
    kinds = (payload.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
    if kinds is not None:
        return set(kinds) <= CODEX_USER_KINDS
    return not text.lstrip().startswith(CODEX_SKIP_PREFIX)


class PiSession(Session):
    host = "pi"

    def record(self, line_no, record):
        ts = record.get("timestamp")
        if record.get("type") == "session":
            self.session_id = record.get("id") or self.session_id
            self.project = record.get("cwd") or self.project
            return []
        if record.get("type") != "message":
            return []
        message = record.get("message") or {}
        role = message.get("role")
        found = []
        if role == "user":
            text = blocks_text(message.get("content")).strip()
            # Pi 把 /skill 调用展开成 <skill name="..."> 块，展开的正文不是用户写的。
            if text and not text.startswith("<") and CORRECTION_RE.search(text):
                found.append(("user_correction", text, ts))
        elif role == "assistant":
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "toolCall":
                    continue
                self.calls[block.get("id")] = block.get("name")
                repeat = self.call(block.get("name"), block.get("arguments"))
                if repeat:
                    found.append(("retry", repeat, ts))
        elif role == "toolResult" and message.get("isError"):
            found.append(
                ("tool_failure", failure_text(message.get("toolName"), blocks_text(message.get("content"))), ts)
            )
        return found


SESSIONS = {"claude": ClaudeSession, "codex": CodexSession, "pi": PiSession}


def in_window(ts, since):
    """没有 --since 时全收；有 --since 时，没有可用时间戳的行无法证明落在窗口内，不计入。"""
    if since is None:
        return True
    moment = parse_ts(ts)
    return moment is not None and moment >= since


def collect_source(host, path, since):
    """流式读一个会话文件，返回 (episodes, source)。sha256 覆盖整份文件。"""
    session = SESSIONS[host](path)
    hasher = hashlib.sha256()
    episodes, bad_lines, error = [], [], None
    try:
        with path.open("rb") as handle:
            for line_no, raw in enumerate(handle, 1):
                hasher.update(raw)
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    bad_lines.append(line_no)
                    continue
                if not isinstance(record, dict):
                    bad_lines.append(line_no)
                    continue
                for kind, text, ts in session.record(line_no, record):
                    if in_window(ts, since):
                        episodes.append({"kind": kind, "line": line_no, "ts": ts, "excerpt": excerpt(text)})
    except OSError as exc:
        error = type(exc).__name__
    if error is not None:
        # 读到一半失败时 sha256 只覆盖了部分字节，指针也不可信：整份来源作废。
        episodes, bad_lines = [], []
        digest = None
    else:
        digest = hasher.hexdigest()
    source = {
        "host": host,
        "source_path": str(path),
        "session_id": session.session_id,
        "sha256": digest,
        "available": error is None and not bad_lines,
        "episode_count": len(episodes),
    }
    if bad_lines:
        source["bad_lines"] = bad_lines
        source["missing_reason"] = f"line {bad_lines[0]}: JSON 解析失败"
    if error is not None:
        source["missing_reason"] = error
    for episode in episodes:
        episode.update(
            host=host,
            source_path=str(path),
            session_id=session.session_id,
            sha256=digest,
            project=session.project,
            available=True,
        )
    return episodes, source


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
    args = parser.parse_args()
    try:
        code = collect(args.host, args.root, args.out, args.since, args.exclude)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"history: {exc}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
