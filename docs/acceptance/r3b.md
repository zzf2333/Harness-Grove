# R3b 验收记录（第一片）：MCP 条目级更新的产品闭环

**日期：** 2026-10-31
**范围：** [ROADMAP](../ROADMAP.md) R3b 的第一片：**更新已存在**的 MCP server 条目。实现落在
`tools/mcp_config.py`（条目逻辑定义、Pi / Claude 两个 JSON 适配器、跨度定位与渲染）、`tools/personalize_mcp.py`
（`mcp-adopt` / `mcp-drift`）、`tools/personalize_mcp_plan.py` / `tools/personalize_mcp_apply.py`（预览、应用、片段
journal、恢复），以及 `tools/personalize*.py` 里按资产 `kind` 的分派。
**base 提交：** `72eac32`；候选实现提交：`75194d3` + `3c26e2b`（存档 tag `archive/r3b-candidate-3c26e2b`）；第 1 轮
审查修复随本次改动入库，用 `git log -1 --format=%h` 取当前提交号。
**结论（候选，待主控第 2 轮复审）：** 候选 `3c26e2b` 的第 1 轮独立审查给了 4 项 P1 与 2 项 P2，都来自正常使用流程，
已按“先跑红、再最小修复”处理（见「第 1 轮审查修复」）。文件层闭环在隔离合成环境里通过——登记已存在条目、各 Host
渲染差异进同一份计划、只批准托管条目、应用只替换 `mcpServers` 下托管成员的值跨度、未托管条目与文件外围字节不变、
外部手改会被 `mcp-drift` 报成 `drifted`、`review` 记进同一本账本、恢复按账本逆序并核对两侧完整文件字节。**Host 是否
加载条目、能否连上 server、有没有收益都没有验证**，本记录不声称它们。
**本片明确不支持：** 新增 / 删除条目（条目不存在时计划标 `not_applicable`，不创建）；改逻辑定义的 `server` 标识（定
位始终用登记的条目名，改标识标 `not_applicable`）；改 `enabled` 启停（普通更新保留每个目标现在写的值与字段有无，
Claude 的启停不在 `.mcp.json` 的条目里，本片不写 `~/.claude.json`）；Codex 的 `~/.codex/config.toml`；Pi / Claude 之外
的 Host；`oauth`、`timeout`、`exposure`、`description`、`headersHelper` 等字段（遇到就报字段名）。

## 官方格式依据（2026-10-31 核对）

- Pi `1.4.0`（`@earendil-works/pi-coding-agent` 的 `docs/mcp.md`）：`mcpServers`；stdio 用 `command` / `args` /
  `env` / `cwd`；HTTP 用 `url` / `headers`；`type` 可选（`stdio` / `http` / `streamable-http`，`sse` 被拒）；
  `env` / `headers` 可用 `${NAME}`，`!command` 必须占满整个值；用户级 `~/.pi/agent/mcp.json`，项目级
  `.pi/mcp.json`（项目配置在项目被信任后才读）。
- Claude Code <https://code.claude.com/docs/en/mcp>：项目级 `.mcp.json` 的 `mcpServers`；stdio 用 `command` /
  `args` / `env`（官方示例也显式写 `"type": "stdio"`，缺 `type` 同样读成 stdio）；HTTP 必须 `"type": "http"`
  （`streamable-http` 是别名）；`${VAR}` 与 `${VAR:-default}` 在 `command` / `args` / `env` / `url` / `headers` 里展
  开；条目没有 `enabled` 字段，启停是按项目记在 `~/.claude.json` 的 `disabledMcpServers`——本片不写那个文件，也不
  假装写了。
- Codex 的 TOML 只有二进制层面的观察，没有对照官方文档核实，因此本片不支持。

## 第 1 轮审查修复（2026-10-31）

候选 `3c26e2b` 的 4 项 P1 与 2 项 P2 都先用会失败的用例或机械检查复现，再改最小代码；没有新增防护、锁、重试或
版本 fallback，旧 `change.py` / `release*.py` 守卫、旧 instructions / Skill 路径与已批准计划的字节绑定都没有回退。
新增用例在 `tests/test_personalize_mcp_review.py`（5 项，覆盖 4 个 P1 的真实 CLI 序列），共享夹具移到
`tests/personalize_mcp_fixture.py`，`tests/test_personalize_mcp.py` 保留原主路径与边界用例。

