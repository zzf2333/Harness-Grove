# R3a 验收记录：目录型 Skill 的多文件闭环 + 隔离 Host 的发现与显式展开

**日期：** 2026-10-31
**范围：** [ROADMAP](../ROADMAP.md) R3a 的四项缺口：`change.py` 支持 skill 资产（多文件 patch）、更新/复盘/撤销走
同一账本、`~/.agents/skills` 读取者事实、Skill 被发现与显式触发的 Host 层证据。实现落在
`tools/asset_content.py`（共享内容层）、`tools/change.py`（旧沙箱闭环）、`tools/personalize*.py`（产品入口）与
`tools/workspace.py`（发现报告里 Skill 条目的显示）。
**结论：** 验收项在隔离环境里全部通过：**文件层**（预览逐文件 diff、批准、应用、账本、逆序恢复、文件集合与字节）
与**一次隔离 Host 的发现/显式展开**（Pi 1.4.0 的真实 loader 与真实 `session.prompt`）。第 1 轮审查的 3 项 P1 与
1 项 P2 已按“先失败用例、再最小修复”处理，旧 R2 计划仍可执行（见下节）。模型自主选用 Skill、任务
执行效果与收益仍**未验证**；本次证据不改变“没有行为证据就记 `unknown`”的口径。**R3a 完成不等于完整 Harness
完成**：只覆盖 Skills 一类资产，目录 import 仍只在沙箱入口，MCP/Hooks/扩展与工具配置仍是后续；是否完成以主控
第 2 轮复审为准。
**base 提交：** `5000e23`；实现提交随本次改动入库，用 `git log -1 --format=%h` 取当前提交号。

## 第 1 轮审查修复（2026-10-31）

候选 `709a523` 的独立审查给了 3 项 P1 与 1 项 P2，都来自正常使用流程。四项先复现为失败用例，再做最小修复；
没有新增防护、锁、重试或版本 fallback 框架。修复后同一套用例转绿，旧 R2 计划仍可执行。

| 问题 | 复现（失败用例） | 修复 |
|---|---|---|
| P1 失败尝试留下的暂存树会混进下一次发布 | `test_change_skill.py::test_failed_attempt_residue_does_not_leak_into_the_next_release`：应用 P1 → 重复 P1 失败 → 恢复 x0001 → 只批 P2；修复前 r0002 的文件集合多出 `references/extra.md`。产品入口共用同一暂存规则（`test_personalize_skill.py::SkillResidualStagingTests`，直接放入失败尝试会留下的暂存树）修复前退出 1 报“打出来的内容和预览的 diff 不一致”。 | `asset_content.stage_patch` 每次打 patch 前只重建本次执行拥有的暂存根 `staging/<执行 ID>/tree` 或 `staging/<执行 ID>/file`；不清整个 staging、发布或用户数据。 |
| P1 只删除引用文件的 patch 无法预览 | `test_personalize_skill.py::SkillDeleteOnlyTests`：修复前 `preview` 退出 1 报“patch 里没有目标路径”。 | `patch_targets` 按成对 diff 头取路径：`+++` 不是 `/dev/null` 时取它，否则取对应的 `---`；不重写 patch 引擎。 |
| P1 旧 R2 计划升级后失效 | `test_personalize_legacy.py`：用 base `5000e23` 真实生成的 `schema_version=1` 计划/批准执行；修复前 `apply` 报 `'expected_files'`，修好 apply 后 `restore` 报 `'files'`。 | 旧单文件计划继续按 `current_sha256` / `expected_sha256` / 恢复内容 sha 与链接契约执行；账本新增字段（`asset_kind` / `before_files` / `after_files` / `restored_files`）从已核对的内容推导；新计划仍要求整树 `expected_files` / `files` 绑定。已批准计划的字节不改。 |
| P2 单文件资产原名与 patch 文件名不同 | `test_personalize.py::SourceNameTests`：来源 `shared-AGENTS.md`、patch 目标 `config/AGENTS.md`；修复前计划 `expected_files` 与资产内容都叫 `AGENTS.md`。 | 单文件暂存按资产原名保存，patch 路径只用于定位；`write_asset` 用来源文件名写回，预览、`expected_files` 与账本统一用原名。 |

