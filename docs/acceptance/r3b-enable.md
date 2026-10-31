# R3b 验收记录（第二片）：MCP 启停只改一层

**日期：** 2026-10-31
**范围：** [ROADMAP](../ROADMAP.md) R3b 的第二片：已登记 MCP server 的启停。实现落在
`tools/mcp_toggle.py`（Claude Code 项目启停）、`tools/mcp_config.py`（`claude-toggle` 适配路径、`TOGGLE_ARRAYS`、
`manage_enabled` 渲染与 `render_target_fragment`）、`tools/mcp_span.py`（跨度定位与拼接的共用层，从 `mcp_config.py`
拆出），以及 `tools/personalize_mcp.py` / `personalize_mcp_plan.py` / `personalize_mcp_apply.py` 里启停目标的登记、
计划、应用、journal 与恢复分支。
**base 提交：** `5a7ba99`；实现提交号用 `git log -1 --format=%h` 取。
**结论（文件层）：** 在隔离合成环境里跑通“禁用 → 复盘 → 再启用 → 逆序恢复两次”。Pi 写条目的 `enabled`，Claude
Code 写 `~/.claude.json` 里声明项目键的 `disabledMcpServers`（只增删绑定的名字）；未托管条目、另一个项目键、审批
数组、信任字段与 user 作用域条目始终原样；三份配置最后逐字节回到原始内容，`~/.claude.json` 仍是 `0600`。
**Claude 的格式依据是静态的：** 来自本机安装的 Claude Code 2.1.312 二进制里只读定位到的产品自身说明与读写函数，
**没有在 Host 里实跑 `/mcp disable`**。所以本片对 Claude 只到“按产品代码里的落点与语义写文件”，不声称 Host 会按它
启停。“Host 是否加载条目、能否连上 server、启停有没有收益”仍是 `unknown`。

## 静态格式依据（2026-10-31）

- Pi `1.4.0` 的 `docs/mcp.md`：`mcpServers` 条目支持 `enabled: false`（条目不连接，但保留）；`env` / `headers` 可用
  `${NAME}`；用户级 `~/.pi/agent/mcp.json`，项目级 `.pi/mcp.json`。这一条是官方文档口径。
- Claude Code 2.1.312（本机安装产物的只读定位）：`/mcp disable` 把 server 名追加进 `~/.claude.json` 的
  `projects[<项目键>].disabledMcpServers`，`/mcp enable` 删掉该名；缺这个列表等价于空列表；项目键是启动时工作目录的
  路径字符串（NFC 规范化，不做 realpath）；键不在 `projects` 里时会回落到默认项目条目，改动静默无效。所以本片只按
  调用方显式给出的项目键读写，键不存在就报错，不回落、不创建项目条目。
- 这不是同一层的三件事：项目 `.mcp.json` 的审批（`disabledMcpjsonServers` / `enabledMcpjsonServers`）、信任
  （`hasTrustDialogAccepted`）与默认关闭的内置 server，都不是本片的开关；用它们冒充启停会把“批准 / 信任”改坏。
- 依据边界：静态读到的落点与语义能让实现可检查（写哪个数组、增删什么、缺列表怎么算），但不能证明 Host 的运行时行为。

## 契约要点与实现位置