| 问题 | 复现（失败用例，修前结果） | 修复 |
|---|---|---|
| P1 改逻辑定义的 `server` 会写错条目且无法恢复：计划用 patch 后的名字定位，同文件的未托管条目被改写，journal 仍记登记的 `demo` | `test_server_rename_is_not_applicable_and_never_rewrites_unmanaged_entries`：修前计划是 `applicable`（`'applicable' != 'not_applicable'`），`apply` 会改写同文件的 `other` | `personalize_mcp_plan.unsupported_patch_reason` 在生成目标行前把改 `server` 标识的 patch 标为 `not_applicable`；`target_row` 的定位与 `server` 字段只用登记绑定名 |
| P1 旧恢复计划能覆盖后续应用：MCP 恢复在 `restorable()` 与 journal 对照前就返回，`x1 A→B` / `x2 B→C` / `x3 C→B` 后 `x1` 旧计划的 hash 又对上，可恢复 A 而账本仍记 `x3 applied` | `test_stale_restore_plan_is_refused_and_the_newest_restore_still_works`：修前 `restore` 退出 `0`（`0 != 1`），文件被写回 A | `personalize_apply.cmd_restore` 先调 `personalize_restore.restorable()` 核对逆序、计划 `execution_id` 与 journal，再按 `asset_kind` 分派到 MCP 的逐文件内容核对；复用现有账本推导，不建第二套栈 |
| P1 普通参数更新会删掉 Pi 已有的 `enabled: false`（Claude 目标先登记、其条目没有该字段）：渲染只用逻辑定义，目标本地启停被当成要删的字段 | `test_plain_update_keeps_pi_disabled_and_claude_without_the_field`：修前 adopt 把 Pi 目标报成 `drifted`，应用后 Pi 条目失去 `enabled` | `mcp_config.render_target_entry` 按目标当前条目渲染：显式 `enabled` 原值保留、缺字段就不补；`current_entry` / `drift` / 预览 / 应用 / 登记漂移走同一函数。改 `enabled` 的 patch 由 `unsupported_patch_reason` 标 `not_applicable`（`test_enabled_change_is_not_applicable`） |
| P1 Claude 合法的显式 `"type": "stdio"` 被登记拒绝 | `test_explicit_stdio_type_round_trips_through_the_whole_loop`：修前 `mcp-adopt` 退出 `1`，报 `claude 条目 demo 有本片不支持的字段：type` | `_supported_entry_fields` 承认 `type`，`_check_type` 不再拒绝 Claude stdio 的 `stdio` 值；stdio 条目原本写明 `type` 时渲染保留它。其他未知 transport（如 `sse`）仍拒绝 |
| P2 验收记录的结束围栏写成带 `text` 标注的版本，CommonMark 无法闭合代码块，后续标题与契约表落进代码块 | 机械检查：7 个代码块的结束行都带这个标注 | 改成三个反引号，11 个围栏全部成对闭合；机械提取到 4 个 `bash` 块 |
| P2 ROADMAP 现状矩阵与“仅观察”仍说 MCP 只有文件级观察 | 只读核对 `docs/ROADMAP.md` 的 MCP 行与发现覆盖段 | 矩阵改成第一片的产品入口、验证与未支持，发现覆盖改成 `部分可托管`；与 `workspace.py` 的 `product_partial` 和 AGENTS 一致，整个 R3b 仍未完成 |

## 环境与隔离

- macOS；`python3` 3.15.0（只用标准库，无新依赖）。
- `$WORK` 由 `mktemp -d` 生成，先 `pwd -P` 归一化（macOS 的 `/tmp` 是软链）。所有命令从 `$WORK/elsewhere` 跑。
- `$WORK/home` 是**合成** Host 根（`~/.pi/agent/mcp.json`），不是真实 `~`；`$WORK/project` 是合成项目根
  （`.mcp.json`）；`$WORK/phe` 是仓库外的 PHE_HOME。真实 Host 配置、会话、凭证都没有读。
- 未托管条目的值里放合成标记串 `SYNTHETIC-UNMANAGED-MARKER-7f3a`，用来证明它不进任何 PHE 产物（不是真实密钥，也不是通用
  secret 扫描）。