兼容夹具 `tests/legacy_r2/` 保存的是 5000e23 的真实输出：字段集合是旧版原样，不是从新版计划删字段模拟；
生成路径换成 `@ROOT@`、journal 名换成 `@JOURNAL@`，用例按当前临时目录和实际 journal 还原，并重新计算绑定 sha256。
夹具说明见该目录 README。

## 环境与隔离

- macOS；`python3` 3.15.0（只用标准库，无新依赖）；`node` v24.20.0；已安装的 Pi `1.4.0`
  （`@earendil-works/pi-coding-agent` 的 `package.json` 里 `version`）。
- `$WORK`：`mktemp -d` 出来的临时目录。macOS 的 `/tmp` 是指向 `/private/tmp` 的软链，先 `pwd -P` 归一化，否则
  登记表的 `resolved_path` 与 `adopt --group` 对不上（本次第一次跑就在这里失败：`盘点里没有该 path 或分组`）。
- `$SRC`：仓库**副本**；所有命令从 `$WORK/elsewhere`（无关 cwd）跑，不依赖仓库里的相对路径。
- `$WORK/home`：合成 Host 根（`~/.pi/agent/skills` 与共享的 `~/.agents/skills`）；`$WORK/skill/demo`：合成 Skill
  来源目录；`$WORK/phe`：仓库外的 PHE_HOME。
- Host 探针子进程的环境由脚本显式给出：`HOME=$WORK/home`、`PI_CODING_AGENT_DIR=$WORK/home/.pi/agent`、
  cwd=`$WORK/elsewhere`、`PI_OFFLINE=1`、`PI_SKIP_VERSION_CHECK=1`、`PI_TELEMETRY=0`；父进程的 `PI_*` 会话变量
  不带进去。没有读取或比较开发者本机的 `~/.pi`、`~/.agents`、`~/.claude`、`~/.codex`、`~/.phe`。
- 只读已安装的 Pi 包本身（`$SDK`）与它自带的 `.md` 文档；不读该用户的 Pi 配置目录。
- 批准记录一律带 `note: "验收夹具：合成批准，不代表真实用户同意"`；这里的 `source=user` 是**夹具**，不是用户同意。
- 模型层是本地 faux provider（零网络、零费用），固定返回一句合法文本。这是**模型依赖的模拟**，只证明“送到模型的
  消息里确实是这份展开内容”，不证明模型会自主选用 Skill 或完成任务。

## 合成输入与命令序列

合成输入：一个目录型 Skill（`SKILL.md` + 两个引用文件）；P1 一条 patch 覆盖四类改动（改正文、改引用文件、加引用
文件、删引用文件）；两个 Host 入口都软链到同一来源目录。

下面五块按顺序执行，就是本次跑的那条序列（从本文档机械提取执行，见「本记录自身的复现」）。退出码逐块核对，
完整输出留在仓库外的日志里。

```bash
REPO=${REPO:?先设成仓库根（含 tools/ 的目录）}
WORK=$(cd "$(mktemp -d)" && pwd -P)
echo "WORK=$WORK"
SRC="$WORK/src"; mkdir -p "$SRC"
cp -R "$REPO/tools" "$SRC/tools"
WORK_DIR="$WORK/elsewhere"
mkdir -p "$WORK_DIR" "$WORK/home/.pi/agent/skills" "$WORK/home/.agents/skills" "$WORK/phe" "$WORK/out"
S="$WORK/skill/demo"; mkdir -p "$S/references"
cat > "$S/SKILL.md" <<'SKILL'
---
name: r3a-demo
description: 合成演示技能：验证多文件更新、逐项批准与恢复。
---

# r3a-demo

回答前先读 references/notes.md。
SKILL
printf '笔记：先给结论。\n' > "$S/references/notes.md"
printf '草稿：这条引用没有被 SKILL.md 用到。\n' > "$S/references/draft.md"
ln -s "$S" "$WORK/home/.pi/agent/skills/r3a-demo"
ln -s "$S" "$WORK/home/.agents/skills/r3a-demo"
SDK=$(python3 -c 'import os,sys;print(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1])))))' "$(command -v pi)")
python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['name'], d['version'])" "$SDK/package.json"
```

