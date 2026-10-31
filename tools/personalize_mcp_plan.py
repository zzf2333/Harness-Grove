"""MCP 条目的预览：逻辑定义 patch 与各 Host 渲染差异写进同一份计划（标准库）。

计划形状沿用 `personalize_plan`：`items[]` 带证据、预测、当前 / 期待 sha256 与 diff，另加 `asset.targets`
（adapter / config_path / scope / server，启停目标还带项目键）与每个目标的渲染差异。批准绑定整份计划的原始字节，
所以计划里任何一个 Host 的文件内容变了都要重新预览。
"""

import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import change
import mcp_config
import mcp_span
import mcp_toggle
import personalize_mcp as mcp_asset
import personalize_plan as planning
from asset_content import content_files, content_sha, file_diffs, patch_targets, stage_patch
from release_deploy import utc_now

SCHEMA_VERSION = planning.SCHEMA_VERSION
PLAN_KIND = planning.PLAN_KIND


def render_diff(current: dict, expected: dict) -> str:
    """托管条目在某个 Host 上的渲染差异：值只含引用，所以可以进预览。"""
    import difflib

    old = (json.dumps(current, ensure_ascii=False, indent=2) + "\n").splitlines(keepends=True)
    new = (json.dumps(expected, ensure_ascii=False, indent=2) + "\n").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile="当前条目", tofile="应用后条目"))


def target_row(target: dict, definition: dict, manage_enabled: bool) -> dict:
    """一个绑定目标在预览时的状态：当前内容、渲染后的片段与整份文件的 sha256。"""
    path = Path(target["config_path"])
    server = target["server"]
    row = {
        "adapter": target["adapter"],
        "config_path": str(path),
        "scope": target["scope"],
        "server": server,
    }
    if target.get("project_key"):
        row["project_key"] = target["project_key"]
    try:
        data, text = mcp_asset.read_config(path)
        if target["adapter"] in mcp_config.TOGGLE_ARRAYS:
            # 启停目标：只增删这个项目键下 disabledMcpServers 里绑定的名字。
            edit = mcp_toggle.toggle_edit(
                target["adapter"], text, target["project_key"], server, disabled=not definition["enabled"]
            )
            span, rendered = edit["span"], edit["replacement"]
            drift, diff = [], mcp_toggle.toggle_diff(target["adapter"], edit, target["project_key"])
        else:
            location = mcp_span.server_location(text, server)
            if location is None:
                row.update(state="missing", reason=f"{path} 里没有 mcpServers 条目 {server}")
                return row
            entry, _span, _fragment = mcp_config.entry_at(text, server)
            span = location["span"]
            current = mcp_config.current_entry(target["adapter"], entry, server)
            expected = mcp_config.render_target_entry(target["adapter"], definition, entry, manage_enabled)
            fields = sorted(set(current) | set(expected))
            drift, diff = (
                [name for name in fields if current.get(name) != expected.get(name)],
                render_diff(current, expected),
            )
            rendered = mcp_config.render_target_fragment(
                target["adapter"], definition, entry, text, span, location["column"], location["step"], manage_enabled
            )
            row.update(column=location["column"], step=location["step"])
    except (OSError, ValueError) as error:
        row.update(state="unsupported", reason=str(error))
        return row
    new_text = mcp_span.splice(text, span, rendered)
    row.update(
        state="applicable",
        reason=None,
        current_file_sha256=mcp_asset.sha256_bytes(data),
        current_fragment_sha256=mcp_asset.sha256_bytes(text[span[0] : span[1]].encode()),
        expected_file_sha256=mcp_asset.sha256_bytes(new_text.encode()),
        expected_fragment_sha256=mcp_asset.sha256_bytes(rendered.encode()),
        drift=drift,
        diff=diff,
    )
    return row


def unsupported_patch_reason(base: dict, expected: dict, targets: list[dict]) -> str | None:
    """本片不生成的修改：改 server 标识、把启停从定义里拿掉、以及 Claude 没有可执行的启停目标。

    启停是逻辑定义里管着的字段：Pi 写条目的 `enabled`，Claude 写 `~/.claude.json` 的项目启停列表。只改 Pi、
    却让 Claude 目标保持原样的计划等于把定义标成一致而实际不一致，所以整组标不可执行，不退化成部分执行。
    """
    if expected["server"] != base["server"]:
        return f"首片不支持改 server 标识（{base['server']} → {expected['server']}）：定位始终用登记的条目名"
    if expected.get("enabled") == base.get("enabled"):
        return None
    if "enabled" not in expected:
        return "本片不支持把启停从逻辑定义里拿掉（改成不管理）：请写 enabled: true / false"
    claude_entries = [target for target in targets if target["adapter"] == "claude"]
    toggles = [target for target in targets if target["adapter"] in mcp_config.TOGGLE_ARRAYS]
    if claude_entries and not (len(claude_entries) == 1 and len(toggles) == 1):
        return (
            "Claude 的 .mcp.json 条目没有 enabled 字段，启停要写到 ~/.claude.json 的项目启停列表"
            f"（disabledMcpServers）；本资产有 {len(claude_entries)} 个 Claude 条目目标与 {len(toggles)} 个"
            " claude-toggle 目标：改启停需要恰好一个 Claude 条目目标对应一个 claude-toggle 目标"
        )
    return None


