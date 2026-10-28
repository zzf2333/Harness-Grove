#!/usr/bin/env python3
"""PHE 资产登记表：adopt 登记盘点条目，drift 只读比对基线（标准库，单文件）。

登记表只记路径、Hash、软链目标和状态，不记文件内容，也不记 settings 的值。
ExternalChange 只报告，不自动接受、覆盖或写回登记表。
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
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
    """编号只增不减：登记表保存高水位 next_asset_number，删掉条目也不会回退。

    边界：手改或删掉整份登记表时，只能保证不与当前剩余条目重号。
    """
    numbers = [int(asset["asset_id"].rsplit("-", 1)[1]) for asset in registry["assets"]]
    number = max(registry.get("next_asset_number", 1), max(numbers, default=0) + 1)
    registry["next_asset_number"] = number + 1
    return f"asset-{number:04d}"


def write_atomic(path: Path, text: str) -> None:
    """同目录写完临时文件再替换，写到一半失败时旧文件保持不变。不防并发登记的丢更新。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def refuse_protected_out(out: Path, protected: list[str]) -> None:
    """报告不得覆盖登记表、盘点或任何已登记文件（含软链和硬链接别名）。"""
    real_out = os.path.realpath(out)
    for path in protected:
        same = os.path.realpath(path) == real_out
        if not same and out.exists() and os.path.exists(path):
            same = os.path.samefile(out, path)
        if same:
            raise ValueError(f"--out 不能指向受保护的文件：{path}")


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
        write_atomic(registry_path, json.dumps(registry, ensure_ascii=False, indent=2) + "\n")
    for refusal in refusals:
        print(f"refused {refusal}", file=sys.stderr)
    return 1 if refusals else 0


def change(asset: dict, change_type: str, path: str, before: dict | None, after: dict | None, at: str) -> dict:
    return {
        "asset_id": asset["asset_id"],
        "type": change_type,
        "path": path,
        "before": before,
        "after": after,
        "detected_at": at,
    }


def path_state(record: dict) -> dict:
    return {"symlink_target": record["symlink_target"], "resolved_path": record["resolved_path"]}


def asset_changes(asset: dict, latest: dict[str, dict], at: str) -> list[dict]:
    """按 observed path 比对：先看缺失和不可读，再看软链目标，最后看内容。

    内容属于真实文件，所以每个资产最多报一条 content_changed；软链目标变了的 path
    不再参与内容比对，否则换链会同时报成内容变化。
    """
    baseline = asset["baseline"]
    resolved_path = asset["resolved_path"]
    changes = []
    content_records = []
    for path in sorted(baseline["paths"]):
        known = {"symlink_target": baseline["paths"][path], "resolved_path": resolved_path}
        record = latest.get(path)
        if record is None:
            changes.append(change(asset, "missing", path, known, None, at))
            continue
        if record.get("access_error") or record["broken_link"]:
            after = path_state(record) | {
                "access_error": record.get("access_error"),
                "broken_link": record["broken_link"],
            }
            changes.append(change(asset, "unreadable", path, known, after, at))
            continue
        if record["symlink_target"] != baseline["paths"][path] or record["resolved_path"] != resolved_path:
            changes.append(change(asset, "symlink_target_changed", path, known, path_state(record), at))
            continue
        content_records.append(record)
    for record in content_records:
        if record["sha256"] != baseline["sha256"]:
            before = {"sha256": baseline["sha256"], "size": baseline["size"], "mtime": baseline["mtime"]}
            after = {"sha256": record["sha256"], "size": record["size"], "mtime": record["mtime"]}
            changes.append(change(asset, "content_changed", resolved_path, before, after, at))
            break
    return changes


def drift(inventory_path: Path, registry_path: Path, out: Path | None) -> int:
    inventory_bytes = inventory_path.read_bytes()
    data = json.loads(inventory_bytes)
    registry = read_registry(registry_path)
    at = utc_now()
    latest: dict[str, dict] = {}
    for record in data["records"]:
        # 同一 path 可能被多个 agent 各记一条，读的是同一个文件，结果一致，取第一条。
        latest.setdefault(record["path"], record)
    changes = []
    for asset in registry["assets"]:
        changes.extend(asset_changes(asset, latest, at))
    report = {
        "detected_at": at,
        "inventory": str(inventory_path),
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "changes": len(changes),
        "external_changes": changes,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if out is None:
        print(text, end="")
    else:
        protected = [str(registry_path), str(inventory_path)]
        for asset in registry["assets"]:
            protected.append(asset["resolved_path"])
            protected.extend(o["path"] for o in asset["observed"])
        refuse_protected_out(out, protected)
        write_atomic(out, text)
    return 0


def main():
    ap = argparse.ArgumentParser(description="PHE registry")
    commands = ap.add_subparsers(dest="cmd", required=True)

    adopt_parser = commands.add_parser("adopt", help="把盘点中的条目登记进登记表")
    adopt_parser.add_argument("--inventory", type=Path, required=True)
    adopt_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    adopt_parser.add_argument("--path", action="append", default=[], help="按盘点记录的 path 选择")
    adopt_parser.add_argument("--group", action="append", default=[], help="按盘点 groups 的 resolved_path 选择整组")

    drift_parser = commands.add_parser("drift", help="对比新盘点与登记基线，只报告 ExternalChange")
    drift_parser.add_argument("--inventory", type=Path, required=True)
    drift_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    drift_parser.add_argument("--out", type=Path)

    args = ap.parse_args()
    if args.cmd == "adopt" and not (args.path or args.group):
        ap.error("adopt 至少需要一个 --path 或 --group")
    try:
        if args.cmd == "adopt":
            code = adopt(args.inventory, args.registry, args.path, args.group)
        else:
            code = drift(args.inventory, args.registry, args.out)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(1, f"registry: {exc}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