`$SDK` 由已安装的 `pi` 可执行文件反推（`pi` → 包内 `dist/bundle/cli.js`，取三层父目录），不写死本机路径。

```bash
(cd "$WORK_DIR" && python3 -I "$SRC/tools/workspace.py" init --phe-home "$WORK/phe")
(cd "$WORK_DIR" && python3 -I "$SRC/tools/inventory.py" --home "$WORK/home" --out "$WORK/phe/registry/inventory.json")
(cd "$WORK_DIR" && python3 -I "$SRC/tools/registry.py" adopt --inventory "$WORK/phe/registry/inventory.json" --registry "$WORK/phe/registry/assets.json" --group "$S")
```

```bash
(cd "$WORK_DIR" && WORK="$WORK" python3 -I - <<'PY'
import hashlib, json, os
from pathlib import Path
work = Path(os.environ["WORK"])
report = work / "out/report.md"
report.write_text("# 合成报告\n\n本报告是验收夹具里宿主写的示例，不是自动诊断。\n", encoding="utf-8")
patch = """--- a/SKILL.md
+++ b/SKILL.md
@@ -6,3 +6,3 @@
 # r3a-demo
 
-回答前先读 references/notes.md。
+回答前先读 references/notes.md 与 references/extra.md。
--- a/references/notes.md
+++ b/references/notes.md
@@ -1,1 +1,1 @@
-笔记：先给结论。
+笔记：先给结论，再复述问题。
--- /dev/null
+++ b/references/extra.md
@@ -0,0 +1,2 @@
+补充笔记：先复述问题。
+结论放在最前面。
--- a/references/draft.md
+++ /dev/null
@@ -1,1 +0,0 @@
-草稿：这条引用没有被 SKILL.md 用到。
"""
registry = json.loads((work / "phe/registry/assets.json").read_text(encoding="utf-8"))
asset_id = registry["assets"][0]["asset_id"]
change = {
    "schema_version": 1,
    "report": {"path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()},
    "items": [
        {
            "item_id": "P1",
            "kind": "user_preference",
            "evidence": ["$WORK 合成报告：示例偏好"],
            "prediction": {
                "why": "合成证据要求正文与引用文件一起更新",
                "expected": "两个入口读到同一份新发布，草稿引用文件消失",
                "verify": "读两个入口的文件集合与每个文件字节；Host 加载与收益记 unknown",
            },
            "target": {"asset_id": asset_id},
            "patch": patch,
        }
    ],
}
(work / "out/change.json").write_text(json.dumps(change, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("asset", asset_id)
PY
)
(cd "$WORK_DIR" && python3 -I "$SRC/tools/personalize.py" preview --phe-home "$WORK/phe" --change "$WORK/out/change.json" --cycle c1)
(cd "$WORK_DIR" && WORK="$WORK" python3 -I - <<'PY'
import hashlib, json, os
from pathlib import Path
work = Path(os.environ["WORK"])
plan = work / "phe/reports/c1/plan.json"
approval = {
    "schema_version": 1,
    "approval_id": "approval-fixture-c1",
    "source": "user",
    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    "items": ["P1"],
    "note": "验收夹具：合成批准，不代表真实用户同意",
}
(work / "out/approval.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("plan_sha256", approval["plan_sha256"])
PY
)
(cd "$WORK_DIR" && python3 -I "$SRC/tools/personalize.py" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c1/plan.json" --item P1 --approval "$WORK/out/approval.json")
readlink "$WORK/home/.pi/agent/skills/r3a-demo"
readlink "$WORK/home/.agents/skills/r3a-demo"
(cd "$(readlink "$WORK/home/.pi/agent/skills/r3a-demo")" && find . -type f | sort)
```

```bash
(cd "$WORK_DIR" && python3 -I "$SRC/tools/r3a/skill_host_probe.py" --sdk "$SDK" --home "$WORK/home" --agent-dir "$WORK/home/.pi/agent" --cwd "$WORK_DIR" --entry "$WORK/home/.pi/agent/skills/r3a-demo" --release "$WORK/phe/releases/r0001/files/asset-0001" --skill-name r3a-demo --out "$WORK/out/host-probe-applied.json")
python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(json.dumps(d['checks'],ensure_ascii=False));print('expanded-has-extra', 'references/extra.md' in d['record']['expanded'])" "$WORK/out/host-probe-applied.json"
```