## 合成输入与命令序列

两份普通 JSON 配置：4 空格缩进的 Pi 用户级文件（`mcpServers` 前后都有外围键），2 空格缩进的 Claude 项目级文件；
两者各有一个托管条目 `demo` 和一个未托管条目 `unmanaged`。

```bash
REPO=${REPO:?先设成仓库根（含 tools/ 的目录）}
WORK=$(cd "$(mktemp -d)" && pwd -P)
echo "WORK=$WORK"
TOOL="$REPO/tools/personalize.py"
mkdir -p "$WORK/home/.pi/agent" "$WORK/project" "$WORK/elsewhere" "$WORK/out"
PI="$WORK/home/.pi/agent/mcp.json"
CLAUDE="$WORK/project/.mcp.json"
cat > "$PI" <<'JSON'
{
    "autoEnableCodemode": false,
    "mcpServers": {
        "demo": {
            "command": "npx",
            "args": ["-y", "demo-mcp"],
            "env": {"DEMO_KEY": "${DEMO_KEY}"}
        },
        "unmanaged": {
            "command": "legacy-server",
            "env": {"LEGACY_TOKEN": "SYNTHETIC-UNMANAGED-MARKER-7f3a"}
        }
    },
    "theme": "synthetic"
}
JSON
cat > "$CLAUDE" <<'JSON'
{
  "mcpServers": {
    "demo": {
      "command": "npx",
      "args": ["-y", "demo-mcp"],
      "env": {"DEMO_KEY": "${DEMO_KEY}"}
    },
    "unmanaged": {
      "command": "legacy-server",
      "env": {"LEGACY_TOKEN": "SYNTHETIC-UNMANAGED-MARKER-7f3a"}
    }
  }
}
JSON
chmod 600 "$CLAUDE"
cp "$PI" "$WORK/pi.orig"; cp "$CLAUDE" "$WORK/claude.orig"
printf '# 合成报告\n\n宿主手写的示例报告，不是自动诊断。\n' > "$WORK/out/report.md"
```

登记已存在的条目：`--target adapter=路径` 只接受 `pi` 取用户级 / 项目级 `mcp.json`、`claude` 取项目级 `.mcp.json`。
`mcp-adopt` 只写 PHE_HOME（登记表、`assets/<id>/asset.json`、`assets/<id>/content/server.json`），不碰 Host。

```bash
cd "$WORK/elsewhere" || exit 1
python3 -I "$REPO/tools/workspace.py" init --phe-home "$WORK/phe"
python3 -I "$TOOL" mcp-adopt --phe-home "$WORK/phe" --server demo \
  --target "pi=$WORK/home/.pi/agent/mcp.json" --target "claude=$WORK/project/.mcp.json"
python3 -I "$TOOL" mcp-drift --phe-home "$WORK/phe"
```

变更包与批准：patch 打的是逻辑定义 `assets/asset-0001/content/server.json`（下面的 diff 由当前定义机械生成）；批准
绑定计划原始字节的 sha256，并只列出 `P1`。

```bash
cd "$WORK/elsewhere" || exit 1
WORK="$WORK" python3 - <<'PY'
import difflib, hashlib, json, os
from pathlib import Path
work = Path(os.environ["WORK"])
definition = sorted((work / "phe/assets").glob("*/content/server.json"))[0]
data = json.loads(definition.read_text(encoding="utf-8"))
data["args"] = data["args"] + ["--verbose"]
after = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
patch = "".join(difflib.unified_diff(definition.read_text(encoding="utf-8").splitlines(keepends=True),
                                     after.splitlines(keepends=True),
                                     fromfile="a/server.json", tofile="b/server.json"))
report = work / "out/report.md"
(work / "change.json").write_text(json.dumps({
    "schema_version": 1,
    "report": {"path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()},
    "items": [{"item_id": "P1", "kind": "user_preference",
               "evidence": ["合成证据：demo 需要 --verbose"],
               "prediction": {"why": "合成场景里 demo 需要加 --verbose",
                              "expected": "P1 生效；未托管条目字节不变",
                              "verify": "读两个配置文件；Host 加载与连接记 unknown"},
               "target": {"asset_id": "asset-0001"}, "patch": patch}],
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(work / "review.json").write_text(json.dumps({
    "verdict": "observe", "prediction_met": "unknown",
    "reason": "合成复盘：只看托管跨度变了；Host 是否加载未验证",
    "observations": ["托管条目已按定义渲染", "未托管条目字节不变"],
    "next": "按 prediction.verify 核对；没有行为证据前记 unknown",
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 -I "$TOOL" preview --phe-home "$WORK/phe" --change "$WORK/change.json" --cycle c1
python3 - "$WORK" <<'PY'
import hashlib, json, sys
from pathlib import Path
work = Path(sys.argv[1])
plan = work / "phe/reports/c1/plan.json"
(work / "approval.json").write_text(json.dumps({
    "schema_version": 1, "approval_id": "approval-c1-P1", "source": "user",
    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(), "items": ["P1"],
    "note": "合成批准：只批准 P1",
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("approval.json 绑定 plan_sha256 =", hashlib.sha256(plan.read_bytes()).hexdigest())
PY
```