| 契约 | 实现 | 证据 |
|---|---|---|
| 启停只改这一层：Pi 条目 `enabled`，Claude 项目启停数组里的这个名字 | `mcp_config.render_target_entry(manage_enabled)`、`mcp_toggle.toggle_edit` | 三份配置的 `cmp` 与 JSON 断言；预览里的 `claude-toggle` 渲染差异 |
| 项目键必须是文件里已有的键，不回落、不新建项目条目 | `mcp_toggle.project_object` | `test_unknown_project_key_is_refused_at_adopt`：`mcp-adopt` 退出 1，报 `项目键`，登记表不落地 |
| 只增删绑定的名字，其他元素与外围字节不动 | `mcp_toggle._removal_span`、插入片段与 `mcp_span.splice` | `test_existing_entries_are_preserved_and_redundant_disable_writes_nothing`：`legacy-disabled` 保留、另一个项目键的同名项不动 |
| 已经是目标状态时不写字节 | `mcp_toggle.toggle_edit` 的 `change` | 同一用例里“已禁用时禁用补丁不改 `~/.claude.json` 字节” |
| 多目标计划不能跳过 Claude 还把定义标成一致 | `personalize_mcp_plan.unsupported_patch_reason` | `test_claude_entry_without_a_toggle_target_blocks_the_whole_group`：整组 `not_applicable`，`apply` 退出 1 |
| 普通参数更新不动启停，只有启停补丁改它 | `mcp_toggle.managed_edit`（`manage_enabled` 为 False 时返回零长度跨度与空替换）、`personalize_mcp_plan.target_row` / `personalize_mcp_apply.prepare_target` | `test_plain_args_update_without_enabled_in_the_definition_keeps_all_three_targets`（定义没有 `enabled`：预览 / 应用退出 0，`~/.claude.json` 字节不变）与 `test_plain_args_update_keeps_a_hand_switched_toggle`（手动启用后普通更新不再禁回去） |
| 定义声明启停却没有 `claude-toggle` 绑定：报 `toggle_unbound`，不报整体同步 | `personalize_mcp.target_state` 的 `toggle_bound`（`has_toggle_target` 从同一资产的绑定算出）、`DRIFT_STATES` | `test_adopt_accepts_a_disabled_pi_entry_as_the_first_target`：登记与 `mcp-drift` 都是 `["in_sync", "toggle_unbound"]`，字段 `["enabled"]`；普通更新仍可执行，改启停仍整组 `not_applicable` |
| 删启停数组首项保留下一项的换行与缩进 | `mcp_toggle._removal_span` 的首项分支（删到后面的逗号为止） | `test_removing_the_first_name_keeps_the_next_entry_layout_and_restores_bytes`：`["demo",` 换行缩进 `"legacy-disabled"]` 启用 `demo` 后是 `[` 换行缩进 `"legacy-disabled"]`，恢复后逐字节回到原文件 |
| 第一片的 MCP 恢复计划（`entries[]` 没有 `span`）仍可执行；启停恢复必须带跨度 | `personalize_mcp_apply.plan_restore_span` | `tests/test_personalize_mcp_legacy.py`：旧计划由 `5a7ba99` 生成，条目目标按当前绑定定位、内容变了仍拒绝；`test_toggle_restore_plan_without_its_span_is_refused` 报 `启停跨度` |
| 预览后正常编辑 `~/.claude.json`（哪怕别的项目键）就要重新预览 | `personalize_mcp_apply.prepare_target` 比整份文件 sha256 | `test_editing_claude_home_after_preview_invalidates_the_approval`：退出 1，报 `在预览之后变了`，账本无新事实 |
| 不备份整份 `~/.claude.json`，只记这个片段与两侧 sha256 | `personalize_mcp_apply.write_journal` 的 `mcp_fragments` 行 | journal 里 `before_fragment == ""`、`after_fragment == ', "disabledMcpServers": ["demo"]'`；PHE_HOME 里搜不到别的名字 |
| 恢复按 journal 的编辑方向反推跨度 | `mcp_toggle.toggle_restore_span` | 逆序恢复后回到“没有这个列表”的原始字节 |
| 漂移按目标分开报 | `mcp_toggle.toggle_drift`（定义没写 `enabled` → `not_managed`） | `test_external_edits_are_reported_per_target`：手改启停列表后只有 `claude-toggle` 是 `drifted`，字段 `["enabled"]` |
| Pi 项目级“只有 `enabled`”的覆盖不假装完整支持 | `mcp_config.definition_from_entry` 的既有规则 | `test_pi_project_enabled_only_override_is_unsupported`：报 `只有 enabled 的项目级覆盖` |

## 环境与隔离

- macOS；`python3` 3.15.0（只用标准库，无新依赖）。
- `$WORK` 由 `mktemp -d` 生成并先 `pwd -P` 归一化（macOS 的 `/tmp` 是软链）。所有命令从 `$WORK/elsewhere` 跑，仓库只被
  复制读取，写入都落在 `$WORK`。
- `$WORK/home` 是**合成** Host 根：`~/.pi/agent/mcp.json` 与 `~/.claude.json`；`$WORK/project` 是合成项目根
  （`.mcp.json`，同时充当 Claude 的项目键）；`$WORK/phe` 是仓库外的 PHE_HOME。真实 Host 配置、会话、凭证都没有读。
- 合成 `~/.claude.json` 里放了三个“不该被动”的东西：同一个文件里另一个项目键的 `disabledMcpServers`（含同名
  `demo`）、声明项目键的 `disabledMcpjsonServers`（审批）与 `hasTrustDialogAccepted`（信任）、user 作用域
  `mcpServers`。脚本断言它们在每一步之后都不变，并确认这些名字没有进任何 PHE 产物。

## 合成输入与命令序列

第一块：写三份合成配置、登记三个目标（`pi` / `claude` / `claude-toggle` + 项目键），预览并应用“关掉 demo”。