```bash
(cd "$WORK_DIR" && python3 -I "$SRC/tools/personalize.py" restore-preview --phe-home "$WORK/phe" --execution x0001 --cycle c2)
(cd "$WORK_DIR" && WORK="$WORK" python3 -I - <<'PY'
import hashlib, json, os
from pathlib import Path
work = Path(os.environ["WORK"])
plan = work / "phe/reports/c2/restore-x0001.json"
approval = {
    "schema_version": 1,
    "approval_id": "approval-fixture-c2-restore",
    "source": "user",
    "execution": "x0001",
    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    "note": "验收夹具：合成恢复批准，不代表真实用户同意",
}
(work / "out/restore-approval.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("restore_plan_sha256", approval["plan_sha256"])
PY
)
(cd "$WORK_DIR" && python3 -I "$SRC/tools/personalize.py" restore --phe-home "$WORK/phe" --execution x0001 --plan "$WORK/phe/reports/c2/restore-x0001.json" --approval "$WORK/out/restore-approval.json")
readlink "$WORK/home/.pi/agent/skills/r3a-demo"
(cd "$(readlink "$WORK/home/.pi/agent/skills/r3a-demo")" && find . -type f | sort)
(cd "$WORK_DIR" && python3 -I "$SRC/tools/r3a/skill_host_probe.py" --sdk "$SDK" --home "$WORK/home" --agent-dir "$WORK/home/.pi/agent" --cwd "$WORK_DIR" --entry "$WORK/home/.pi/agent/skills/r3a-demo" --release "$S" --skill-name r3a-demo --out "$WORK/out/host-probe-restored.json")
python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(json.dumps(d['checks'],ensure_ascii=False));print('expanded-has-extra', 'references/extra.md' in d['record']['expanded']);print('expanded-base', [l for l in d['record']['expanded'].splitlines() if l.startswith('References are relative')])" "$WORK/out/host-probe-restored.json"
python3 -c "import json,sys;print([json.loads(l)['kind'] for l in open(sys.argv[1]) if l.strip()])" "$WORK/phe/ledger.jsonl"
```

| 块 | 内容 | 退出码 | 关键输出 |
|---|---|---|---|
| 1 | 合成输入 + `init` + `inventory` + `adopt` | 0 / 0 / 0 | `initialized …`；`adopted asset-0001 … (2 个 observed path)` |
| 2 | 变更包 + `preview` + 夹具批准 + `apply` | 0 / 0 / 0 | 计划 `plan_sha256 fad0c0…`；`execution_id=x0001`、`release=r0001` |
| 3 | Host 探针（应用后） | 0 | 十项检查全 `true`；`expanded-has-extra True` |
| 4 | `restore-preview` + 夹具批准 + `restore` + 探针（恢复后） | 0 / 0 / 0 / 0 | `restored_files` 回到应用前；`expanded-has-extra False` |
| 5 | 账本 | 0 | `['approval', 'apply', 'restore']` |

## 预览：逐文件 diff

`docs/acceptance/r3a/plan.json` 与同目录的 `preview.md` 是本次执行的原件（路径是那一次的 `$WORK`）：

| 断言 | 实际值 |
|---|---|
| 资产类型 | `asset.kind = "skill"` |
| 两个入口读同一份内容 | 两个入口 `sha256` 都是 `9b7d5b4a…`，`files` 都是 `SKILL.md`、`references/draft.md`、`references/notes.md` |
| 内容摘要不是 `SKILL.md` 单文件 hash | 入口 `sha256` 是逐文件「相对路径 + 文件 sha256」的整棵摘要；`SKILL.md` 自己的 sha256 是 `5c991a27…` |
| 逐文件状态 | `SKILL.md` 修改、`references/notes.md` 修改、`references/extra.md` 新增、`references/draft.md` 删除 |
| 计划里的目标路径 | `patch_targets` 四项全在，包括只有删除的 `references/draft.md`（旧实现会丢掉它） |
| 预期文件集合 | `SKILL.md`、`references/extra.md`、`references/notes.md`（草稿文件消失） |
| 预览写入范围 | 只写 `<PHE_HOME>/reports/c1/`；`home/` 与来源目录的文件指纹前后相同，账本不新增 |