应用、条目级 drift、复盘与恢复（全部从 `$WORK/elsewhere` 调）：

```bash
cd "$WORK/elsewhere" || exit 1
python3 -I "$TOOL" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c1/plan.json" \
  --item P1 --approval "$WORK/approval.json"
python3 -I "$TOOL" mcp-drift --phe-home "$WORK/phe"
python3 -I "$TOOL" review --phe-home "$WORK/phe" --execution x0001 --record "$WORK/review.json"
python3 -I "$TOOL" restore-preview --phe-home "$WORK/phe" --execution x0001 --cycle c2
python3 - "$WORK" <<'PY'
import hashlib, json, sys
from pathlib import Path
work = Path(sys.argv[1])
plan = work / "phe/reports/c2/restore-x0001.json"
(work / "approval-restore.json").write_text(json.dumps({
    "schema_version": 1, "approval_id": "approval-restore-x0001", "source": "user",
    "execution": "x0001", "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    "note": "合成恢复批准",
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 -I "$TOOL" restore --phe-home "$WORK/phe" --execution x0001 \
  --plan "$WORK/phe/reports/c2/restore-x0001.json" --approval "$WORK/approval-restore.json"
cmp "$PI" "$WORK/pi.orig" && echo "pi: 字节与原始一致"
cmp "$CLAUDE" "$WORK/claude.orig" && echo "claude: 字节与原始一致"
stat -f 'claude mode=%Lp' "$CLAUDE" 2>/dev/null || stat -c 'claude mode=%a' "$CLAUDE"
```

## 实测记录（机械提取，路径已脱敏）

登记：两个目标都是 `in_sync`，逻辑定义写在 `assets/asset-0001/content/server.json`。

```text
$ python3 -I $TOOL mcp-adopt --phe-home $TMP/phe --server demo --target pi=$TMP/home/.pi/agent/mcp.json --target claude=$TMP/project/.mcp.json
  "asset_id": "asset-0001", "server": "demo", "definition": "$TMP/phe/assets/asset-0001/content/server.json",
  targets: pi/user/in_sync[]、claude/project/in_sync[]
exit=0
```

预览：人可读预览带三行说明（工具按输入生成，不是自动诊断）与两条实际 diff（逻辑定义 + 各 Host 渲染）。

```text
$ python3 -I $TOOL preview --phe-home $TMP/phe --change $TMP/work/change.json --cycle c1
"plan_sha256": "dfe477cd…"
"host_writes": ["$TMP/home/.pi/agent/mcp.json", "$TMP/project/.mcp.json"], "items": ["P1"], "missing": []
exit=0
--- preview.md 节选 ---
- 为什么改：合成场景里 demo 需要加 --verbose
- 改后期待什么：P1 生效；未托管条目字节不变
- 凭什么判断：读两个配置文件；Host 加载与连接记 unknown
--- a/server.json
+++ b/server.json
@@ -6,7 +6,8 @@
   "command": "npx",
   "args": [
     "-y",
-    "demo-mcp"
+    "demo-mcp",
+    "--verbose"
   ],
```

应用：只批准 `P1`；执行记 `x0001`、发布 `r0001`、journal `journal/20261031T072855Z-mcp.json`。

