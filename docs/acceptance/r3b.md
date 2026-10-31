# R3b 验收记录（第一片）：MCP 条目级更新的产品闭环

**日期：** 2026-10-31
**范围：** [ROADMAP](../ROADMAP.md) R3b 的第一片：**更新已存在**的 MCP server 条目。实现落在
`tools/mcp_config.py`（条目逻辑定义、Pi / Claude 两个 JSON 适配器、跨度定位与渲染）、`tools/personalize_mcp.py`
（`mcp-adopt` / `mcp-drift`）、`tools/personalize_mcp_plan.py` / `tools/personalize_mcp_apply.py`（预览、应用、片段
journal、恢复），以及 `tools/personalize*.py` 里按资产 `kind` 的分派。
**base 提交：** `72eac32`；实现提交：`75194d3`。
**结论（候选，待主控独立复审）：** 文件层闭环在隔离合成环境里全部通过——登记已存在条目、各 Host 渲染差异进同一
份计划、只批准托管条目、应用只替换 `mcpServers` 下托管成员的值跨度、未托管条目与文件外围字节不变、外部手改会被
`mcp-drift` 报成 `drifted`、`review` 记进同一本账本、恢复后两个配置的字节与权限位回到应用前。**Host 是否加载条目、
能否连上 server、有没有收益都没有验证**，本记录不声称它们。
**本片明确不支持：** 新增 / 删除条目（条目不存在时计划标 `not_applicable`，不创建）；Codex 的
`~/.codex/config.toml`；`enabled` 开关（Claude 的启停不在 `.mcp.json` 的条目里，本片不写 `~/.claude.json`）；
Pi / Claude 之外的 Host；`oauth`、`timeout`、`exposure`、`description`、`headersHelper` 等字段（遇到就报字段名）。

## 官方格式依据（2026-10-31 核对）

- Pi `1.4.0`（`@earendil-works/pi-coding-agent` 的 `docs/mcp.md`）：`mcpServers`；stdio 用 `command` / `args` /
  `env` / `cwd`；HTTP 用 `url` / `headers`；`type` 可选（`stdio` / `http` / `streamable-http`，`sse` 被拒）；
  `env` / `headers` 可用 `${NAME}`，`!command` 必须占满整个值；用户级 `~/.pi/agent/mcp.json`，项目级
  `.pi/mcp.json`（项目配置在项目被信任后才读）。
- Claude Code <https://code.claude.com/docs/en/mcp>：项目级 `.mcp.json` 的 `mcpServers`；stdio 用 `command` /
  `args` / `env`；HTTP 必须 `"type": "http"`（缺 `type` 会被读成 stdio，`streamable-http` 是别名）；`${VAR}` 与
  `${VAR:-default}` 在 `command` / `args` / `env` / `url` / `headers` 里展开；条目没有 `enabled` 字段，启停是按项目
  记在 `~/.claude.json` 的 `disabledMcpServers`——本片不写那个文件，也不假装写了。
- Codex 的 TOML 只有二进制层面的观察，没有对照官方文档核实，因此本片不支持。

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
```text

预览：人可读预览带三行说明（工具按输入生成，不是自动诊断）与两条实际 diff（逻辑定义 + 各 Host 渲染）。

```text
$ python3 -I $TOOL preview --phe-home $TMP/phe --change $TMP/work/change.json --cycle c1
"plan_sha256": "6f2cbb2e3fa7d801871cd8c1ad645ec31b0f2965b9fe1ac49438079acc2a859b"
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
```text

应用：只批准 `P1`；执行记 `x0001`、发布 `r0001`、journal `journal/20261031T070125Z-mcp.json`。

```text
$ python3 -I $TOOL apply ... --item P1 --approval $TMP/work/approval.json
"execution_id": "x0001", "before_sha256": "5adc7e77…", "after_sha256": "8f374c0e…"
exit=0
pi: 托管跨度已更新；未托管条目与外围字节一致；mode=0o644
claude: 托管跨度已更新；未托管条目与外围字节一致；mode=0o600
```text

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
```text

复盘进同一本账本（`review` 事实绑定 `x0001` 与记录 sha256），未托管标记串没有进 PHE_HOME：