## 应用与恢复：文件层

| 断言 | 实际值 |
|---|---|
| 只批准 P1 时其他条目不生效 | 变更包只有 P1；`apply --item P1` 配只覆盖 P1 的批准，账本一条 `apply` |
| 两个入口指向同一发布 | 两个 `readlink` 都是 `$WORK/phe/releases/r0001/files/asset-0001` |
| 发布里是应用后的文件集合 | `SKILL.md`、`references/extra.md`、`references/notes.md`；没有 `references/draft.md` |
| 引用文件内容 | 发布里 `SKILL.md` 正文提到 `references/extra.md`，`references/notes.md` 是“先给结论，再复述问题。” |
| 来源目录不改 | `find "$S"` 应用前后都是三个文件（apply 只写 PHE 资产与发布） |
| 账本 | `apply` 行带 `asset_kind=skill`、`before_files` / `after_files`、`before_sha256` / `after_sha256` 与两个 journal |
| 预览后改引用文件 | 用例覆盖：`apply` 退出 1，报 `入口 … 的内容在预览之后变了`，两个入口与文件都不写，账本无新行 |
| 未受管入口 | 用例覆盖：入口换成普通目录副本后预览把它标成 `other`（“PHE 不覆盖”），`apply` 退出 1 报 `入口是普通文件或目录`，普通目录与另一入口都不写 |
| 陈旧 patch | 旧入口用例覆盖：同一资产重复应用同一 patch 退出 1，报 `patch 打不上当前生效内容`，入口与账本不变 |
| 失败尝试的残留暂存 | 旧入口用例走完整序列：应用 P1 → 重复 P1 失败留下 `staging/x0002/tree`（含 `references/extra.md`）→ 恢复 x0001 → 只批 P2；发布与入口都不含 `references/extra.md`。产品入口共用同一暂存规则，用例直接放入失败尝试会留下的暂存树，重新发布前该暂存根被重建 |
| 只删除一个引用文件 | 用例覆盖：`references/draft.md` 的删除项出现在计划 `patch_targets` 与 `diffs`，`apply` 后发布不含它，`restore` 后文件集合与字节回原 |
| 升级后续作旧计划 | 用例 `tests/test_personalize_legacy.py`：base `5000e23` 生成并批准的应用计划与恢复计划在新版本执行成功；账本 `asset_kind` / `before_files` / `after_files` / `restored_files` 从已核对内容推导；输入改变仍退出 1 |
| 单文件资产原名 | 用例 `SourceNameTests`：来源 `shared-AGENTS.md`、patch 目标 `config/AGENTS.md`，预览 `expected_files`、资产内容与账本都用 `shared-AGENTS.md` |
| 逆序恢复 | 恢复计划 `restore.files` 等于应用前的三文件集合；`restore` 退出 0，`restored_files` / `restored_sha256` 回到应用前值 |
| 恢复后入口 | 两个 `readlink` 都回到 `$S`；相对软链变体保留原链接字符串（用例 `RelativeSkillRestoreTests`） |

## Host 层：真实 Pi 的发现与显式展开

先跑一个小探针确认路径可用（一次性隔离环境）：

| 探针 | 结果 |
|---|---|
| `DefaultResourceLoader({cwd, agentDir})` + `loader.getSkills()`，`HOME=$WORK/home` | 从 `$HOME/.agents/skills/demo` 发现 1 个 Skill，`name` / `description` / `filePath` / `baseDir` 正确，`diagnostics=[]` |
| 同上，`agentDir=$HOME/.pi/agent`（两个入口都在） | Pi 按名字去重，只报 `.pi/agent/skills/demo` 这一条（agentDir 的技能根优先） |
| `createAgentSession({..., noTools: "all"})` 后的 `session.systemPrompt` | **没有** `<skills>` 段：Pi 只在存在 `read` / `bash` 这类工具时才把 Skills 清单放进 system prompt |
| 同一会话改成 `tools: ["read"]`（只声明、不执行） | `<skills>` 段出现，含 `<name>demo</name>`、`<description>`、`<location>` |