def build_plan(phe: Path, args, package: dict, report: dict, change_sha: str, asset: dict) -> dict:
    asset_id = asset["asset_id"]
    meta = mcp_asset.metadata(phe, asset_id)
    base_file = mcp_asset.definition_file(phe, asset_id)
    base_definition = mcp_asset.load_definition(phe, asset_id)
    base_sha = change.sha256_file(base_file)
    targets = mcp_asset.target_bindings(meta, asset_id)
    items, missing = [], []
    for item in planning.require(package, "items", "变更包"):
        item_id = planning.require(item, "item_id", "变更包条目")
        patch = planning.require(item, "patch", f"条目 {item_id}")
        missing.extend(planning.missing_notes(item_id, item))
        row = {
            "item_id": item_id,
            "evidence": planning.require(item, "evidence", f"条目 {item_id}"),
            "prediction": planning.require(item, "prediction", f"条目 {item_id}"),
            "patch_targets": patch_targets(patch),
            "current_sha256": base_sha,
            "current_files": content_files(base_file),
        }
        try:
            with tempfile.TemporaryDirectory(prefix="phe-mcp-preview-") as directory:
                staged = stage_patch(planning.preview_writer(), Path(directory), patch, base_file)
                expected_definition = mcp_config.check_definition(json.loads(staged.read_text(encoding="utf-8")))
                row.update(
                    expected_sha256=content_sha(staged),
                    expected_files=content_files(staged),
                    definition_diffs=file_diffs(base_file, staged),
                )
        except (OSError, ValueError) as error:
            row.update(state="not_applicable", reason=str(error).splitlines()[0])
            items.append(row)
            continue
        unsupported = unsupported_patch_reason(base_definition, expected_definition, targets)
        if unsupported is not None:
            row.update(state="not_applicable", reason=unsupported)
            items.append(row)
            continue
        manage_enabled = expected_definition.get("enabled") != base_definition.get("enabled")
        rows = [target_row(target, expected_definition, manage_enabled) for target in targets]
        blocked = [f"{target['config_path']}：{target['reason']}" for target in rows if target["state"] != "applicable"]
        row.update(
            state="not_applicable" if blocked else "applicable",
            reason="；".join(blocked) if blocked else None,
            targets=rows,
        )
        items.append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "cycle": args.cycle,
        "created_at": utc_now(),
        "phe_home": str(phe),
        "report": report,
        "change": {"path": str(Path(args.change)), "sha256": change_sha},
        "asset": {
            "asset_id": asset_id,
            "kind": "mcp",
            "resolved_path": str(base_file),
            "shared": len(targets) > 1,
            "server": base_definition["server"],
            "entries": [],
            "targets": [target_binding_row(target) for target in targets],
        },
        "items": items,
        "host_writes": [target["config_path"] for target in targets],
        "phe_writes": [
            f"assets/{asset_id}/content/{mcp_config.DEFINITION_NAME}",
            f"assets/{asset_id}/asset.json",
            "releases/<下一个编号>/ 与 releases/high_water.json",
            "staging/<执行 ID>/",
            "journal/<时间戳>.json（只记托管前后的片段）",
            "ledger.jsonl",
        ],
        "restore": {
            "method": "journal-fragment",
            "note": "恢复把每个 Host 的托管跨度写回应用前的片段（journal 记录），并核对两侧完整文件 sha256；"
            "PHE 的逻辑定义保留应用后的内容，与指令 / Skill 的恢复口径一致",
        },
        "missing": missing,
    }


def target_binding_row(target: dict) -> dict:
    """计划里记录的绑定：启停目标多带项目键（批准计划要能看到写入落点）。"""
    row = {
        "adapter": target["adapter"],
        "config_path": target["config_path"],
        "scope": target["scope"],
        "server": target["server"],
    }
    if target.get("project_key"):
        row["project_key"] = target["project_key"]
    return row


def render_target_header(target: dict) -> str:
    """一个托管目标在预览里的标题：启停目标多一行项目键。"""
    header = f"`{target['adapter']}` · `{target['config_path']}`"
    if target.get("project_key"):
        header += f" · 项目键 `{target['project_key']}` · `{mcp_config.TOGGLE_ARRAYS[target['adapter']]}`"
    return header


