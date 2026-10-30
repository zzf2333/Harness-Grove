"""M3a proposal 工具的共用输入校验：JSON 读取、字段集合、非空字符串、正整数和带时区时间。

`proposal_candidate.py` 处理候选段，`proposal_retired.py` 处理显式 Retired 检索输入。两边用同一套
错误类型和字段规则，调用方统一按 `InputError` 退出 1、不写产物。
"""

import json
import re
from datetime import datetime

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InputError(ValueError):
    """输入或结构不合法：调用方退出 1，不写任何产物。"""


def read_json_object(path, label):
    """读一个 UTF-8 JSON 对象，同时返回原始字节（输入文件的 sha256 绑定用）。"""
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


def sha256_hex(where, value):
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise InputError(f"{where} 不是小写十六进制 SHA-256")
    return value


def parse_timestamp(where, value):
    """解析带时区的 ISO 日期时间。`Z` 按 UTC 处理；无时区或不可解析都拒绝。"""
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} 不是非空字符串")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InputError(f"{where} 不是 ISO 日期时间：{value!r}") from exc
    if parsed.tzinfo is None:
        raise InputError(f"{where} 必须带时区：{value!r}")
    return parsed