```bash
REPO=${REPO:?先设成仓库根（含 tools/ 的目录）}
WORK=$(cd "$(mktemp -d)" && pwd -P)
echo "WORK=$WORK"
TOOL="$REPO/tools/personalize.py"
PI="$WORK/home/.pi/agent/mcp.json"
CLAUDE="$WORK/project/.mcp.json"
CLAUDE_HOME="$WORK/home/.claude.json"
PROJECT_KEY="$WORK/project"
mkdir -p "$WORK/home/.pi/agent" "$WORK/project" "$WORK/elsewhere" "$WORK/out"
cat > "$PI" <<'JSON'
{
    "mcpServers": {
        "demo": {
            "command": "npx",
            "args": ["-y", "demo-mcp"],
            "env": {"DEMO_KEY": "${DEMO_KEY}"}
        },
        "legacy-server": {
            "command": "node",
            "args": ["legacy.js"]
        }
    }
}
JSON
cat > "$CLAUDE" <<'JSON'
{
  "mcpServers": {
    "demo": {"command": "npx", "args": ["-y", "demo-mcp"], "env": {"DEMO_KEY": "${DEMO_KEY}"}},
    "unmanaged": {"url": "https://example.invalid/mcp"}
  }
}
JSON
cat > "$CLAUDE_HOME" <<JSON
{
  "projects": {
    "$PROJECT_KEY": {
      "mcpServers": {"global-demo": {"command": "npx", "args": ["global"]}},
      "hasTrustDialogAccepted": true,
      "disabledMcpjsonServers": ["approval-unmanaged"]
    },
    "$WORK/home/elsewhere": {
      "disabledMcpServers": ["demo", "legacy-disabled"]
    }
  },
  "mcpServers": {"user-scope-demo": {"command": "npx"}}
}
JSON
chmod 600 "$CLAUDE_HOME"
cp "$PI" "$WORK/pi.before"
cp "$CLAUDE" "$WORK/claude.before"
cp "$CLAUDE_HOME" "$WORK/home.before"
python3 -I "$REPO/tools/workspace.py" init --phe-home "$WORK/phe" > /dev/null
cd "$WORK/elsewhere"
python3 -I "$TOOL" mcp-adopt --phe-home "$WORK/phe" --server demo \
  --target "pi=$PI" --target "claude=$CLAUDE" \
  --target "claude-toggle=$CLAUDE_HOME" --project-key "$PROJECT_KEY" > "$WORK/out/adopt.json"
python3 - "$WORK" <<'PY'
import hashlib, json, sys
from pathlib import Path

work = Path(sys.argv[1])
definition = work / "phe/assets/asset-0001/content/server.json"
report = work / "out/report.md"
report.write_text("# 合成报告\n\n- 观察：合成场景里 demo 需要先关掉，条目本身不变\n", encoding="utf-8")


def package(before: str, after: str, item_id: str) -> dict:
    import difflib

    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/server.json",
            tofile="b/server.json",
        )
    )
    return {
        "schema_version": 1,
        "report": {"path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()},
        "items": [
            {
                "item_id": item_id,
                "kind": "user_preference",
                "evidence": ["合成证据：合成场景要求先关掉这个 server，再说要不要开回来"],
                "prediction": {
                    "why": "合成场景要求 demo 暂时不连接",
                    "expected": "Pi 条目写 enabled: false；Claude 把 demo 记进声明项目键的 disabledMcpServers",
                    "verify": "读三份配置；Host 是否加载、能否连上记 unknown",
                },
                "target": {"asset_id": "asset-0001"},
                "patch": patch,
            }
        ],
    }


before = definition.read_text(encoding="utf-8")
disabled = before.replace('  "env": {', '  "enabled": false,\n  "env": {', 1)
assert disabled != before and '"enabled": false' in disabled
(work / "out/change-disable.json").write_text(
    json.dumps(package(before, disabled, "P1"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY
python3 -I "$TOOL" preview --phe-home "$WORK/phe" --change "$WORK/out/change-disable.json" --cycle c1 > /dev/null
python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path

work = Path(sys.argv[1])
plan = json.loads((work / "phe/reports/c1/plan.json").read_text(encoding="utf-8"))
item = plan["items"][0]
assert item["state"] == "applicable", item.get("reason")
assert [row["adapter"] for row in item["targets"]] == ["pi", "claude", "claude-toggle"], item["targets"]
toggle = item["targets"][2]
assert toggle["project_key"] == str(work / "project"), toggle
assert toggle["state"] == "applicable", toggle
host_writes = plan["host_writes"]
assert str(work / "home/.claude.json") in host_writes, host_writes
for path in (work / "phe/reports/c1/preview.md", work / "phe/reports/c1/plan.json"):
    text = path.read_text(encoding="utf-8")
    for needle in ("legacy-disabled", "approval-unmanaged", "other-server", "global-demo", "user-scope-demo"):
        assert needle not in text, (path.name, needle)
preview = (work / "phe/reports/c1/preview.md").read_text(encoding="utf-8")
assert "项目键" in preview and "disabledMcpServers" in preview
sha = __import__("hashlib").sha256((work / "phe/reports/c1/plan.json").read_bytes()).hexdigest()
approval = {
    "schema_version": 1,
    "approval_id": "a1",
    "source": "user",
    "plan_sha256": sha,
    "items": ["P1"],
    "note": "合成批准：先关掉 demo",
}
(work / "out/approval.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 -I "$TOOL" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c1/plan.json" \
  --item P1 --approval "$WORK/out/approval.json" > "$WORK/out/apply.json"
python3 - "$WORK" <<'PY'
import json, os, sys
from pathlib import Path

work = Path(sys.argv[1])
pi = json.loads((work / "home/.pi/agent/mcp.json").read_text(encoding="utf-8"))
assert pi["mcpServers"]["demo"]["enabled"] is False, pi["mcpServers"]["demo"]
assert pi["mcpServers"]["legacy-server"] == {"command": "node", "args": ["legacy.js"]}, pi
claude = json.loads((work / "project/.mcp.json").read_text(encoding="utf-8"))
assert "enabled" not in claude["mcpServers"]["demo"], claude["mcpServers"]["demo"]
assert (work / "project/.mcp.json").read_bytes() == (work / "claude.before").read_bytes()
home = json.loads((work / "home/.claude.json").read_text(encoding="utf-8"))
bucket = home["projects"][str(work / "project")]
assert bucket["disabledMcpServers"] == ["demo"], bucket
assert bucket["disabledMcpjsonServers"] == ["approval-unmanaged"], bucket
assert bucket["hasTrustDialogAccepted"] is True, bucket
assert home["projects"][str(work / "home/elsewhere")]["disabledMcpServers"] == ["demo", "legacy-disabled"], home
assert home["mcpServers"] == {"user-scope-demo": {"command": "npx"}}, home
assert oct(os.stat(work / "home/.claude.json").st_mode & 0o777) == "0o600"
text = (work / "home/.claude.json").read_text(encoding="utf-8")
for needle in ("legacy-disabled", "approval-unmanaged", "global-demo", "user-scope-demo"):
    assert text.count(needle) == 1, needle
PY
python3 -I "$TOOL" mcp-drift --phe-home "$WORK/phe" > "$WORK/out/drift.json"
python3 -I "$TOOL" review --phe-home "$WORK/phe" --execution x0001 --record "$WORK/out/review.json" > /dev/null 2>&1 || true
python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path

work = Path(sys.argv[1])
drift = json.loads((work / "out/drift.json").read_text(encoding="utf-8"))
assert [row["state"] for row in drift["targets"]] == ["in_sync", "in_sync", "in_sync"], drift
ledger = [json.loads(line) for line in (work / "phe/ledger.jsonl").read_text(encoding="utf-8").splitlines()]
journal = json.loads((work / "phe" / ledger[-1]["journals"][0]).read_text(encoding="utf-8"))
toggle = next(row for row in journal["targets"] if row["adapter"] == "claude-toggle")
assert toggle["project_key"] == str(work / "project"), toggle
assert toggle["before_fragment"] == "", toggle
assert toggle["after_fragment"] == ', "disabledMcpServers": ["demo"]', toggle
for path in (work / "phe").rglob("*"):
    if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ("legacy-disabled", "approval-unmanaged", "global-demo", "user-scope-demo", "unmanaged"):
            assert needle not in text, (path.name, needle)
PY
echo "阶段 1 通过：禁用写在三份配置里，未托管条目、审批、信任与另一个项目键原样"

```