```text
$ python3 -I $TOOL review --phe-home $TMP/phe --execution x0001 --record $TMP/work/review.json
"kind": "review", "execution_id": "x0001", "record_sha256": "70e4b408…"
exit=0
--- PHE_HOME 里是否出现未托管标记串 ---
未托管标记串未进入 PHE_HOME 产物
```text

恢复：先 `restore-preview`（绑定 journal、两侧文件 sha256 与恢复片段），再单独批准、再 `restore`。两个文件与原始
字节逐字节相同，Claude 的 `0600` 位保留。

```text
$ python3 -I $TOOL restore-preview ... --cycle c2
"plan_sha256": "800b7460…", "host_writes": ["$TMP/home/.pi/agent/mcp.json", "$TMP/project/.mcp.json"]
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
```text

## 契约要点与实现位置

| 契约 | 实现 | 证据 |
|---|---|---|
| 只替换 `mcpServers` 下托管成员的值跨度，不整份重写 | `mcp_config.server_location` / `splice`（`JSONDecoder.raw_decode` + 小范围扫描） | 上节“未托管条目与外围字节一致”与 `cmp` 逐字节一致 |
| 逻辑定义与绑定显式记 `asset_id` / `adapter` / `config_path` / `server` | `personalize_mcp.register` 写 `asset.json` 的 `mcp.targets` 与登记表 `mcp` 段 | `mcp-adopt` 输出与 `assets/asset-0001/asset.json` |
| 同一套批准、`execution_id`、账本、`status` / `review` | `personalize_plan` / `personalize_apply` / `personalize_restore` 按 `kind=mcp`、`asset_kind=mcp` 分派 | 账本四行事实、`execution_id=x0001` |
| journal 只记托管前后片段、目标与两侧文件 sha256 | `personalize_mcp_apply.write_journal`（`kind=mcp_fragments`） | “含 LEGACY_TOKEN: False” |
| 写前核对整份文件与片段 sha256，变了要求重新预览 | `check_target_current`、`build_restore_plan`、`restore` | `tests/test_personalize_mcp.py` 的 `test_apply_refuses_when_a_host_config_changed_after_preview`、`test_external_edit_is_reported_and_blocks_restore` |
| 凭证只允许引用 | `mcp_config._check_reference` / `_check_url` | `test_adapter_rules_and_credential_field_contract` |

## 红 → 绿

先把同一份 `tests/test_personalize_mcp.py` 放到 base `72eac32` 的导出树里跑（只补 `tools/mcp_config.py`，
产品入口仍是旧版）：9 个用例里 8 个失败，失败原因是
`personalize.py: error: argument cmd: invalid choice: 'mcp-adopt'`；唯一通过的是纯适配器规则单测。加回实现后
同一份用例全绿（`Ran 9 tests … OK`）。

## 测试与检查

```text
python3 -m unittest discover -s tests -t tests → Ran 421 tests, OK
python3 -m unittest discover -s tests -t tests -k test_personalize_mcp → Ran 9 tests, OK
python3 -m unittest discover -s tests -t tests -k test_workspace → Ran 22 tests, OK
ruff check . → All checks passed
ruff format --check . → 113 files already formatted
```text

## 未验证 / 剩余缺口

- Host 是否加载这个条目、能否连上 server：**未验证**。应用成功只说明配置文件里的托管跨度变了。
- 收益：`unknown`。`review` 只记事实，不判断效果。
- 新增 / 删除条目、Codex TOML、`enabled` 开关的落点（Claude 在 `~/.claude.json`）、Pi 项目级配置需要项目信任这一
  前置：本片不覆盖，也不在预览里假装覆盖。
- 非引用型 `env` / `headers` 字面值、`oauth`、`timeout`、`exposure`、`description`、`headersHelper` 让条目不可托管，
  报字段名而不是脱敏后写回；旧托管片段如果是这些形状，本片同样不支持（不声称能还原原字节）。
- 条目同名但 transport 不同（stdio ↔ http）时，本片按“同一 server 的字段变化”渲染；跨 transport 的语义等价没有
  验证。

## 本记录自身的复现

本文的 bash 块按顺序执行即为本次跑过的序列（每块都从 `$WORK/elsewhere` 调，`REPO` 指向克隆目录）。完整输出留在
仓库外的日志里，脱敏后的关键行已摘到「实测记录」。`plan_sha256`、journal 名与记录 sha256 每次运行都会变，
不能当固定断言；`cmp` 与 drift 的状态行才是断言对象。