因此最终探针只声明 `read` 工具，靠 faux provider 返回纯文本保证没有工具执行（`tool_results=0`）。

应用后（更新版）的十项检查全部 `true`（原件 `docs/acceptance/r3a/host-probe-applied.json`）：

| 检查 | 证据 |
|---|---|
| `one_skill_discovered` | 真实 loader 只发现 1 个 Skill，名字是 `r3a-demo` |
| `description_advertised` | 描述出现在 system prompt 的 `<skills>` 段 |
| `file_path_is_entry` / `base_dir_is_entry` | `filePath` 是入口下的 `SKILL.md`，`baseDir` 是入口目录（不是别的副本） |
| `skills_section_lists_skill` | `<name>r3a-demo</name>` 在 `<available_skills>` 里 |
| `expanded_body_matches_release` | `session.prompt("/skill:r3a-demo …")` 送到模型的消息里，正文是发布里 `SKILL.md` 去掉 frontmatter 后的内容 |
| `expanded_base_dir_matches_entry` | 展开文本含 `References are relative to <入口目录>.` |
| `expanded_keeps_user_args` | 用户参数“请按技能执行”留在展开文本尾部 |
| `files_match_release` | 入口目录（跟随软链）的逐文件 sha256 与发布内容完全相同 |
| `no_tool_execution` | `model_calls=1`、`tool_results=0`：faux 回复不含 toolCall，没有工具执行 |

恢复后再跑同一个探针（`--release "$S"`）：十项检查同样全 `true`，且展开文本里 `references/extra.md` 不再出现——
Pi 读到的是恢复后的原版内容（原件 `docs/acceptance/r3a/host-probe-restored.json`）。

**Host 通过的范围：** 原生发现 + 用户显式触发（`/skill:`）的展开；模型依赖部分由 faux provider 模拟。不证明
模型会自主选用这个 Skill、不证明任务执行成功、不证明收益。

## `~/.agents/skills` 读取者的事实

| 事实 | 证据 |
|---|---|
| Pi 1.4.0 读 `$HOME/.agents/skills` | 上表的真实 loader 发现与展开（隔离 HOME） |
| Codex 也读这个目录 | **未核实**：本次没有在隔离环境启动 Codex；盘点里的 `readers` 仍是本机观察，报告继续标 `load_chain unverified` |

## 红 → 绿

新用例先在 base `5000e23` 的源码上跑（`git archive 5000e23` 解到临时目录，只加入新用例与合成素材）：

| 用例 | base 上的结果 | 现在 |
|---|---|---|
| `test_personalize_skill.py`（6 项） | `FAILED (failures=6)`：预览没有逐文件 diff、`apply` 直接失败、恢复没有文件集合 | `Ran 8 tests … OK`（含第 1 轮新增 2 项） |
| `test_change_skill.py`（2 项） | `FAILED (failures=2)`：旧入口只按单文件 `read_bytes()` 取内容，多文件 patch 打不上 | `Ran 3 tests … OK`（含新增 1 项） |
| `test_workspace.py` 的技能条目断言 | `FAILED (failures=2)`：技能目录显示成“实体文件”、能力写“部分可托管” | `Ran 20 tests … OK` |

第 1 轮审查修复的用例先在**修复前的候选**上跑（同一份用例与合成素材）：

| 用例 | 修复前 | 现在 |
|---|---|---|
| `test_change_skill.py::test_failed_attempt_residue_does_not_leak_into_the_next_release` | `FAILED (failures=1)`：r0002 的 `after_files` 多出 `references/extra.md` | `Ran 3 tests … OK` |
| `test_personalize_skill.py::SkillResidualStagingTests` | `FAILED (failures=1)`：`apply` 退出 1 报 `打出来的内容和预览的 diff 不一致` | `Ran 8 tests … OK` |
| `test_personalize_skill.py::SkillDeleteOnlyTests` | `FAILED (failures=1)`：`preview` 退出 1 报 `patch 里没有目标路径` | `Ran 8 tests … OK` |
| `test_personalize.py::SourceNameTests` | `FAILED (failures=1)`：`expected_files` 是 `AGENTS.md`，不是 `shared-AGENTS.md` | `Ran 10 tests … OK` |
| `test_personalize_legacy.py`（3 项） | `FAILED (failures=2)`：`apply` 与 `restore` 都先在 `'expected_files'` 上失败；只修 apply 后 `restore` 接着报 `'files'` | `Ran 3 tests … OK` |