第二块：复盘这次禁用、把 `enabled` 改回 `true` 再启用，然后按逆序恢复两次（先撤销启用，再撤销禁用），最后逐字节核对。

```bash
python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path

work = Path(sys.argv[1])
record = {
    "verdict": "observe",
    "prediction_met": "unknown",
    "reason": "合成复盘：只核对到三份配置里的托管跨度变了；Host 是否按这个列表启停未验证",
    "observations": ["Pi 条目写了 enabled: false", "声明项目键的 disabledMcpServers 只多了 demo"],
    "next": "按 prediction.verify 核对；没有行为证据前记 unknown",
}
(work / "out/review.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 -I "$TOOL" review --phe-home "$WORK/phe" --execution x0001 --record "$WORK/out/review.json" > /dev/null
python3 - "$WORK" <<'PY'
import difflib, hashlib, json, sys
from pathlib import Path

work = Path(sys.argv[1])
definition = work / "phe/assets/asset-0001/content/server.json"
report = work / "out/report.md"
before = definition.read_text(encoding="utf-8")
after = before.replace('  "enabled": false,\n', '  "enabled": true,\n', 1)
assert after != before, before
patch = "".join(
    difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile="a/server.json", tofile="b/server.json"
    )
)
package = {
    "schema_version": 1,
    "report": {"path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()},
    "items": [
        {
            "item_id": "P2",
            "kind": "user_preference",
            "evidence": ["合成证据：合成场景要求把这个 server 开回来"],
            "prediction": {
                "why": "合成场景不再需要关着它",
                "expected": "Pi 条目去掉 enabled；Claude 的声明项目键删掉 demo",
                "verify": "读三份配置；Host 是否加载、能否连上记 unknown",
            },
            "target": {"asset_id": "asset-0001"},
            "patch": patch,
        }
    ],
}
(work / "out/change-enable.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 -I "$TOOL" preview --phe-home "$WORK/phe" --change "$WORK/out/change-enable.json" --cycle c2 > /dev/null
python3 - "$WORK" <<'PY'
import hashlib, json, sys
from pathlib import Path

work = Path(sys.argv[1])
plan = work / "phe/reports/c2/plan.json"
item = json.loads(plan.read_text(encoding="utf-8"))["items"][0]
assert item["state"] == "applicable", item.get("reason")
approval = {
    "schema_version": 1,
    "approval_id": "a2",
    "source": "user",
    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    "items": ["P2"],
    "note": "合成批准：把 demo 开回来",
}
(work / "out/approval-enable.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 -I "$TOOL" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c2/plan.json" \
  --item P2 --approval "$WORK/out/approval-enable.json" > /dev/null
python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path

work = Path(sys.argv[1])
pi = json.loads((work / "home/.pi/agent/mcp.json").read_text(encoding="utf-8"))
assert "enabled" not in pi["mcpServers"]["demo"], pi["mcpServers"]["demo"]
home = json.loads((work / "home/.claude.json").read_text(encoding="utf-8"))
assert home["projects"][str(work / "project")]["disabledMcpServers"] == [], home
assert home["projects"][str(work / "home/elsewhere")]["disabledMcpServers"] == ["demo", "legacy-disabled"], home
PY
echo "阶段 2 通过：再启用只删掉这个名字，另一个项目键与未托管条目原样"
python3 -I "$TOOL" restore-preview --phe-home "$WORK/phe" --execution x0002 --cycle c3 > /dev/null
python3 - "$WORK" <<'PY'
import hashlib, json, sys
from pathlib import Path

work = Path(sys.argv[1])
plan = work / "phe/reports/c3/restore-x0002.json"
assert plan.is_file(), plan
approval = {
    "schema_version": 1,
    "approval_id": "a3",
    "source": "user",
    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    "items": ["P2"],
    "execution": "x0002",
    "note": "合成批准：撤销这次启用",
}
(work / "out/approval-restore-enable.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
PY
python3 -I "$TOOL" restore --phe-home "$WORK/phe" --execution x0002 \
  --plan "$WORK/phe/reports/c3/restore-x0002.json" --approval "$WORK/out/approval-restore-enable.json" > /dev/null
python3 -I "$TOOL" restore-preview --phe-home "$WORK/phe" --execution x0001 --cycle c4 > /dev/null
python3 - "$WORK" <<'PY'
import hashlib, json, sys
from pathlib import Path

work = Path(sys.argv[1])
plan = work / "phe/reports/c4/restore-x0001.json"
assert plan.is_file(), plan
approval = {
    "schema_version": 1,
    "approval_id": "a4",
    "source": "user",
    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    "items": ["P1"],
    "execution": "x0001",
    "note": "合成批准：撤销这次禁用",
}
(work / "out/approval-restore-disable.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
PY
python3 -I "$TOOL" restore --phe-home "$WORK/phe" --execution x0001 \
  --plan "$WORK/phe/reports/c4/restore-x0001.json" --approval "$WORK/out/approval-restore-disable.json" > /dev/null
cmp "$WORK/home/.claude.json" "$WORK/home.before"
cmp "$WORK/home/.pi/agent/mcp.json" "$WORK/pi.before"
cmp "$WORK/project/.mcp.json" "$WORK/claude.before"
python3 - "$WORK" <<'PY'
import json, os, stat, sys
from pathlib import Path

work = Path(sys.argv[1])
home = json.loads((work / "home/.claude.json").read_text(encoding="utf-8"))
bucket = home["projects"][str(work / "project")]
assert "disabledMcpServers" not in bucket, bucket
assert bucket == {
    "mcpServers": {"global-demo": {"command": "npx", "args": ["global"]}},
    "hasTrustDialogAccepted": True,
    "disabledMcpjsonServers": ["approval-unmanaged"],
}, bucket
assert stat.S_IMODE(os.stat(work / "home/.claude.json").st_mode) == 0o600
PY
python3 -I "$TOOL" mcp-drift --phe-home "$WORK/phe" > "$WORK/out/drift-restored.json"
python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path

work = Path(sys.argv[1])
drift = json.loads((work / "out/drift-restored.json").read_text(encoding="utf-8"))
assert [row["state"] for row in drift["targets"]] == ["in_sync", "in_sync", "in_sync"], drift
ledger = [json.loads(line) for line in (work / "phe/ledger.jsonl").read_text(encoding="utf-8").splitlines()]
kinds = [row["kind"] for row in ledger]
assert kinds == ["approval", "apply", "review", "approval", "apply", "restore", "restore"], kinds
print("账本事实：", " → ".join(kinds))
PY
echo "阶段 3 通过：逆序恢复两次，三份配置逐字节回到原始内容，权限位仍是 0600"
echo "WORK=${WORK}（核对后自行删除：chmod -R u+w \"$WORK\" && rm -rf \"$WORK\"）"

```

