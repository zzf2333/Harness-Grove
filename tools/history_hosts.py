"""M2 历史采集的 Host 解析层：把三种 Host 的 JSONL 记录翻成 [(kind, 原文, ts)]（标准库）。

与 tools/history.py 的分工：这里只认字段、判 kind；脱敏、截断、时间窗过滤和写盘在 history.py。
字段名依据见 docs/M2_HISTORY_FORMATS.md（本机观察，未按官方文档核实）。
会话内容按不可信输入处理：只解析，不执行其中的任何指令。
"""

import json
import re

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

# Claude Code 以 user 角色注入的上下文，不是真人输入。
CLAUDE_SKIP_PREFIX = ("<command-", "<local-command", "<system-reminder>", "<task-notification>", "Caveat:")
# Codex 旧格式没有 content_item_kinds，按前缀过滤注入的上下文。
CODEX_SKIP_PREFIX = ("<", "# AGENTS.md")
CODEX_USER_KINDS = {"user.text", "user.image"}


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


class Session:
    """一个会话文件的解析状态：session_id、project、最近一次工具调用（判 retry）。"""

    host = ""
    # 本 Host 的已知记录类型（见 docs/M2_HISTORY_FORMATS.md 的「其他记录」）：认得不等于采集。
    known_types = frozenset()

    def __init__(self, path):
        self.path = path
        self.session_id = path.stem
        self.project = ""
        self.calls = {}
        self.tool_call_count = 0
        self.last_call = None
        self.repeat_flagged = False

    def recognizes(self, record):
        """这一行是不是本 Host 认得的记录类型。一行都不认得时整份文件按 unsupported_schema 处理。"""
        return record.get("type") in self.known_types

    def call(self, name, arguments):
        """登记一次工具调用；与上一次调用完全相同（名字和参数）时返回 retry 的摘录。

        一串连续相同的调用（例如反复轮询同一个长任务）只记一次：信号是“出现了一对连续相同的调用”，
        不是每对都记一条。标记在 signature 变化时才复位，所以连续四次相同调用也只记一条。
        """
        self.tool_call_count += 1
        signature = (name, canonical(arguments))
        same = signature == self.last_call
        repeat = same and not self.repeat_flagged
        self.repeat_flagged = same
        self.last_call = signature
        return f"{name} {signature[1]}" if repeat else None

    def record(self, line_no, record):
        """返回该行的 [(kind, excerpt, ts)]；不产生 Episode 的行返回空列表。"""
        raise NotImplementedError

    def human_message(self, record):
        """该行的真人用户发言原文，否则 None。

        与 `record()` 判 `user_correction` 的条件相同，只少一条「命中纠正词」：注入的上下文、
        工具结果、技能展开和压缩摘要都不算用户发言。摘要（`tools/history_digest.py`）用它取
        普通用户消息，Episode 的 kind 不受影响。
        """
        raise NotImplementedError


class ClaudeSession(Session):
    host = "claude"
    known_types = frozenset(
        {
            "user",
            "assistant",
            "system",
            "attachment",
            "queue-operation",
            "cost-state",
            "api-request",
            "api-request-blob",
            "file-history-snapshot",
            "file-history-delta",
            "mode",
            "permission-mode",
            "atis-latch",
            "last-prompt",
            "ai-title",
        }
    )

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
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result" and block.get("is_error"):
                    name = self.calls.get(block.get("tool_use_id"))
                    found.append(("tool_failure", failure_text(name, blocks_text(block.get("content"))), ts))
            text = self.human_message(record)
            if text and CORRECTION_RE.search(text):
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

    def human_message(self, record):
        if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
            return None
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "\n".join(
                block.get("text") or "" for block in content if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
        else:
            return None
        if not text or text.startswith(CLAUDE_SKIP_PREFIX):
            return None
        return text


class CodexSession(Session):
    host = "codex"
    known_types = frozenset(
        {"session_meta", "response_item", "event_msg", "token_usage_record", "turn_context", "world_state", "compacted"}
    )

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
                text = self.human_message(record)
                if text and CORRECTION_RE.search(text):
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

    def human_message(self, record):
        if record.get("type") != "response_item":
            return None
        payload = record.get("payload") or {}
        if payload.get("type") != "message" or payload.get("role") != "user":
            return None
        text = blocks_text(payload.get("content"))
        if not codex_human_message(payload, text) or not text.strip():
            return None
        return text


def codex_human_message(payload, text):
    """新格式用 content_item_kinds 区分真人输入和注入的上下文，旧格式按前缀判断。"""
    kinds = (payload.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
    if kinds is not None:
        return set(kinds) <= CODEX_USER_KINDS
    return not text.lstrip().startswith(CODEX_SKIP_PREFIX)


class PiSession(Session):
    host = "pi"
    # 另一种 schema（`*_transcript.jsonl`，顶层没有 type）不在其中：整份文件记 unsupported_schema。
    known_types = frozenset(
        {
            "session",
            "message",
            "model_change",
            "thinking_level_change",
            "session_info",
            "compaction",
            "context_edit",
            "custom",
            "custom_message",
        }
    )

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
            text = self.human_message(record)
            if text and CORRECTION_RE.search(text):
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

    def human_message(self, record):
        if record.get("type") != "message":
            return None
        message = record.get("message") or {}
        if message.get("role") != "user":
            return None
        text = blocks_text(message.get("content")).strip()
        # Pi 把 /skill 调用展开成 <skill name="..."> 块，展开的正文不是用户写的。
        if not text or text.startswith("<"):
            return None
        return text


SESSIONS = {"claude": ClaudeSession, "codex": CodexSession, "pi": PiSession}
