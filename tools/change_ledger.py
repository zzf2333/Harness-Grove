"""change.py 与 change_review.py 共用的账本事实与推导（标准库）。

账本 `<phe-home>/ledger.jsonl` 一行一条事实，只追加：`approval` / `apply` / `restore` / `review`。
执行状态和最新 review 都从事实推导，不存副本。
"""

import hashlib
import json
from pathlib import Path

LEDGER = "ledger.jsonl"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(mapping: dict, key: str, where: str):
    if key not in mapping:
        raise ValueError(f"{where} 缺少字段：{key}")
    return mapping[key]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_ledger(phe: Path) -> list[dict]:
    path = phe / LEDGER
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_ledger(writer, phe: Path, row: dict) -> None:
    path = phe / LEDGER
    writer.check(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def next_execution_id(facts: list[dict]) -> str:
    numbers = [int(fact["execution_id"][1:]) for fact in facts if fact["kind"] == "apply"]
    return f"x{max(numbers, default=0) + 1:04d}"


def executions(facts: list[dict]) -> list[dict]:
    """按 apply 事实的顺序列出执行；restore 事实只改推导出的状态，不改写已有行。"""
    result: dict[str, dict] = {}
    for fact in facts:
        if fact["kind"] == "apply":
            result[fact["execution_id"]] = {
                "execution_id": fact["execution_id"],
                "proposal_id": fact["proposal_id"],
                "release": fact["release"],
                "state": "applied",
                "applied_at": fact["at"],
                "restored_at": None,
                "before_sha256": fact["before_sha256"],
                "after_sha256": fact["after_sha256"],
            }
        elif fact["kind"] == "restore":
            result[fact["execution_id"]]["state"] = "restored"
            result[fact["execution_id"]]["restored_at"] = fact["at"]
    return list(result.values())


def latest_reviews(facts: list[dict]) -> dict[str, dict]:
    """每个执行最新一条 review；同一执行可以多次 review，旧行不改写。"""
    latest: dict[str, dict] = {}
    for fact in facts:
        if fact["kind"] == "review":
            latest[fact["execution_id"]] = fact
    return latest