实现过程中按失败证据改了两处：入口/来源是目录时按整棵内容取摘要与 diff（不是只读 `SKILL.md` 或只算一个文件）；
预览后引用文件变化要按整棵摘要核对（不是只看正文文件）。第 1 轮修复轮另改了三处共用内容层规则（重建本次暂存根、
按成对 diff 头取目标路径、旧计划字段从已核对内容推导），没有改批准计划字节，也没有回退旧 R2 / instructions 与守卫路径。

## 测试与检查

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python3 -m unittest discover -s tests -t tests` | 0 | `Ran 412 tests … OK`（base 397 + R3a 交付 8 + 第 1 轮修复 7） |
| `ruff check .` | 0 | `All checks passed!` |
| `ruff format --check .` | 0 | `108 files already formatted` |

类型检查：仓库没有 `pyproject.toml`，`pyright` / `mypy` 未安装，**未执行**。

## 未验证 / 剩余缺口

- 模型自主选用 Skill、任务执行效果、收益：**未验证**，`review` 仍只能记 `observe` / `unknown`。
- Codex 读 `~/.agents/skills`：仍未核实（Pi 已核实）。
- 目录 import（`release.py import` 一个 Skill 目录）仍只在旧沙箱入口；产品入口的 skill 资产由 `apply` 首次写出
  资产内容，`personalize` 没有 import 子命令。
- 普通目录入口（未受管的 Skill 目录）不迁移、不覆盖：只要登记的某个入口是普通目录，计划就不可执行，要先把入口换成
  软链或从登记里移出。
- 资产内容里出现软链或特殊文件会被拒绝；引用文件必须在同一目录树内。
- 安装包、默认 `~/.phe` 与安装引导仍未提供。
- 本记录未读真实 Host 配置、未调用真实模型、未部署、未推送。

## 本记录自身的复现

从本文档机械提取全部 `bash` 块、顺序拼接后执行（`REPO` 指向仓库根）：

```sh
REPO=<仓库根> python3 - <<'PY'
import os, pathlib, subprocess, tempfile
fence = "`" * 3
blocks, current = [], None
for line in pathlib.Path("docs/acceptance/r3a.md").read_text(encoding="utf-8").splitlines():
    if current is None and line == fence + "bash":
        current = []
    elif current is not None and line == fence:
        blocks.append("\n".join(current))
        current = None
    elif current is not None:
        current.append(line)
with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
    fh.write("\n".join(blocks))
    path = fh.name
print(f"{len(blocks)} 块 → {path}")
raise SystemExit(subprocess.call(["bash", "-e", path], env=dict(os.environ)))
PY
```

其余块都在同一个 shell 里顺序执行；`WORK` 指向的临时目录在核对后先 `chmod -R u+w` 再删除。

## 工件与日志

- 本次执行的计划、预览、恢复预览、账本与两次探针记录：`docs/acceptance/r3a/`
  （`plan.json`、`preview.md`、`restore-x0001.json`、`restore-x0001.md`、`ledger.jsonl`、`host-probe-applied.json`、
  `host-probe-restored.json`）。工件里的临时工作目录统一写成 `$WORK`，不含本机用户名或机器绝对路径；
  报告/内容 sha256 都是那次运行的原始值。
- 完整命令输出与探针 stdout/stderr：仓库外的 `${TMPDIR}/pi-worker/` 日志（本次为 `r3a-doc-run-*.log`（机械提取
  执行）、`r3a-red-candidate-*.log`（把同一套新用例放到候选 `709a523` 上跑）、`r3a-fix-full-*.log`（全量测试与
  ruff））。
