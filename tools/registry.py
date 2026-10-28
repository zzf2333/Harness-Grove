#!/usr/bin/env python3
"""PHE 资产登记表：adopt 把盘点条目登记为 Active(imported, unverified)（标准库，单文件）。

登记表只记路径、Hash、软链目标和状态，不记文件内容，也不记 settings 的值。
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REGISTRY = Path("registry/assets.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_registry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        raise TypeError(f"{path} 不是登记表：缺少 assets 列表")
    return data


def next_asset_id(registry: dict) -> str:
    """编号只增不减：取现有最大编号 + 1。本切片没有删除功能，不需要另存计数器。"""
    numbers = [int(asset["asset_id"].rsplit("-", 1)[1]) for asset in registry["assets"]]
    return f"asset-{max(numbers, default=0) + 1:04d}"


def refusal_reason(record: dict) -> str | None:
    if record.get("access_error"):
        return f"access_error={record['access_error']}"
    if record["broken_link"]:
        return "broken_link"
    if record["resolved_path"] is None:
        return "无法解析真实路径"
    return None


def selection(data: dict, paths: list[str], groups: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """把 --path / --group 解析成待登记资产，返回 ({resolved_path: 记录}, 拒绝说明)。

    资产按 resolved_path 认定；observed 覆盖盘点里所有指向它的记录，所以按 path 和按 group
    选中的是同一个资产。
    """
    by_path: dict[str, list[dict]] = {}
    by_resolved: dict[str, list[dict]] = {}
    for record in data["records"]:
        by_path.setdefault(record["path"], []).append(record)
        if record["resolved_path"] is not None:
            by_resolved.setdefault(record["resolved_path"], []).append(record)
    group_members = {group["resolved_path"]: group["paths"] for group in data.get("groups", [])}

    targets: list[tuple[str, list[dict]]] = [(path, by_path.get(path, [])) for path in paths]
    for resolved in groups:
        members = group_members.get(resolved)
        targets.append((resolved, [record for path in members or [] for record in by_path.get(path, [])]))

    chosen: dict[str, list[dict]] = {}
    refusals: list[str] = []
    for label, records in targets:
        if not records:
            refusals.append(f"{label}: 盘点里没有该 path 或分组")
            continue
        reasons = [reason for record in records if (reason := refusal_reason(record)) is not None]
        if reasons:
            refusals.append(f"{label}: {'、'.join(sorted(set(reasons)))}")
            continue
        chosen[records[0]["resolved_path"]] = by_resolved[records[0]["resolved_path"]]
    return chosen, refusals


def new_asset(asset_id: str, resolved_path: str, records: list[dict], adopted_at: str, digest: str) -> dict:
    ordered = sorted(records, key=lambda record: (record["agent"], record["path"]))
    # 同一 resolved_path 的各条记录读的是同一个真实文件，指纹一致，取排序后的第一条。
    first = ordered[0]
    return {
        "asset_id": asset_id,
        "resolved_path": resolved_path,
        "observed": [
            {
                "agent": record["agent"],
                "path": record["path"],
                "is_symlink": record["is_symlink"],
                "symlink_target": record["symlink_target"],
            }
            for record in ordered
        ],
        "baseline": {
            "sha256": first["sha256"],
            "size": first["size"],
            "mtime": first["mtime"],
            "paths": {record["path"]: record["symlink_target"] for record in records},
        },
        "state": "Active",
        "origin": "imported",
        "evidence_status": "unverified",
        "adopted_at": adopted_at,
        "inventory_sha256": digest,
    }


def adopt(inventory_path: Path, registry_path: Path, paths: list[str], groups: list[str]) -> int:
    inventory_bytes = inventory_path.read_bytes()
    data = json.loads(inventory_bytes)
    try:
        registry = read_registry(registry_path)
    except FileNotFoundError:
        registry = {"assets": []}
    chosen, refusals = selection(data, paths, groups)
    existing = {asset["resolved_path"]: asset["asset_id"] for asset in registry["assets"]}
    adopted_at = utc_now()
    digest = hashlib.sha256(inventory_bytes).hexdigest()
    added = 0
    for resolved_path in sorted(chosen):
        if resolved_path in existing:
            print(f"skip {resolved_path}: 已登记为 {existing[resolved_path]}")
            continue
        asset = new_asset(next_asset_id(registry), resolved_path, chosen[resolved_path], adopted_at, digest)
        registry["assets"].append(asset)
        added += 1
        print(f"adopted {asset['asset_id']} {resolved_path} ({len(asset['observed'])} 个 observed path)")
    if added:
        # 只写登记表本身；被登记的文件一个字节都不动。
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for refusal in refusals:
        print(f"refused {refusal}", file=sys.stderr)
    return 1 if refusals else 0


def main():
    ap = argparse.ArgumentParser(description="PHE registry")
    commands = ap.add_subparsers(dest="cmd", required=True)

    adopt_parser = commands.add_parser("adopt", help="把盘点中的条目登记进登记表")
    adopt_parser.add_argument("--inventory", type=Path, required=True)
    adopt_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    adopt_parser.add_argument("--path", action="append", default=[], help="按盘点记录的 path 选择")
    adopt_parser.add_argument("--group", action="append", default=[], help="按盘点 groups 的 resolved_path 选择整组")

    args = ap.parse_args()
    if not (args.path or args.group):
        ap.error("adopt 至少需要一个 --path 或 --group")
    try:
        code = adopt(args.inventory, args.registry, args.path, args.group)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(1, f"registry: {exc}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