```text
$ python3 -I $TOOL apply ... --item P1 --approval $TMP/work/approval.json
"execution_id": "x0001", "before_sha256": "5adc7e77…", "after_sha256": "8f374c0e…"
exit=0
pi: 托管跨度已更新；未托管条目与外围字节一致；mode=0o644
claude: 托管跨度已更新；未托管条目与外围字节一致；mode=0o600
```

外部手改托管条目后，`mcp-drift` 报 `drifted`（字段 `args`），同一个文件改回去再报 `in_sync`：

```text
$ python3 -I $TOOL mcp-drift --phe-home $PHE_HOME
pi in_sync []
claude drifted ['args']
exit=0
（改回应用后的内容）
pi in_sync []
claude in_sync []
exit=0
```

复盘进同一本账本（`review` 事实绑定 `x0001` 与记录 sha256），未托管标记串没有进 PHE_HOME：

```text
$ python3 -I $TOOL review --phe-home $TMP/phe --execution x0001 --record $TMP/work/review.json
"kind": "review", "execution_id": "x0001", "record_sha256": "70e4b408…"
exit=0
--- PHE_HOME 里是否出现未托管标记串 ---
未托管标记串未进入 PHE_HOME 产物
```

恢复：先 `restore-preview`（绑定 journal、两侧文件 sha256 与恢复片段），再单独批准、再 `restore`。两个文件与原始
字节逐字节相同，Claude 的 `0600` 位保留。

```text
$ python3 -I $TOOL restore-preview ... --cycle c2
"plan_sha256": "df23b077…", "host_writes": ["$TMP/home/.pi/agent/mcp.json", "$TMP/project/.mcp.json"]
exit=0
$ python3 -I $TOOL restore ... --approval $TMP/work/approval-restore.json
"kind": "restore", "execution_id": "x0001", restored_configs: pi / claude
exit=0
pi: 字节与原始一致
claude: 字节与原始一致
claude mode=600
--- 账本事实 ---
approval x0001
apply x0001 mcp
review x0001
restore x0001
--- journal ---
kind mcp_fragments targets [('pi', 'demo'), ('claude', 'demo')]
before_fragment 首行 {
含 LEGACY_TOKEN: False
```

## 契约要点与实现位置

| 契约 | 实现 | 证据 |
|---|---|---|
| 只替换 `mcpServers` 下托管成员的值跨度，不整份重写 | `mcp_config.server_location` / `splice`（`JSONDecoder.raw_decode` + 小范围扫描） | 上节“未托管条目与外围字节一致”与 `cmp` 逐字节一致 |
| 逻辑定义与绑定显式记 `asset_id` / `adapter` / `config_path` / `server` | `personalize_mcp.register` 写 `asset.json` 的 `mcp.targets` 与登记表 `mcp` 段 | `mcp-adopt` 输出与 `assets/asset-0001/asset.json` |
| 同一套批准、`execution_id`、账本、`status` / `review` | `personalize_plan` / `personalize_apply` / `personalize_restore` 按 `kind=mcp`、`asset_kind=mcp` 分派 | 账本四行事实、`execution_id=x0001` |
| journal 只记托管前后片段、目标与两侧文件 sha256 | `personalize_mcp_apply.write_journal`（`kind=mcp_fragments`） | “含 LEGACY_TOKEN: False” |
| 写前核对整份文件与片段 sha256，变了要求重新预览 | `check_target_current`、`build_restore_plan`、`restore` | `tests/test_personalize_mcp.py` 的 `test_apply_refuses_when_a_host_config_changed_after_preview`、`test_external_edit_is_reported_and_blocks_restore` |
| 恢复先核对逆序、执行身份与 journal，再看内容 hash | `personalize_apply.cmd_restore` 调 `personalize_restore.restorable()` 后才分派；`personalize_mcp_apply.restore` 复核两侧 sha256 | `test_stale_restore_plan_is_refused_and_the_newest_restore_still_works`：`x1`/`x2`/`x3` 回到旧字节后，旧计划退出 `1`，最新执行的逆序恢复正常 |
| 普通更新保留目标本地状态（启停、显式 stdio `type`），不改隐式启停 | `mcp_config.render_target_entry` / `current_entry`；登记、预览、应用与 `mcp-drift` 走同一函数 | `test_plain_update_keeps_pi_disabled_and_claude_without_the_field`、`test_explicit_stdio_type_round_trips_through_the_whole_loop`、`test_enabled_change_is_not_applicable` |
| 改 `server` 标识不生成可执行修改，定位只用登记名 | `personalize_mcp_plan.unsupported_patch_reason`、`target_row` | `test_server_rename_is_not_applicable_and_never_rewrites_unmanaged_entries`（同文件 `demo` + `other`，无写入、无标记串泄露） |
| 凭证只允许引用 | `mcp_config._check_reference` / `_check_url` | `test_adapter_rules_and_credential_field_contract` |

