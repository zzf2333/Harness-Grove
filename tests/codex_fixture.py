"""Codex 接口测试共用的 JSONL 事件构造器：全部手写合成，不调用模型。"""

import json
from typing import Any

USAGE = {
    "input_tokens": 100,
    "cached_input_tokens": 40,
    "cache_write_input_tokens": 60,
    "output_tokens": 10,
    "reasoning_output_tokens": 5,
}
ZERO_USAGE = dict.fromkeys(USAGE, 0)


def event(kind: str, **fields: Any) -> dict:
    return {"type": kind, **fields}


def jsonl(*events: dict) -> bytes:
    return ("\n".join(json.dumps(item) for item in events) + "\n").encode()


def completed(usage: dict | None = None) -> dict:
    return event("turn.completed", **({"usage": usage} if usage is not None else {}))


def message(text: str) -> dict:
    return event("item.completed", item={"type": "agent_message", "text": text})