## 实测记录（机械提取，路径已脱敏）

登记与漂移：三个目标登记成功，逻辑定义里没有 `enabled`，所以启停目标报 `not_managed`；两个条目目标报 `in_sync`。

```text
$ python3 -I $TOOL mcp-adopt --phe-home $WORK/phe --server demo \
    --target pi=$WORK/home/.pi/agent/mcp.json --target claude=$WORK/project/.mcp.json \
    --target claude-toggle=$WORK/home/.claude.json --project-key $WORK/project
"asset_id": "asset-0001", targets: pi/in_sync、claude/in_sync、claude-toggle/not_managed
exit=0
$ python3 -I $TOOL mcp-drift --phe-home $WORK/phe
pi in_sync []、claude in_sync []、claude-toggle not_managed []
exit=0
```

预览：启停目标进了 `host_writes`，条目渲染差异里多一行项目键与数组名，插入片段只有绑定的这个名字。

```text
$ python3 -I $TOOL preview --phe-home $WORK/phe --change $WORK/out/change-disable.json --cycle c1
"host_writes": ["$WORK/home/.pi/agent/mcp.json", "$WORK/project/.mcp.json", "$WORK/home/.claude.json"]
items[0].state=applicable，targets=[pi user, claude project, claude-toggle user]
exit=0
--- preview.md 节选 ---
| claude-toggle | user | `$WORK/home/.claude.json` | `demo` · 项目启停（`disabledMcpServers`） |
--- 项目启停：项目键 $WORK/project 的 disabledMcpServers
+++ 应用后
-没有这个名字（启用）；原本没有这个列表
+有这个名字（禁用）；原本没有这个列表
 插入片段：`, "disabledMcpServers": ["demo"]`
```

