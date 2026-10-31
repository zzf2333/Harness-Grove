"""R3b 产品入口（MCP 条目）：登记已存在的 server 条目与条目级 drift（标准库）。

范围（R3b）：Pi 的 `mcp.json`（用户级与项目级）与 Claude Code 项目级 `.mcp.json` 里**已经存在**的 server
条目，以及 Claude Code 的项目启停（`~/.claude.json` 的 `projects[<项目键>].disabledMcpServers`）。取值、渲染与
比对规则在 `mcp_config.py` 与 `mcp_toggle.py`；预览 / 应用 / 恢复在 `personalize_mcp_plan.py` 与
`personalize_mcp_apply.py`，它们与这条路共用 R2 的写入范围、计划原始字节批准、`execution_id` 与同一本账本。

`mcp-adopt` 只写 PHE_HOME（登记表、`assets/<id>/asset.json`、`assets/<id>/content/server.json`），不碰 Host：
从第一个**条目目标**的现有条目投影出逻辑定义，其余条目目标只做渲染校验与漂移报告；`claude-toggle` 目标
（需要 `--project-key`）不投影定义，只校验项目键在 `~/.claude.json` 里存在。`mcp-drift` 只读：条目目标按适配器
渲染后比字段名，启停目标比项目启停列表里有没有这个名字。

凭证：不读、不解析 `env` / `headers` 的值，不执行 `!command`；定义里 `env` / `headers` 只接受含 `${NAME}` 的
引用，`url` 不接受 userinfo。
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import change
import mcp_config
import mcp_toggle
import personalize_plan as planning
from change_ledger import read_json, require
from release_deploy import load_sibling, utc_now

registry_tool = load_sibling("registry")
workspace_tool = load_sibling("workspace")

SCHEMA_VERSION = planning.SCHEMA_VERSION
PLAN_KIND = planning.PLAN_KIND
RESTORE_PLAN_KIND = planning.RESTORE_PLAN_KIND
JOURNAL_KIND = "mcp_fragments"
DRIFT_STATES = {
    "in_sync": "与逻辑定义一致",
    "drifted": "与逻辑定义不一致",
    "missing": "配置文件里没有这个条目",
    "unsupported": "条目里有本片不支持的字段或值",
    "not_managed": "逻辑定义没有声明启停（enabled）：这一项不比对",
    "toggle_unbound": "条目参数一致，但这个 Claude 项目没有 claude-toggle 绑定：启停未管理，整体不算同步",
}


# 资产元数据：MCP 的条目绑定记在 asset.json 的 mcp 段（adapter / config_path / server），不靠 agent → release。


def asset_directory(phe: Path, asset_id: str) -> Path:
    return phe / "assets" / asset_id


def metadata(phe: Path, asset_id: str) -> dict:
    path = asset_directory(phe, asset_id) / "asset.json"
    if not path.is_file():
        raise ValueError(f"{asset_id} 没有 asset.json，先用 mcp-adopt 登记")
    data = read_json(path)
    if data.get("kind") != "mcp":
        raise ValueError(f"{asset_id} 是 {data.get('kind')}，不是 MCP 条目资产")
    return data


def target_bindings(meta: dict, asset_id: str) -> list[dict]:
    """绑定的 Host 目标：每一项都带 adapter / config_path / server，启停目标还带项目键。"""
    binding = meta.get("mcp") or {}
    targets = binding.get("targets") or []
    if not targets:
        raise ValueError(f"{asset_id} 的 asset.json 没有 mcp.targets 绑定")
    rows = []
    for target in targets:
        adapter = target.get("adapter")
        if adapter not in mcp_config.ADAPTER_PATHS:
            raise ValueError(f"{asset_id} 的目标 adapter 未支持：{adapter}")
        row = {
            "adapter": adapter,
            "config_path": target["config_path"],
            "scope": target.get("scope"),
            "server": require(binding, "server", f"{asset_id} 的 mcp 绑定"),
        }
        if adapter in mcp_config.TOGGLE_ARRAYS:
            row["project_key"] = require(target, "project_key", f"{asset_id} 的 {adapter} 目标")
        rows.append(row)
    return rows


def definition_file(phe: Path, asset_id: str) -> Path:
    return asset_directory(phe, asset_id) / "content" / mcp_config.DEFINITION_NAME


def load_definition(phe: Path, asset_id: str) -> dict:
    path = definition_file(phe, asset_id)
    return mcp_config.check_definition(json.loads(path.read_text(encoding="utf-8")), str(path))


def sha256_bytes(data: bytes) -> str:
    return change.sha256_bytes(data)


def file_mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def read_config(path: Path) -> tuple[bytes, str]:
    """读一个 JSON 配置文件：调用方只拿文本做有界定位，不整体重写。"""
    if not path.is_file():
        raise ValueError(f"配置文件不存在：{path}")
    data = path.read_bytes()
    return data, data.decode("utf-8")


# 登记：从已存在的条目写出逻辑定义与绑定；只写 PHE_HOME，不碰 Host。


def parse_targets(values: list[str], project_key: str | None) -> list[dict]:
    """`adapter=路径` 列表；`claude-toggle` 目标必须带项目键，且一个资产只登记一个。"""
    targets: list[dict] = []
    for value in values:
        adapter, separator, raw = value.partition("=")
        if not separator or not raw:
            raise ValueError(f"--target 需要 adapter=路径 的形式：{value}")
        if adapter not in mcp_config.ADAPTER_PATHS:
            raise ValueError(f"--target 的 adapter 只能是 {' / '.join(mcp_config.ADAPTER_PATHS)}：{adapter}")
        path = Path(os.path.realpath(raw))
        reason = mcp_config.target_path_reason(adapter, path)
        if reason is not None:
            raise ValueError(reason)
        if any(target["config_path"] == str(path) for target in targets):
            raise ValueError(f"--target 重复：{path}")
        target = {"adapter": adapter, "config_path": str(path), "scope": mcp_config.adapter_scope(adapter, path)}
        if adapter in mcp_config.TOGGLE_ARRAYS:
            target["project_key"] = project_key
        targets.append(target)
    if not targets:
        raise ValueError("至少需要一个 --target adapter=路径")
    toggles = [target for target in targets if target["adapter"] in mcp_config.TOGGLE_ARRAYS]
    if toggles and project_key is None:
        raise ValueError("有 claude-toggle 目标时必须给 --project-key：它是 ~/.claude.json 里项目条目的键")
    if project_key is not None and not toggles:
        raise ValueError("--project-key 只用于 claude-toggle 目标")
    if len(toggles) > 1:
        raise ValueError("本片一个资产只登记一个 claude-toggle 目标")
    if len(toggles) == len(targets):
        raise ValueError("至少需要一个条目目标（pi / claude）：启停目标只写启停，不投影逻辑定义")
    return targets


def existing_asset(registry: dict, server: str, targets: list[dict]) -> dict | None:
    """同一 server 已经登记在同一个 Host 入口时不再新建资产。"""
    for asset in registry["assets"]:
        binding = asset.get("mcp") or {}
        if binding.get("server") != server:
            continue
        paths = {target["config_path"] for target in binding.get("targets") or []}
        if paths & {target["config_path"] for target in targets}:
            return asset
    return None


def project_definition(server: str, targets: list[dict]) -> dict:
    """从第一个条目目标的现有条目投影逻辑定义；其余条目目标只做渲染校验，启停目标校验项目键存在。"""
    definition = None
    for target in targets:
        _data, text = read_config(Path(target["config_path"]))
        if target["adapter"] in mcp_config.TOGGLE_ARRAYS:
            mcp_toggle.project_object(text, target["project_key"])
            continue
        entry, _span, _fragment = mcp_config.entry_at(text, server)
        if definition is None:
            definition = mcp_config.definition_from_entry(target["adapter"], entry, server)
        mcp_config.render_target_entry(target["adapter"], definition, entry)
    if definition is None:
        raise ValueError(f"没有条目目标（pi / claude）能投影 {server} 的逻辑定义")
    return definition


# `target_state` 同时用于登记与 `mcp-drift`，两边口径一致。
def has_toggle_target(targets: list[dict]) -> bool:
    """这个资产有没有 `claude-toggle` 目标：没有的话，定义里的启停就没有对应的写入落点。"""
    return any(target["adapter"] in mcp_config.TOGGLE_ARRAYS for target in targets)


def target_state(target: dict, definition: dict, toggle_bound: bool) -> dict:
    """单个目标相对逻辑定义的漂移：条目目标比渲染后的字段名，启停目标比项目启停列表里有没有这个名字。

    `toggle_bound` 表示这个资产有 `claude-toggle` 目标。Claude 的 `.mcp.json` 条目没有启停字段：定义声明了
    `enabled` 却没有绑定启停目标时，条目参数一致也不报 `in_sync`，改报 `toggle_unbound`。
    """
    _data, text = read_config(Path(target["config_path"]))
    if target["adapter"] in mcp_config.TOGGLE_ARRAYS:
        return mcp_toggle.toggle_drift(target["adapter"], definition, text, target["project_key"], target["server"])
    state = mcp_config.drift(target["adapter"], definition, text)
    if state["state"] == "in_sync" and "enabled" in definition and target["adapter"] == "claude" and not toggle_bound:
        return {**state, "state": "toggle_unbound", "fields": ["enabled"]}
    return state


def register(writer, phe: Path, args: argparse.Namespace, registry_path: Path) -> dict:
    """把已存在的 server 条目登记成一条 MCP 资产，并写出逻辑定义与目标绑定。"""
    targets = parse_targets(args.target, args.project_key)
    definition = project_definition(args.server, targets)
    states = [
        target_state(dict(target, server=args.server), definition, toggle_bound=has_toggle_target(targets))
        for target in targets
    ]
    try:
        registry = registry_tool.read_registry(registry_path)
    except FileNotFoundError:
        registry = {"assets": []}
    clash = existing_asset(registry, args.server, targets)
    if clash is not None:
        raise ValueError(f"{args.server} 已经登记为 {clash['asset_id']}，不重复登记")
    asset_id = registry_tool.next_asset_id(registry)
    at = utc_now()
    binding = {"server": args.server, "targets": [dict(target) for target in targets]}
    registry["assets"].append(
        {
            "asset_id": asset_id,
            "kind": "mcp",
            "resolved_path": targets[0]["config_path"],
            "observed": [
                {
                    "agent": target["adapter"],
                    "path": target["config_path"],
                    "is_symlink": os.path.islink(target["config_path"]),
                    "symlink_target": (
                        os.readlink(target["config_path"]) if os.path.islink(target["config_path"]) else None
                    ),
                }
                for target in targets
            ],
            "baseline": {"kind": "mcp_entry", "server": args.server, "targets": binding["targets"]},
            "mcp": binding,
            "state": "Active",
            "origin": "imported",
            "evidence_status": "unverified",
            "adopted_at": at,
            "inventory_sha256": None,
        }
    )
    writer.write_json(registry_path, registry)
    writer.write_json(
        asset_directory(phe, asset_id) / "asset.json",
        {
            "asset_id": asset_id,
            "kind": "mcp",
            "name": mcp_config.DEFINITION_NAME,
            "source": targets[0]["config_path"],
            "imported_at": at,
            "mcp": binding,
        },
    )
    writer.write_bytes(definition_file(phe, asset_id), mcp_config.definition_text(definition).encode("utf-8"))
    return {
        "asset_id": asset_id,
        "server": args.server,
        "definition": str(definition_file(phe, asset_id)),
        "targets": [
            {
                "adapter": target["adapter"],
                "config_path": target["config_path"],
                "scope": target["scope"],
                "state": state["state"],
                "fields": state["fields"],
            }
            for target, state in zip(targets, states)
        ],
        "note": "登记只写 PHE_HOME；Host 是否加载这个条目没有验证。漂移只报字段名，不读也不打印字段值。",
    }


def cmd_mcp_adopt(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    registry_path = args.registry or phe / workspace_tool.REGISTRY_RELATIVE
    print(json.dumps(register(planning.product_writer(phe), phe, args, registry_path), ensure_ascii=False, indent=2))
    return 0


# 条目级 drift：托管定义按适配器渲染后与宿主当前条目比较，外部手改不会被说成“没变化”。


def resolve_asset(phe: Path, value: str | None) -> str:
    if value is not None:
        return value
    candidates = []
    assets = phe / "assets"
    if assets.is_dir():
        for directory in sorted(assets.iterdir()):
            declaration = directory / "asset.json"
            if declaration.is_file() and read_json(declaration).get("kind") == "mcp":
                candidates.append(read_json(declaration)["asset_id"])
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"有 {len(candidates)} 条 MCP 资产，请用 --asset-id 指定：{candidates}")


def drift_rows(phe: Path, asset_id: str) -> dict:
    meta = metadata(phe, asset_id)
    definition = load_definition(phe, asset_id)
    rows = []
    targets = target_bindings(meta, asset_id)
    bound = has_toggle_target(targets)
    for target in targets:
        result = target_state(target, definition, toggle_bound=bound)
        rows.append(
            {
                "adapter": target["adapter"],
                "config_path": target["config_path"],
                "scope": target["scope"],
                "server": target["server"],
                "state": result["state"],
                "state_note": DRIFT_STATES[result["state"]],
                "fields": result["fields"],
                "reason": result["reason"],
            }
        )
    path = definition_file(phe, asset_id)
    return {
        "phe_home": str(phe),
        "asset_id": asset_id,
        "server": definition["server"],
        "definition": str(path),
        "definition_sha256": change.sha256_file(path),
        "targets": rows,
        "note": "条目级比对：只看托管条目的字段，不按整文件 Hash；不打印字段值，也不证明 Host 加载过。",
    }


def cmd_mcp_drift(args: argparse.Namespace) -> int:
    phe = planning.workspace(args.phe_home)
    print(json.dumps(drift_rows(phe, resolve_asset(phe, args.asset_id)), ensure_ascii=False, indent=2))
    return 0