## 红 → 绿

先把同一份 `tests/test_personalize_mcp.py` 放到 base `72eac32` 的导出树里跑（只补 `tools/mcp_config.py`，
产品入口仍是旧版）：9 个用例里 8 个失败，失败原因是
`personalize.py: error: argument cmd: invalid choice: 'mcp-adopt'`；唯一通过的是纯适配器规则单测。加回实现后
同一份用例全绿（`Ran 9 tests … OK`）。

第 1 轮审查修复的红：新增的 `tests/test_personalize_mcp_review.py` 5 项放到候选 `3c26e2b` 的同一套源码上跑，
5 项全失败，失败点正是 4 个 P1——计划是 `applicable` 而不是 `not_applicable`（rename、enabled）、`restore` 退出
`0` 而不是 `1`（旧恢复计划）、adopt 把 Pi 目标报成 `drifted`、Claude 显式 stdio 的 `type` 被登记拒绝。修后同一
套用例全绿（`Ran 14 tests … OK`，含原 9 项）。

## 测试与检查

```text
python3 -m unittest discover -s tests -t tests → Ran 426 tests, OK
python3 -m unittest discover -s tests -t tests -k test_personalize_mcp → Ran 14 tests, OK
python3 -m unittest discover -s tests -t tests -k test_workspace → Ran 22 tests, OK
ruff check . → All checks passed
ruff format --check . → 116 files already formatted
```

## 未验证 / 剩余缺口

- Host 是否加载这个条目、能否连上 server：**未验证**。应用成功只说明配置文件里的托管跨度变了。
- 收益：`unknown`。`review` 只记事实，不判断效果。
- 新增 / 删除条目、改 `server` 标识、Codex TOML、`enabled` 开关的落点（Claude 在 `~/.claude.json`）、Pi 项目级配置
  需要项目信任这一前置：本片不覆盖，也不在预览里假装覆盖。普通更新只保留各目标现有的启停，跨目标启停是否一致
  不在本片能力内。
- Claude 显式 `"type": "stdio"` 只在条目原本写明时保留；其他 transport 别名、`sse` 等仍拒绝，也没有按 transport 等价
  改写条目。
- 非引用型 `env` / `headers` 字面值、`oauth`、`timeout`、`exposure`、`description`、`headersHelper` 让条目不可托管，
  报字段名而不是脱敏后写回；旧托管片段如果是这些形状，本片同样不支持（不声称能还原原字节）。
- 条目同名但 transport 不同（stdio ↔ http）时，本片按“同一 server 的字段变化”渲染；跨 transport 的语义等价没有
  验证。

## 本记录自身的复现

从本文档机械提取全部 `bash` 块、顺序拼接后用 `bash -e` 执行（`REPO` 指向仓库副本，`RUN_CWD` 是无关 cwd）：

```sh
REPO=<仓库副本> RUN_CWD=<无关 cwd> python3 - <<'PY'
import os, pathlib, subprocess, sys
fence = "`" * 3
blocks, current = [], None
for line in pathlib.Path("docs/acceptance/r3b.md").read_text(encoding="utf-8").splitlines():
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

4 个 `bash` 块在同一个 shell 里顺序执行，块内任一命令失败都会中止（不是只看末命令退出码）；本次从仓库副本 +
无关 cwd 跑，4 块全 `exit=0`。`WORK` 指向的临时目录在核对后先 `chmod -R u+w` 再删除。完整输出留在仓库外的
日志里，脱敏后的关键行已摘到「实测记录」。`plan_sha256`、journal 名与记录 sha256 每次运行都会变，不能当固定
断言；`cmp` 与 drift 的状态行才是断言对象。