应用：三份文件里只有托管跨度变了；Claude 项目级 `.mcp.json` 的条目渲染结果与原来一致，所以字节完全没动。

```text
$ python3 -I $TOOL apply ... --item P1 --approval $WORK/out/approval.json
"execution_id": "x0001", "release": "r0001", "journal": "journal/20261031T083603Z-mcp.json"
exit=0
pi: demo 条目 enabled=false；legacy-server 原样
claude: .mcp.json 字节与应用前一致（cmp 通过）
~/.claude.json: 声明项目键 disabledMcpServers=["demo"]；disabledMcpjsonServers、hasTrustDialogAccepted、
                另一个项目键的 ["demo", "legacy-disabled"] 与 user 作用域条目原样；mode=0600
journal 目标：pi span=[38,166]、claude span=[32,114]、
claude-toggle span=[281,281] before="" after=', "disabledMcpServers": ["demo"]'
```

再启用与逆序恢复：启用只删掉这个名字，两次恢复后三份配置与原始字节逐字节相同。

```text
$ python3 -I $TOOL preview ... --cycle c2（enabled: true）
items[0].state=applicable
$ python3 -I $TOOL apply ... --item P2 --approval $WORK/out/approval-enable.json
exit=0；Pi 条目不再有 enabled 字段；声明项目键 disabledMcpServers=[]
$ python3 -I $TOOL restore-preview --phe-home $WORK/phe --execution x0002 --cycle c3
$ python3 -I $TOOL restore --phe-home $WORK/phe --execution x0002 --plan ... --approval ...
$ python3 -I $TOOL restore-preview --phe-home $WORK/phe --execution x0001 --cycle c4
$ python3 -I $TOOL restore --phe-home $WORK/phe --execution x0001 --plan ... --approval ...
exit=0（两次）
cmp 三份配置：全部与 *.before 逐字节一致
~/.claude.json：声明项目键又回到没有 disabledMcpServers；mode=0600
账本事实： approval → apply → review → approval → apply → restore → restore
drift：pi in_sync、claude in_sync、claude-toggle in_sync
```