def render_preview(plan: dict, plan_sha: str) -> str:
    asset = plan["asset"]
    lines = [
        f"# MCP 条目预览 · 周期 {plan['cycle']}",
        "",
        f"- 生成时间：{plan['created_at']}（工具按输入生成，不是自动诊断）",
        f"- PHE_HOME：`{plan['phe_home']}`",
        f"- 报告：`{plan['report']['path']}` sha256 `{plan['report']['sha256']}`",
        f"- 变更包：`{plan['change']['path']}` sha256 `{plan['change']['sha256']}`",
        f"- 计划 sha256：`{plan_sha}`（批准记录绑定这份计划的原始字节）",
        f"- 条目：`{asset['asset_id']}` · server `{asset['server']}` · 逻辑定义 `{asset['resolved_path']}`",
        (
            "- 写入方式：条目目标只替换各 Host 配置里 `mcpServers` 下这个条目的值跨度；启停目标只增删 "
            "`~/.claude.json` 项目键下启停列表里的这个名字。未托管的 server 与文件外围字节不变。"
        ),
        "",
        "## 托管目标",
        "",
        "| Agent | scope | 配置 | 条目 |",
        "|---|---|---|---|",
    ]
    for target in asset["targets"]:
        label = f"`{target['server']}`"
        if target.get("project_key"):
            label += f" · 项目启停（`{mcp_config.TOGGLE_ARRAYS[target['adapter']]}`）"
        lines.append(f"| {target['adapter']} | {target['scope']} | `{target['config_path']}` | {label} |")
    for item in plan["items"]:
        if item["state"] != "applicable":
            lines.extend(["", f"### {item['item_id']}（当前不适用）", "", f"- {item['reason']}", ""])
            continue
        prediction = item["prediction"] if isinstance(item["prediction"], dict) else {}
        lines.extend(
            [
                "",
                f"### {item['item_id']}",
                "",
                f"- 为什么改：{prediction.get('why', '（未写）')}",
                f"- 改后期待什么：{prediction.get('expected', '（未写）')}",
                f"- 凭什么判断：{prediction.get('verify', '（未写）')}",
                f"- 证据：{item['evidence']}",
                f"- 逻辑定义 sha256：`{item['current_sha256']}` → `{item['expected_sha256']}`",
                "",
                "#### 逻辑定义 diff",
                "",
                "```diff",
                "".join(row["diff"] for row in item["definition_diffs"]).rstrip("\n"),
                "```",
                "",
                "#### 各 Host 渲染差异",
                "",
                "| Agent | 配置 | 当前文件 sha256 | 应用后文件 sha256 | 条目漂移字段 |",
                "|---|---|---|---|---|",
            ]
        )
        for target in item["targets"]:
            fields = "、".join(target["drift"]) or "无（与逻辑定义一致）"
            lines.append(
                f"| {target['adapter']} | `{target['config_path']}` | `{target['current_file_sha256']}` | "
                f"`{target['expected_file_sha256']}` | {fields} |"
            )
        for target in item["targets"]:
            lines.extend(["", render_target_header(target), "", "```diff", target["diff"].rstrip("\n"), "```"])
    lines.extend(["", "## 缺项清单（正常契约，补全后再批准更稳）", ""])
    if plan["missing"]:
        lines.extend(f"- {note['item_id']}：缺 {'、'.join(note['missing'])}" for note in plan["missing"])
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 写入范围",
            "",
            "Host 配置（只这些）：",
            "",
            *[f"- `{path}`" for path in plan["host_writes"]],
            "",
            "PHE_HOME 产物（相对 `<PHE_HOME>`）：",
            "",
            *[f"- `{path}`" for path in plan["phe_writes"]],
            "",
            f"恢复方式：{plan['restore']['note']}。恢复也要单独预览并再次批准。",
            "",
            "## 说明",
            "",
            "- 只支持更新已存在的条目：`mcpServers` 里没有这个 server、或要新增 / 删除条目时，本条目不适用。",
            (
                "- `env` / `headers` 的值必须是含 `${NAME}` 的引用；`url` 不带内联认证。字面值、`oauth`、"
                "`timeout`、`exposure`、`description`、`headersHelper`、`!command` 等本片不支持，遇到就明确报出字段名。"
            ),
            (
                "- 启停只改这一层：Pi 写条目的 `enabled`（`false` 保留条目不连接）；Claude Code 只在指定的项目键下"
                "增删 `disabledMcpServers` 里这个 server 名。`disabledMcpjsonServers` / `enabledMcpjsonServers`"
                "（项目 `.mcp.json` 审批）、`hasTrustDialogAccepted`（信任）与默认关闭的内置 server 不受影响；"
                "启用只表示去掉这条禁用，不保证 Host 会加载或能连上。"
            ),
            "- 应用成功只说明配置文件里的托管跨度变了；Host 是否加载、是否能连上 server 都没有验证。",
        ]
    )
    return "\n".join(lines) + "\n"