## 红 → 绿

先写用例再实现：`tests/test_personalize_mcp_toggle.py`（8 项）与 `tests/test_personalize_mcp_review.py` 里改写的两项
放进 base `5a7ba99` 的导出树（只把这些测试文件放进 base 的副本，产品入口仍是第一片）跑：

```text
python3 -m unittest discover -s tests -t tests -k test_personalize_mcp_toggle
→ Ran 8 tests, FAILED (failures=7)
  6 项失败于 mcp-adopt 退出 2：`unrecognized arguments: --target claude-toggle=…` / `--project-key …`
  1 项（Claude 条目没有启停目标）失败于 reason 仍是 `首片不支持改启停（enabled）`
  1 项通过：Pi 项目级“只有 enabled”的覆盖本来就报不支持
python3 -m unittest discover -s tests -t tests -k test_enabled_change_without_a_claude_toggle_target
→ Ran 1 test, FAILED (failures=1)：`claude 的 .mcp.json 条目没有 enabled 字段…本片不写那个文件`
python3 -m unittest discover -s tests -t tests -k test_adopt_accepts_a_disabled
→ Ran 1 test, FAILED (failures=1)：同一句拒绝消息（Pi 条目带 enabled: false 时先登记就被拒）
```

加回实现后同一份用例全绿：`-k test_personalize_mcp_toggle` → 8 tests OK，`-k test_personalize_mcp` → 23 tests OK。

## 第 1 轮审查修复（红 → 绿）

第 1 轮独立审查报了 2 项 P1 与 2 项同源 P2，主控与 advisor 裁定四项都修（它们分别是已批准的启停补丁的行为、
字节口径与兼容性契约，不是新增防护）。还是先写真实 CLI 用例（`tests/test_personalize_mcp_toggle.py` 三组、
`tests/test_personalize_mcp_review.py` 改一项、新增 `tests/test_personalize_mcp_legacy.py`），在候选内容上跑出 6 项
失败，再做最小修复：

1. **普通参数更新不读定义里可能缺的 `enabled`（P1）。** `claude-toggle` 目标在预览与执行两处都无条件读
   `definition["enabled"]`。定义没有这个字段时，只改 `args` 的普通更新直接退出 1；定义写着 `enabled: false` 而用户
   在 Claude 里手动启用过时，普通更新会把它重新禁回去。修法：两处统一走 `mcp_toggle.managed_edit`，`manage_enabled`
   为 False 时只返回零长度跨度与空替换，计划仍绑定整份文件与前后的片段 sha256，journal 与启停列表都不写字节。
2. **定义声明启停、却没有 `claude-toggle` 绑定时不能报成同步（P1）。** Pi 首目标带 `enabled: false`、只要
   pi + claude 两个目标时，`mcp-adopt` 与 `mcp-drift` 都把 Claude 报成 `in_sync`。修法：`target_state` 加
   `toggle_bound`（由 `has_toggle_target` 从同一资产的绑定算出），定义声明了 `enabled` 而 Claude 条目没有对应启停目标时
   报 `toggle_unbound` 与字段 `["enabled"]`；登记与普通参数更新仍可执行，改启停的补丁仍整组 `not_applicable`。
3. **删启停数组首项不能吞掉下一项的排版（P2）。** 修前删除跨度一直延伸到第二项值的起点，
   `["demo",` 换行缩进 `"legacy-disabled"]` 启用 `demo` 后变成 `["legacy-disabled"]`。修法：首项只删到它后面的逗号
   为止，其余元素与外围字节不动；批准后恢复仍逐字节回到原文件。
4. **第一片生成并批准的 MCP 恢复计划要能继续执行（P2）。** 新代码在 `restore` 里强制读计划条目的 `span`，而
   `5a7ba99` 的恢复计划没有这个字段，升级后执行旧计划报 `personalize: 'span'`。修法：新增 `plan_restore_span`，计划
   记了 `span` 就用记下的；旧计划的条目目标按当前绑定定位运行时跨度；启停目标仍必须带显式 `span`，缺了就拒绝。
   通用批准 / PHE_HOME / 执行 ID / 逆序 / journal / 两侧 sha256 的核对顺序与内容变化的拒绝都不变；执行不修改计划或
   批准字节。旧计划夹具见 `tests/legacy_mcp_restore/`（由 `5a7ba99` 真实生成，只做 `@ROOT@` / `@JOURNAL@` 两处
   替换；路径变了计划字节就变了，所以批准按替换后的字节重写，不是原字节重放）。

修前的红（`-k test_personalize_mcp`）：

```text
Ran 29 tests, FAILED (failures=6)
- test_plain_args_update_without_enabled_in_the_definition_keeps_all_three_targets：preview 退出 1，`personalize: 'enabled'`
- test_plain_args_update_keeps_a_hand_switched_toggle：应用后 disabledMcpServers 又从 [] 变回 ["demo"]
- test_adopt_accepts_a_disabled_pi_entry_as_the_first_target：['in_sync', 'in_sync'] != ['in_sync', 'toggle_unbound']
- test_removing_the_first_name_keeps_the_next_entry_layout_and_restores_bytes：应用后是 ["legacy-disabled"]，下一项前的换行与缩进被吞掉
- test_first_slice_restore_plan_still_restores_both_entries：restore 退出 1，`personalize: 'span'`
- test_toggle_restore_plan_without_its_span_is_refused：同样报 `personalize: 'span'`，不是“启停跨度”
```

修后：`-k test_personalize_mcp` → 29 tests OK。

## 测试与检查

```text
python3 -m unittest discover -s tests -t tests → Ran 441 tests, OK
python3 -m unittest discover -s tests -t tests -k test_personalize_mcp → Ran 29 tests, OK
python3 -m unittest discover -s tests -t tests -k test_personalize_mcp_toggle → Ran 12 tests, OK
python3 -m unittest discover -s tests -t tests -k test_personalize_mcp_legacy → Ran 2 tests, OK
python3 -m unittest discover -s tests -t tests -k test_workspace → Ran 22 tests, OK
ruff check . → All checks passed
ruff format --check . → 124 files already formatted
```

## 未验证 / 剩余缺口

- **Claude 的启停效果未在 Host 里验证。** 格式依据是二进制的静态定位；没有跑过 `/mcp disable` / `/mcp enable`，也没有
  观察过 Host 是否因为这个数组而不连接。要在一次性隔离 HOME 里实跑一次才能把这一项从 `unknown` 变成已验证。
- Pi 的 `enabled: false` 是官方文档口径，同样**没有**在隔离 Host 里观察“条目不连接”的行为。
- “启用”只表示去掉这一层禁用：审批（`disabledMcpjsonServers`）、信任（`hasTrustDialogAccepted`）、默认关闭的内置
  server、项目未被信任时不读 `.pi/mcp.json` 这些前置都没有动，也没有验证；不保证 Host 会加载或能连上。
- 收益：`unknown`。`review` 只记事实，不判断效果。
- 启停目标的边界：一个资产只登记一个 `claude-toggle` 目标（一个项目键）；同一个 server 在多个项目里各自启停需要多个
  资产或后续切片。项目键要按 Host 当时的工作目录字符串给出，写错（例如软链前后缀不同）时会拒绝，不自动纠正。
- 新增 / 删除条目、改 `server` 标识、Codex `config.toml`、审批与信任开关的写入仍未支持。
- 仓库无类型检查配置，未跑 typecheck。

## 本记录自身的复现

从本文档机械提取全部 `bash` 块、顺序拼接后用 `bash -e` 执行（`REPO` 指向仓库副本，`RUN_CWD` 是无关 cwd）：

```sh
REPO=<仓库副本> RUN_CWD=<无关工作目录> python3 - <临时输出脚本路径> <<'PY'
import os, pathlib, subprocess, sys
fence = "`" * 3
blocks, current = [], None
doc = pathlib.Path(os.environ["REPO"]) / "docs/acceptance/r3b-enable.md"
for line in doc.read_text(encoding="utf-8").splitlines():
    if current is None and line == fence + "bash":
        current = []
    elif current is not None and line == fence:
        blocks.append("\n".join(current))
        current = None
    elif current is not None:
        current.append(line)
script = pathlib.Path(sys.argv[1])
script.write_text("\n".join(blocks) + "\n", encoding="utf-8")
print(f"{len(blocks)} 块 → {script}")
raise SystemExit(subprocess.call(["bash", "-e", str(script)], env=dict(os.environ), cwd=os.environ["RUN_CWD"]))
PY
```

两块 `bash` 在同一个 shell 里顺序执行（第二块依赖第一块定义的 `WORK` / `TOOL` 等变量），块内任一命令失败都会中止。
`plan_sha256`、journal 名与时间戳每次运行都会变，不能当固定断言；断言对象是 `cmp`、JSON 字段与 drift 状态。
工作目录核对后先 `chmod -R u+w` 再删除；完整输出留在仓库外的日志里，脱敏后的关键行已摘到「实测记录」。
