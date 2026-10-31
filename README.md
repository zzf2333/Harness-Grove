# Harness Grove

Harness Grove 是 Personal Harness Evolution（PHE）的开发仓库。PHE 是面向所有用户的开源产品：用户安装后，结合自己已有的完整 Agent Harness（指令、Skills、MCP、Hooks、扩展、工具配置）、行为习惯和会话反馈，持续个性化优化，帮助驾驭 Agent。它用可追溯的证据、默认轻量的评估（值得时才做有限成本的实验）和人工批准来改动配置；资产管理是基础，评测是手段。

产品当前在 R2：`tools/workspace.py` 提供克隆后即可用的初始化与只读发现入口，`tools/personalize.py` 提供预览 → 批准 → 应用 → 复盘 → 恢复的产品入口（**都不是安装包**，也没有默认 PHE_HOME）。仓库里仍没有打包、自动安装或 Host 加载验证；写入只发生在你显式给出的 PHE_HOME 和逐条批准的入口上。排程见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 克隆后开始使用（R1：初始化 + 只读发现）

这叫“克隆后开始使用”，不是安装包：没有安装命令，没有默认 PHE_HOME，也不写任何真实 Host 配置。
下面整段流程只写一个新开的临时工作区 `$WORK`：合成 Host 树、仓库外的 PHE_HOME、无关的 cwd。
命令可以直接复制，把 `SRC` 换成你 clone 下来的仓库路径（`-I` 让 Python 不读当前目录与用户 site-packages；工具自己设了不写字节码，所以不会在源码树里留下 `__pycache__`）。

```bash
WORK=$(cd "$(mktemp -d)" && pwd -P); SRC=/path/to/Harness-Grove
mkdir -p "$WORK/host/.claude/skills/demo" "$WORK/host/.claude/rules" "$WORK/host/.pi/agent" \
    "$WORK/host/.codex" "$WORK/project/.claude/skills/proj-skill" "$WORK/elsewhere"
printf '先给结论。\n先复述问题再回答。\n用户说先别做时，不要越界执行。\n复盘时先读账本。\n' \
    > "$WORK/shared-AGENTS.md"
printf -- '---\nname: demo\n---\n演示 skill\n' > "$WORK/host/.claude/skills/demo/SKILL.md"
printf '{"servers": {"demo": {"command": "demo"}}}\n' > "$WORK/host/.pi/agent/mcp.json"
printf '风格规则\n' > "$WORK/host/.claude/rules/style.md"
ln -s "$WORK/shared-AGENTS.md" "$WORK/host/.claude/CLAUDE.md"
ln -s "$WORK/shared-AGENTS.md" "$WORK/host/.codex/AGENTS.md"
ln -s "$WORK/shared-AGENTS.md" "$WORK/host/.pi/agent/AGENTS.md"
printf '项目指令\n' > "$WORK/project/AGENTS.md"
printf -- '---\nname: proj-skill\n---\n' > "$WORK/project/.claude/skills/proj-skill/SKILL.md"
cd "$WORK/elsewhere"

python3 -I "$SRC/tools/workspace.py" init --phe-home "$WORK/phe"
python3 -I "$SRC/tools/workspace.py" discover --phe-home "$WORK/phe" \
    --home "$WORK/host" --project "$WORK/project"
```

`init` 建出 `<PHE_HOME>/assets/`、`<PHE_HOME>/releases/` 和格式版本标记 `<PHE_HOME>/workspace.json`；
重复运行保留已有数据，不迁移、不覆盖。登记表、账本与绑定按实际操作创建，`init` 不预建。
`--phe-home` 也可以用 `PHE_HOME` 环境变量给，命令行优先；两者都没给就报错退出 2，不会默认落到 `~/.phe`。

`discover` 只读盘点，把结果写进 PHE_HOME：

| 文件 | 内容 |
|---|---|
| `<PHE_HOME>/reports/discovery.md` | 人可读清单：来源路径、软链目标、Host 入口、逻辑类型、登记状态、能力限制、`load_chain unverified` |
| `<PHE_HOME>/reports/discovery.json` | 同一份结果的结构化版本：ObservedAsset 字段 + 管理状态 + 登记状态 + 覆盖摘要与扫描范围 |

看报告：`less "$WORK/phe/reports/discovery.md"`。它不改输入文件、软链、登记表或配置，同一 fixture 连跑两次字节一致。

可选：把共享指令登记成基线（`registry.py adopt` 只写登记表，不改被登记文件），再跑一次 `discover`，
报告里那条资产会显示“已登记 asset-0001（Active）”，其余仍是“未登记”：

```bash
python3 -I "$SRC/tools/inventory.py" --home "$WORK/host" --project "$WORK/project" \
    --out "$WORK/phe/registry/inventory.json"
python3 -I "$SRC/tools/registry.py" adopt --inventory "$WORK/phe/registry/inventory.json" \
    --registry "$WORK/phe/registry/assets.json" --group "$WORK/shared-AGENTS.md"
python3 -I "$SRC/tools/workspace.py" discover --phe-home "$WORK/phe" \
    --home "$WORK/host" --project "$WORK/project"
```

这一次运行告诉你什么：机器上有哪些 Harness 资产、从哪个文件或软链来、哪个 Host 入口在读、PHE 到哪一步能管
（`沙箱可托管` / `部分可托管` / `仅观察` / `未支持`）。它**不**证明这些配置被 Host 加载过：每条都标 `load_chain unverified`。
MCP 只有文件级观察，Hooks 没有候选路径所以列“未支持”。本流程的验收记录见 [docs/acceptance/r1.md](docs/acceptance/r1.md)。
清理放在 R2 流程末尾（发布是只读的，要先赋可写再删）；**R1 跑完不要先删 `$WORK`**，下面那一段接着用这份合成环境。

## R2：用你批准的改动更新自己的指令

这一段接着上一段：合成 Host、仓外 `$WORK/phe`、无关 cwd 都不变。产品入口 `tools/personalize.py` 与旧沙箱入口
（`tools/change.py`、`tools/release*.py`）共用发布、journal、绑定和账本格式，但写入政策是分开的：

- 旧沙箱入口仍拒绝真实 home 及其下 `.claude`、`.codex`、`.pi`、`.phe`、`.agents`，只用于演练。
- 产品入口按**你批准的那份计划**写：只写计划里列出的 Host 入口，加上显式 `<PHE_HOME>` 产物。执行前还核对实际
  `--phe-home` 就是计划里那个工作区、每个入口当前读的还是计划要改的那份内容；演练批准（`sandbox_rehearsal`）沿用旧
  沙箱的真实环境拒绝条件，不获得真实 home、`.phe` 或 Host 入口的写入能力。

先准备合成输入：一份宿主写的报告、两周期各一份会话记录，和一份带两条目的机读变更包（都是普通 JSON，
工具不生成也不解析 Markdown）。

```bash
mkdir -p "$WORK/out" "$WORK/pi-root/sessions/--home-synth-proj--"
printf '# 合成报告\n\n宿主写的示例，不是自动诊断。\n' > "$WORK/out/report.md"
cat > "$WORK/pi-root/sessions/--home-synth-proj--/c1.jsonl" <<'JSONL'
{"type":"session","id":"c1","cwd":"/home/synthetic/proj","timestamp":"2026-10-30T09:00:00.000Z","version":"0"}
{"type":"message","timestamp":"2026-10-30T09:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"以后回答先给结论。"}]}}
{"type":"message","timestamp":"2026-10-30T09:00:02.000Z","message":{"role":"user","content":[{"type":"text","text":"不对，复盘时也要对照原预测。"}]}}
{"type":"message","timestamp":"2026-10-30T09:00:03.000Z","message":{"role":"assistant","content":[{"type":"text","text":"收到，先给结论。"}]}}
{"type":"message","timestamp":"2026-10-30T09:00:04.000Z","message":{"role":"user","content":[{"type":"text","text":"这几条约束都要保留。"}]}}
{"type":"message","timestamp":"2026-10-30T09:00:05.000Z","message":{"role":"user","content":[{"type":"text","text":"下次复盘先读账本再改。"}]}}
JSONL
cat > "$WORK/pi-root/sessions/--home-synth-proj--/c2.jsonl" <<'JSONL'
{"type":"session","id":"c2","cwd":"/home/synthetic/proj","timestamp":"2026-10-31T09:00:00.000Z","version":"0"}
{"type":"message","timestamp":"2026-10-31T09:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"以后回答先给结论。"}]}}
{"type":"message","timestamp":"2026-10-31T09:00:02.000Z","message":{"role":"user","content":[{"type":"text","text":"不对，复盘时也要对照原预测。"}]}}
{"type":"message","timestamp":"2026-10-31T09:00:03.000Z","message":{"role":"assistant","content":[{"type":"text","text":"收到，先给结论。"}]}}
{"type":"message","timestamp":"2026-10-31T09:00:04.000Z","message":{"role":"user","content":[{"type":"text","text":"恢复前先给我看预览。"}]}}
{"type":"message","timestamp":"2026-10-31T09:00:05.000Z","message":{"role":"user","content":[{"type":"text","text":"不要再顺手改相邻文件。"}]}}
JSONL
python3 - "$WORK" <<'PY'
import hashlib, json, pathlib, sys

work = pathlib.Path(sys.argv[1])
report = work / "out/report.md"
sessions = work / "pi-root/sessions/--home-synth-proj--"


def item(item_id, line, kind, why, patch):
    return {
        "item_id": item_id,
        "kind": kind,
        "evidence": [f"{sessions}/c1.jsonl:L{line}"],
        "prediction": {
            "why": why,
            "expected": f"{item_id} 生效；不丢“用户说先别做时不越界”",
            "verify": "读一遍入口内容；收益待验证，没有 Host 加载证据时记 unknown",
        },
        "target": {"asset_id": "asset-0001"},
        "patch": patch,
    }


change = {
    "schema_version": 1,
    "report": {"path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()},
    "items": [
        item(
            "P1",
            2,
            "user_preference",
            "宿主按 c1:L2 的用户原话起草：回答先给结论",
            "--- a/config/AGENTS.md\n+++ b/config/AGENTS.md\n@@ -1,3 +1,3 @@\n 先给结论。\n"
            "-先复述问题再回答。\n+先给结论，再复述问题。\n 用户说先别做时，不要越界执行。\n",
        ),
        item(
            "P2",
            3,
            "hypothesis",
            "宿主按 c1:L3 的用户原话提出假设：复盘时对照原预测",
            "--- a/config/AGENTS.md\n+++ b/config/AGENTS.md\n@@ -3,2 +3,2 @@\n 用户说先别做时，不要越界执行。\n"
            "-复盘时先读账本。\n+复盘时先读账本，并对照原预测。\n",
        ),
    ],
}
(work / "out/change.json").write_text(json.dumps(change, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
```

预览，然后读它：

```bash
T="$SRC/tools/personalize.py"
python3 -I "$T" preview --phe-home "$WORK/phe" --change "$WORK/out/change.json" --cycle c1
# 读预览：每条的三行说明、实际 diff、每个入口的当前内容与写入范围
less "$WORK/phe/reports/c1/preview.md"
```

预览只写 `<PHE_HOME>/reports/c1/`，不动 Host。**批准就是宿主按用户原话写一份绑定计划 sha256 的记录**（工具不
自动造批准）。下面用合成夹具代写批准记录：第一条 `source=user` 只是**夹具来源，不代表任何真实用户同意**；
第二条 `source=sandbox_rehearsal` 是演练批准，它没有真实 home、`.phe` 或 Host 入口的写入能力。

```bash
approve_apply() {  # $1 计划文件 $2 批准记录 $3 批准 ID $4 来源 $5… 获批条目
  python3 - "$@" <<'PY'
import hashlib, json, pathlib, sys

plan, out, approval_id, source, *items = sys.argv[1:]
record = {
    "schema_version": 1,
    "approval_id": approval_id,
    "source": source,
    "plan_sha256": hashlib.sha256(pathlib.Path(plan).read_bytes()).hexdigest(),
    "items": items,
    "note": "夹具：合成批准，不代表真实用户同意",
}
pathlib.Path(out).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}
approve_restore() {  # $1 恢复计划 $2 批准记录 $3 批准 ID $4 来源 $5 执行 ID
  python3 - "$@" <<'PY'
import hashlib, json, pathlib, sys

plan, out, approval_id, source, execution = sys.argv[1:]
record = {
    "schema_version": 1,
    "approval_id": approval_id,
    "source": source,
    "execution": execution,
    "plan_sha256": hashlib.sha256(pathlib.Path(plan).read_bytes()).hexdigest(),
    "note": "夹具：合成恢复批准，不代表真实用户同意",
}
pathlib.Path(out).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

approve_apply "$WORK/phe/reports/c1/plan.json" "$WORK/out/approval.json" approve-001 user P1
python3 -I "$T" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c1/plan.json" \
    --item P1 --approval "$WORK/out/approval.json"
# 第二条要重新预览：P1 已经把入口内容改了，旧计划里的“当前内容”不再成立。
python3 -I "$T" preview --phe-home "$WORK/phe" --change "$WORK/out/change.json" --cycle c1
approve_apply "$WORK/phe/reports/c1/plan.json" "$WORK/out/approval-rehearsal.json" approve-002 sandbox_rehearsal P2
python3 -I "$T" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c1/plan.json" \
    --item P2 --approval "$WORK/out/approval-rehearsal.json"
python3 -I "$T" status --phe-home "$WORK/phe"
```

没被批准的条目不会生效；预览之后变更包、入口形态或入口内容任何一处变了，`apply` 都退出 1 要求重新预览——它不会
拿旧计划执行另一份 diff。执行前还核对两件事：实际 `--phe-home` 必须就是计划里的那个工作区（在 A 批准、换
`--phe-home` B 执行会退出 1），每个入口现在读的必须还是计划要改的那份内容（共享入口已经分叉时预览就停下）。
应用成功只说明入口指向了 PHE 发布，**不说明 Host 加载过，也不说明变好了**。

复盘与下一周期（记录都是普通 JSON，证据指针是会话文件 + `L<行号>`）：

```bash
cat > "$WORK/out/review.json" <<JSON
{
  "verdict": "observe",
  "prediction_met": "unknown",
  "reason": "只在合成 home 应用；Host 是否加载未验证",
  "observations": ["$WORK/pi-root/sessions/--home-synth-proj--/c1.jsonl:L2"],
  "next": "按 prediction.verify 在观察窗内统计；没有行为证据前记 unknown"
}
JSON
cat > "$WORK/out/cycle-c1.json" <<JSON
{
  "schema_version": 1,
  "cycle": "c1",
  "recorded_at": "2026-10-31T12:00:00+00:00",
  "summary": "第一周期：只改了偏好一条，收益未知",
  "processed_evidence": [
    {
      "source_path": "$WORK/pi-root/sessions/--home-synth-proj--/c1.jsonl",
      "lines": [2, 3],
      "kind": "user_preference",
      "from_ts": "2026-10-30T09:00:00+00:00",
      "to_ts": "2026-10-30T09:00:05+00:00"
    }
  ],
  "next_observations": ["恢复预览与批准是否按计划执行", "下一周期只收新证据"]
}
JSON
cat > "$WORK/out/cycle-c2.json" <<JSON
{
  "schema_version": 1,
  "cycle": "c2",
  "recorded_at": "2026-10-31T13:00:00+00:00",
  "summary": "第二周期：只新增证据，旧预测仍可读",
  "processed_evidence": [
    {
      "source_path": "$WORK/pi-root/sessions/--home-synth-proj--/c2.jsonl",
      "lines": [5, 6],
      "kind": "user_preference",
      "from_ts": "2026-10-31T09:00:00+00:00",
      "to_ts": "2026-10-31T09:00:05+00:00"
    }
  ],
  "next_observations": ["下一周期对照这两条观察"]
}
JSON

python3 -I "$T" review-plan --phe-home "$WORK/phe"       # 观察窗 + 原预测
python3 -I "$T" review --phe-home "$WORK/phe" --execution x0001 --record "$WORK/out/review.json"
python3 -I "$T" digest --phe-home "$WORK/phe" --cycle c1 --host pi --root "$WORK/pi-root"
python3 -I "$T" cycle-record --phe-home "$WORK/phe" --cycle c1 --record "$WORK/out/cycle-c1.json"
python3 -I "$T" cycle-plan --phe-home "$WORK/phe" --cycle c2
python3 -I "$T" digest --phe-home "$WORK/phe" --cycle c2 --host pi --root "$WORK/pi-root" --since 2026-10-31
python3 -I "$T" cycle-record --phe-home "$WORK/phe" --cycle c2 --record "$WORK/out/cycle-c2.json"
python3 -I "$T" cycle-plan --phe-home "$WORK/phe" --cycle c3
```

`cycle-plan` 读回上一周期的证据范围（源文件路径 + `L<行号>` + 原文 ts）、三行说明和复盘结论；`cycle.at` 与 `review.at`
是记录时间，**不是**已处理证据水位。下一周期只收上一周期没处理过的证据，重复计入会被拒。

撤销也要先预览、再批准，而且只允许逆序：

```bash
python3 -I "$T" restore-preview --phe-home "$WORK/phe" --execution x0002 --cycle c2
approve_restore "$WORK/phe/reports/c2/restore-x0002.json" "$WORK/out/approval-restore.json" approve-restore-002 sandbox_rehearsal x0002
python3 -I "$T" restore --phe-home "$WORK/phe" --execution x0002 \
    --plan "$WORK/phe/reports/c2/restore-x0002.json" --approval "$WORK/out/approval-restore.json"
python3 -I "$T" restore-preview --phe-home "$WORK/phe" --execution x0001 --cycle c2
approve_restore "$WORK/phe/reports/c2/restore-x0001.json" "$WORK/out/approval-restore-user.json" approve-restore-001 user x0001
python3 -I "$T" restore --phe-home "$WORK/phe" --execution x0001 \
    --plan "$WORK/phe/reports/c2/restore-x0001.json" --approval "$WORK/out/approval-restore-user.json"
```

恢复执行前会复核入口形态、入口当前内容与恢复目标的实际内容：预览之后任何一处变了都退出 1，要求重新预览。

核对结果，并从末尾清理这个临时工作区（发布是只读的，要先赋可写再删）：

```bash
readlink "$WORK/host/.claude/CLAUDE.md" "$WORK/host/.codex/AGENTS.md" "$WORK/host/.pi/agent/AGENTS.md"
shasum -a 256 "$WORK/shared-AGENTS.md" "$WORK/host/.pi/agent/AGENTS.md"
python3 -c 'import json,sys; print([json.loads(line)["kind"] for line in open(sys.argv[1], encoding="utf-8")])' \
    "$WORK/phe/ledger.jsonl"
chmod -R u+w "$WORK"
cd "$SRC"
rm -rf "$WORK"
test ! -e "$WORK" && echo "临时工作区已删除"
```

两个 `shasum` 相同说明恢复回到了应用前字节；软链目标回到 `$WORK/shared-AGENTS.md`；账本的 `kind` 序列是
`approval, apply, approval, apply, review, cycle, cycle, restore, restore`。本流程的验收记录见
[docs/acceptance/r2.md](docs/acceptance/r2.md)；**R2 只覆盖指令这一类资产和一个用户周期**。

## R3a：更新一个 Skill（正文 + 引用文件）

Skill 是目录资产：`SKILL.md` 加它引用的文件（`references/*.md` 等）。R3a 把 R2 的闭环扩到这类资产，命令仍是
`tools/personalize.py` 的 `preview` / `apply` / `restore-preview` / `restore`，账本还是同一份：

- 登记：两个 Host 技能根（`~/.pi/agent/skills/<名字>` 与共享的 `~/.agents/skills/<名字>`）软链到同一个来源目录，
  `registry.py adopt --group <来源目录>` 把它登记成一条资产（两个入口共享一份内容）。
- 预览逐文件：计划列出每个文件的增 / 删 / 改（`SKILL.md` 与引用文件都在内）和应用前后的文件集合。内容摘要不是
  `SKILL.md` 的单一 hash，而是「相对路径 + 每个文件 sha256」的整棵摘要；预览里正文改一行、增删一个引用文件都会分别
  显示。
- 批准与执行：只写被批准的条目；两个入口都指向同一份发布 `releases/rNNNN/files/<asset_id>`，来源目录本身不改。
- 恢复：按逆序把入口指回原来源，还原件集合与每个文件的字节；相对软链保留原链接字符串。
- 输入形态：patch 可以只删除一个引用文件（成对 diff 头里 `+++` 是 `/dev/null`）；计划会把删除项列进目标路径与逐文件 diff，应用后发布不含它，恢复后文件回原。
- 升级后续作：R2 时生成并批准的 `schema_version=1` 计划升级后仍按原 sha 与入口链接契约执行；账本的 `asset_kind`、`before_files` / `after_files` / `restored_files` 从已核对的内容推导，不要求重新预览或重新批准；输入在预览后变了仍拒绝。

新增的拒绝条件只有三条，都来自“按整棵内容判断”这件事：预览之后引用文件变了要重新预览（报 `… 的内容在预览之后变了`）；
登记的入口是未受管的普通目录时计划不可执行（预览把它标成 `other`，报“PHE 不覆盖”，不迁移、不覆盖）；资产内容里出现
软链或特殊文件时拒绝。

完整的可复跑序列（合成 Skill + 两个入口软链 + 隔离 Host 探针）见 [docs/acceptance/r3a.md](docs/acceptance/r3a.md)，
那里的 bash 块可以从文档机械提取后顺序执行。Host 层已经验证到“Pi 1.4.0 在隔离 HOME 里发现这个 Skill，并在 `/skill:`
显式触发时把发布内容展开进模型消息”；**未验证**模型会自主选用它，也不证明任务效果或收益。

## R3b（第一片）：更新一个已存在的 MCP 条目

MCP server 现在按**条目**维护，不按整份配置文件：一个逻辑 server 定义存在 `assets/<asset_id>/content/server.json`，
按 Host 适配器渲染成 Pi 或 Claude Code 的 JSON 条目。命令仍在 `tools/personalize.py`，账本还是同一份：

- 登记：`mcp-adopt --server <名字> --target pi=<路径> --target claude=<路径>` 读**已经存在**的条目，写出逻辑定义与
  绑定（`asset.json` 的 `mcp.targets` 记 adapter / config_path / scope / server）。只接受 Pi 的用户级
  `~/.pi/agent/mcp.json`、项目级 `.pi/mcp.json` 与 Claude Code 项目级 `.mcp.json`；其他路径直接拒绝。它只写
  PHE_HOME，不碰 Host。
- 预览：`preview` 把逻辑定义的 diff 与**每个 Host 的渲染差异**放进同一份计划，`host_writes` 只列托管的配置文件。
- 应用：只替换 `mcpServers` 下托管成员的值跨度，未托管的 server 与文件外围字节不动；文件权限位保留。写入方式只有
  这一种，`instructions` / Skill 的入口条目不会因此获得整文件写权限。
- drift：`mcp-drift` 按适配器把定义渲染后与每个 Host 的当前条目比较，只报字段名（不打印值）。外部手改会被报成
  `drifted`，不会因为整文件 Hash 没进账本就显示“没变化”。
- 恢复：`restore-preview` / `restore` 单独预览、单独批准，按 journal 的片段反向拼接并核对两侧文件 sha256；PHE 的
  逻辑定义保留应用后的内容。

凭证口径：`env` / `headers` 的值必须是 `${NAME}` 引用，`url` 不带内联认证；其他字面值、`oauth`、`timeout`、
`exposure`、`description`、`headersHelper`、`!command` 让该条目不可托管并报出字段名。工具不读值、不执行 `!command`，
journal 与发布里都没有未托管条目的内容。

本片**不支持**：新增 / 删除条目（`mcpServers` 里没有这个 server 时计划标 `not_applicable`，不创建）、Codex 的
`~/.codex/config.toml`、`enabled` / 禁用的落点（Claude 在 `~/.claude.json`，本片不写）、以及“Host 会加载”或
“能连上 server”这类结论。

最小输入与批准的样子（完整可跑序列见 [docs/acceptance/r3b.md](docs/acceptance/r3b.md)）：

```bash
# 两个合成配置：4 空格缩进的 Pi 用户级文件、2 空格缩进的 Claude 项目级文件，各含托管 demo 与未托管 unmanaged
REPO=${REPO:?仓库根}
WORK=$(cd "$(mktemp -d)" && pwd -P)
mkdir -p "$WORK/home/.pi/agent" "$WORK/project" "$WORK/elsewhere"
cat > "$WORK/home/.pi/agent/mcp.json" <<'JSON'
{"mcpServers": {"demo": {"command": "npx", "args": ["-y", "demo-mcp"], "env": {"DEMO_KEY": "${DEMO_KEY}"}}}}
JSON
cat > "$WORK/project/.mcp.json" <<'JSON'
{"mcpServers": {"demo": {"command": "npx", "args": ["-y", "demo-mcp"], "env": {"DEMO_KEY": "${DEMO_KEY}"}}}}
JSON
python3 -I "$REPO/tools/workspace.py" init --phe-home "$WORK/phe"
cd "$WORK/elsewhere"
python3 -I "$REPO/tools/personalize.py" mcp-adopt --phe-home "$WORK/phe" --server demo \
  --target "pi=$WORK/home/.pi/agent/mcp.json" --target "claude=$WORK/project/.mcp.json"
python3 -I "$REPO/tools/personalize.py" preview --phe-home "$WORK/phe" --change <变更包> --cycle c1
python3 -I "$REPO/tools/personalize.py" apply --phe-home "$WORK/phe" --plan "$WORK/phe/reports/c1/plan.json" \
  --item P1 --approval <批准记录>
```

批准记录与 R2 同形：`{"schema_version":1,"approval_id":"…","source":"user","plan_sha256":"<plan.json 的 sha256>",
"items":["P1"],"note":"…"}`——`items` 只列托管条目，`source=user` 由宿主按用户原话写，工具不自己造。恢复批准另带
`"execution":"x0001"`。

## 当前状态

已交付的是零件与沙箱闭环，完成状态只在各自声明的范围内成立：

- 产品路线：[docs/ROADMAP.md](docs/ROADMAP.md) R1–R4。R1（独立开始使用）的初始化与只读发现入口已交付：`tools/workspace.py init/discover` 在沙箱验收（见 [docs/acceptance/r1.md](docs/acceptance/r1.md)），报告写在显式 PHE_HOME 的 `reports/` 下；仍未提供安装包、默认 PHE_HOME、真实 Host 写入与加载验证。R2（首条持续个性化闭环）的产品入口已交付：`tools/personalize.py` 的 `preview/apply/restore-preview/restore/status/review-plan/review/digest/cycle-record/cycle-plan` 在隔离模拟用户环境里通过两次合成周期的验收（见 [docs/acceptance/r2.md](docs/acceptance/r2.md)）；旧 `release_deploy` 守卫一字未改，仍拒绝真实 home 及 Host 入口路径，那套入口继续只用于演练。安装包、默认 `~/.phe`、Host 加载验证仍未提供。
- R3b（MCP）第一片已交付：`tools/personalize.py` 的 `mcp-adopt` / `mcp-drift` 与同一套 `preview` / `apply` /
  `restore-preview` / `restore` / `review` 支持**更新已存在**的 Pi（用户级、项目级）与 Claude Code（项目级）JSON
  条目，只替换 `mcpServers` 下托管成员的跨度，未托管条目与外围字节不变，journal 只记托管片段（见
  [docs/acceptance/r3b.md](docs/acceptance/r3b.md)）。新增 / 删除条目、Codex TOML、`enabled` 落点、Host 加载与
  连接、收益都**未验证或未支持**。
- R3a（Skills）文件层与一次隔离 Host 的发现/显式展开已交付：同一套产品入口直接对目录型 Skill（`SKILL.md` + 引用文件）做逐文件预览、应用、账本与逆序恢复，两个 Host 技能入口指向同一发布；Host 层用真实 Pi 1.4.0 的 loader 与 `session.prompt("/skill:…")` 验证发现与展开，模型层是 faux provider（见 [docs/acceptance/r3a.md](docs/acceptance/r3a.md)）。模型自主选用、任务效果、Codex 读 `~/.agents/skills`、安装包仍**未验证**；MCP、Hooks、扩展与工具配置是 R3b–R3e。
- M1 的 A–D1 已在沙箱完成（盘点、登记、漂移检测、发布/部署/回滚）；D2（MCP、hooks、Codex config.toml）未开始，接管真实环境需用户另批，见 [docs/M1_PLAN.md](docs/M1_PLAN.md)。
- M2 已完成（按原定 Gate 结项，2026-10-30）：历史采集、结构分组、`log`/`status` 续作记录、Codex Target 最小接口已完成。两个真实 Host 续作同一提案已通过：S1 阻塞交接（Pi 起草、真实 Codex 会话从文件续读并停在阻塞处），S2 正常冻结交接（Pi 起草、Codex 独立复核后冻结，主控补核快照绑定；未批准或运行 Target），见 [docs/M2_PLAN.md](docs/M2_PLAN.md)。Target 按需逐个适配：Claude Code 有 M-1/M0 真实运行证据；真实 Claude Code Target 实验 m2-real-002 启动 1/15 次后因服务端拒绝订阅登录（403）停在 `pivot:telemetry_missing`，加载链阻塞保留。Codex Target 真实运行、费用与隔离验证是后续适配项，不追加为 Gate。
- M3a（历史阶段，未完成）：切片 A 完成，新增 `tools/proposal.py draft/check` 从 M2 分组产物生成经验候选提案并只读检查缺项；切片 B1 完成，`draft --retired <JSON>` / `check --retired <JSON>` 处理 Host 显式给出的有限 Retired 检索文件并核对 `unrelated` / `alternative` / `reopen` 关系。自动 Retired 检索（B2）、自动独立证据判断（C）暂停，D 缩小为改前写三行说明（为什么改 / 期待 / 凭什么判断），见 [docs/M3a_PLAN.md](docs/M3a_PLAN.md)。
- 闭环切片 1（沙箱实现，R2 的零件）：`tools/history.py digest` 把真实会话整理为带源文件/行号指针的脱敏 Markdown 分块；宿主按 [Skill §11](skill/harness-evolve/SKILL.md#11-经验复盘闭环切片-1) 只读诊断、核对原文并生成可审批报告。已在真实会话上跑过摘要，报告与真实验收未在真实配置上执行。
- 闭环切片 2（沙箱实现，R2 的零件）：`tools/change.py apply/status/restore` 把报告条目按批准应用到临时 home 的三个 Agent 入口，账本 `<phe-home>/ledger.jsonl` 只追加批准/应用/恢复事实，恢复按执行逆序；真实配置未写入（`change.py` 当前只在沙箱运行，守卫拒绝真实入口）。见 [docs/M3a_PLAN.md](docs/M3a_PLAN.md#闭环切片-2批准应用与恢复仅临时配置) 与 [Skill §12](skill/harness-evolve/SKILL.md#12-经验复盘应用闭环切片-2)。
- 闭环切片 3（沙箱实现，R2 的零件）：`tools/change.py review-plan/review` 读账本给出每个执行的观察窗与原预测（三行说明），把宿主的保留/调整/撤销/继续观察连同证据指针与记录 sha256 追加为 `review` 事实；`status` 附最新 review。工具只记录、不判断效果，`revert` 仍需用户批准后才 `restore`。演练在切片 2 账本副本上跑通，见 [docs/M3a_PLAN.md](docs/M3a_PLAN.md#闭环切片-3复盘账本对照原预测) 与 [Skill §13](skill/harness-evolve/SKILL.md#13-复盘记录闭环切片-3)。
- M-1 已完成：spike-001 判定 **go**（2026-10-28，25 次运行，7 项门槛全部通过）；真实规则信号为 inconclusive。M0 已完成：生产等价基线复验 c-002 判定 go（15 次，规则信号 inconclusive）。运行产物位于被忽略的 `experiments/`。

## 文档地图

| 文件 | 内容 | 什么时候读 |
|---|---|---|
| [docs/ROADMAP.md](docs/ROADMAP.md) | **当前唯一排程**：产品目标、三个空间、Harness 资产现状矩阵、R1–R4 各自场景/复用/缺口/验收/依赖/完成标准 | 想知道现在做什么、下一步做什么 |
| [docs/acceptance/r1.md](docs/acceptance/r1.md) | R1 验收记录：初始化与只读发现的命令、退出码、断言、剩余缺口与未验证项 | 想复核 R1 交付或重跑首次使用流程时 |
| [docs/acceptance/r2.md](docs/acceptance/r2.md) | R2 验收记录：产品入口两次合成周期的命令、退出码、断言、旧沙箱与产品写入政策的对照、剩余缺口 | 想复核 R2 交付或重跑预览 → 批准 → 应用 → 复盘 → 恢复时 |
| [docs/acceptance/r3a.md](docs/acceptance/r3a.md) | R3a 验收记录：目录型 Skill 的逐文件预览/应用/恢复、隔离 Host 的发现与显式展开、红→绿、可机械提取的命令序列 | 想复核 Skill 闭环或重跑 Host 探针时 |
| [docs/acceptance/r3b.md](docs/acceptance/r3b.md) | R3b 第一片验收记录：MCP 条目级更新与 drift、未托管条目字节与凭证不进产物的证据、红→绿、可机械提取的命令序列 | 想复核 MCP 闭环或重跑两个合成 JSON 配置时 |
| [docs/PHE_SPEC.md](docs/PHE_SPEC.md) | 产品契约：边界、三个空间、用户流程、架构、术语与状态、生命周期、资产库共同契约、安全、历史阶段与评估口径 | 想了解产品边界与长期怎么设计 |
| [skill/harness-evolve/SKILL.md](skill/harness-evolve/SKILL.md) | 可移植 Agent Skill：起草 → 冻结 → 用户批准 → `run` → `judge` 的操作顺序与硬规则 | 准备在宿主 Agent 里跑一次规则消融评测时 |
| [docs/M2_PLAN.md](docs/M2_PLAN.md) | M2 历史采集、证据分组、跨 Host 续作的切片与隐私约束（历史阶段） | 做 M2 相关维护 |
| [docs/M3a_PLAN.md](docs/M3a_PLAN.md) | M3a 阶段计划：切片 A/B1 契约、闭环切片 1–3 的沙箱实现（历史阶段） | 改 `proposal.py` / `change.py` |
| [docs/M2_HISTORY_FORMATS.md](docs/M2_HISTORY_FORMATS.md) | Claude Code、Codex、Pi 的会话目录与 JSONL 字段表（本机观察，未按官方文档核实） | 改 `tools/history.py` 或核对会话格式时 |
| [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md) | 真实实验的执行控制：主动暂停与续跑、墙钟累计口径、Target 写隔离 | 准备跑或审查 `phe.py run` |
| [docs/M1_D_DESIGN.md](docs/M1_D_DESIGN.md) | 切片 D1 的沙箱设计：守卫、目录、命令、验收 | 实现或审查切片 D |
| [docs/M1_PLAN.md](docs/M1_PLAN.md) | M1 的完成条件、切片、盘点约束（A–D1 沙箱完成，D2 未开始；历史阶段） | 推进或审查 M1 遗留项 |
| [docs/M0_PLAN.md](docs/M0_PLAN.md) | M0 的完成条件、切片、门槛 v2、选任务要求 | 推进或审查 M0 |
| [docs/M-1_PROTOCOL.md](docs/M-1_PROTOCOL.md) | M-1 探针的完整执行协议：路径、对照规则、spike-000 用量测量、25 次运行、预算、门槛、判定 | 准备执行或审查 M-1 |
| [examples/m-1/proposal.template.json](examples/m-1/proposal.template.json) | M-1 预注册模板（draft），未知项为 null | 填写并冻结 spike-001 时 |
| [examples/m3a/](examples/m3a/) | M3a 合成输入示例：分组 JSON、带 `retired_review` 的 Host 候选、显式 Retired 检索文件 | 跑通 `draft --retired` / `check --retired` 时 |
| [examples/lightweight-loop/](examples/lightweight-loop/) | 轻量评估闭环的可复跑示例：合成 pi 会话、最小配置、宿主手写报告与 `render_inputs.py`，全部只写临时目录 | 想从头走一遍摘要 → 报告 → 批准 → 应用 → 复盘 → 恢复时 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 各版本的设计决定 | 想知道某个决定为什么这样定 |

**阅读顺序：** 先读 ROADMAP 的产品目标与 R1–R4，再读 PHE_SPEC 的「产品边界」「三个空间与数据归属」「评估口径」；M-1 协议和各 M 阶段计划只在需要那部分证据时读。

## 仓库与用户资产的区别

本仓库是**源码**：`tools/` 是产品代码，`skill/harness-evolve/` 是可移植 Skill 源码，`examples/` 是手写合成示例，测试只写临时目录。目标是用户的 Harness 资产、发布、绑定、账本、证据摘要与报告都在仓库外的 PHE_HOME（产品规划默认 `~/.phe`；当前只有克隆后的 `tools/workspace.py init --phe-home <目录>`，没有安装包，也没有创建过默认目录）；真实 Host 入口是 `~/.pi`、`~/.codex`、`~/.claude`、`~/.agents` 及项目级目录。规范上，新增提交不引入用户会话、真实配置或本机绝对路径。现状：早期 `experiments/` 下用 `git add -f` 加入的工件含本机绝对路径，`episodes/`、`reports/` 仍是开发期写入位置；两者都未清理，不把清理当作已完成。

## 目录约定

- `tools/workspace.py`：R1 的用户入口，克隆后从任意工作目录用 `python3 -I <源码>/tools/workspace.py` 运行，不是安装包。`init --phe-home <目录>`（或 `PHE_HOME` 环境变量，命令行优先；没有默认目录）建出 `assets/`、`releases/` 与格式版本标记 `workspace.json`；已有工作区保留数据、不迁移，版本不符时拒绝并说明。`discover --phe-home <目录> --home <显式 Host 根> [--project <根> …] [--registry <登记表>]` 复用 `inventory.py` 的候选路径表与分组做只读盘点，读 `<PHE_HOME>/registry/assets.json`（缺省位置）标记登记状态：入口当前指向的正是某个已登记的来源路径时认那个资产，认不出来（例如 `release deploy` 把入口换成发布文件的软链）再按登记表里的 `observed` 入口路径认身份；资产身份与入口当前目标分开比较，所以部署后仍显示已登记，并另外说明当前目标不是登记源。把 `reports/discovery.md`（人可读）和 `reports/discovery.json`（结构化，不含运行时间所以可复现）写进 PHE_HOME；不 adopt、不导入、不部署、不改输入。覆盖摘要按逻辑类型列“发现 N 条 / 未发现 / 未支持”，Hooks 这类没有候选路径的类型记“未支持”、不猜路径；每条记录带管理状态（`沙箱可托管` / `部分可托管` / `仅观察` / `未支持`）与 `load_chain unverified`。管理状态按条目判断，不只看盘点类型：`.claude/agents`、`.pi/agent/agents` 里的子代理定义（含目录节点）在盘点里归成 instructions，但逻辑类型未支持，报告只标“仅观察”，不继承单文件指令的沙箱托管能力。项目级范围只在传了 `--project` 时扫描，没传时报告写“未扫描”，不写成“没有”。
- `tools/m1/run_spike.py`：M-1 最小 Runner，只用标准库；一次调用跑一个 slot，产出写到 `experiments/<id>/`。
- `tools/phe.py`：M0 评测入口；`run <实验目录>` 按已批准 proposal 调度，`judge <实验目录> [--out <结果路径>]` 判定。组角色由 `harness.variants[*].remove` 推导（两个空列表为基线，`real_rule` 为真实规则，`required_control` / `redundant_control` 可缺省）；次数、顺序、数值门槛和预算读取 proposal，执行参数读取 config。`gates.version` 缺省为 v1，新模板使用 v2；缺少控制组的对应门槛输出 `not_applicable`。Target 由 `proposal.target.agent` 选择：`claude-code` 保持旧默认命令；`codex` 使用 `codex exec --json --ephemeral --ignore-user-config -s workspace-write -C <work> -m <proposal.target.model_id> -o <output>/last-message.txt <prompt>`。`config.target_agent` 只用于声明同一选择，与 proposal 冲突时拒绝启动；`config.target_argv` 只替代 Claude 默认命令供不调用模型的本地验证，Codex 拒绝这个覆盖、只用规范 argv（本地契约验证在测试层替换 subprocess），`argv.json` 记录完整实际 argv。Codex JSONL 取最后一次 `turn.completed` 的 thread 累计用量，TOK 只算 input+output；缺 usage、坏行、非零退出和失败事件保留 error 语义，不补零，最后一个 turn 未闭合也按截断处理。费用没有来源，保持 `None` 并继续 `pivot:telemetry_missing`；不提供 max-turns 预算控制。Codex 指纹只覆盖 `~/.codex/AGENTS.md` 和 `config.toml`，不读 auth。真实 Codex 会话隔离与费用仍未验证（详见 [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md)）。`run --pause-after-new-slots N` 在完成 N 个新 slot 后、启动下一个 Target 前主动暂停：退出码 0、不写 `stop.json`，续跑按原 schedule 跳过已完成；墙钟预算按 `runs.jsonl` 里已记录的执行时长为累计，人工等待不计入。`config.sandbox` 声明后用固定路径的 macOS `/usr/bin/sandbox-exec` 在执行层把 Target 及其子进程、外置验收器和后续 Git 采集限制为只能写 runner 为本 slot 创建的三个临时目录（工作目录、专用 tmp、输出子目录）；沙箱程序和写目录都不可配置，每个 slot 启动前用实际 policy 和完整允许清单实测，新实验还冻结 runner 模块 Hash（Codex 实验无论有无 `sandbox` 都要求完整三项，要求取自冻结 proposal 的原始 `target.agent`），不可用或配置越权就拒绝启动；验收前覆盖可信测试时按真实路径核对目的文件，逃出验收副本的链接、绝对路径和 `..` 直接拒绝并记下原因（详见 [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md)）。`log <实验目录> --host {claude,codex,pi} --step {draft,freeze,approve,run,judge,report} [--note <说明>]` 追加一条续作记录到 `experiments/<id>/host_log.jsonl`（在 proposal 冻结哈希之外，不改 proposal.json）；`status <实验目录>` 只读推导阶段（`empty` → `drafted` → `frozen` → `approved` → `running` → `judged` → `reported`），输出 `stage`、各文件是否存在的 `evidence`、`last_log`、`inconsistent`（日志声称的步骤比文件证据靠后）和 `notes`（坏行行号、证据链缺口），不写任何文件。旧 `tools/m1/` 入口保留供冻结实验引用。
- `tools/inventory.py`：M1 切片 A 的只读盘点；`--home`、`--project`（可多个）指定扫描根，`--out` 写 ObservedAsset JSON（缺省打印到 stdout）。每条记录带软链目标、断链标记、Hash、`status: unmanaged`、`load_chain: unverified`；顶层 `groups` 按真实路径聚合共享条目。`agent` 取 `claude`、`codex`、`pi` 或 `shared`：`shared` 表示跨工具共享目录（目前是 `~/.agents/skills`，Codex 和 Pi 都读、Claude Code 不读），这类记录另带 `readers`，单一 Agent 的目录不记（`readers` 来自本机观察，未按官方文档核实）。`settings*.json`、`.mcp.json`、`config.toml` 只记 Hash 和顶层键名，不记值。
- `tools/registry.py`：M1 切片 B/C 的登记与漂移检测；`adopt --inventory <盘点> --registry <登记表> (--path <p> | --group <resolved_path>)` 把选中的盘点条目登记为 `Active(origin=imported, evidence_status=unverified)`。资产按真实路径认定，`observed` 记录所有指向它的 agent/path；只写登记表，不改被登记文件。默认登记表 `registry/assets.json`（被 Git 忽略）。同一真实路径不重复登记；`access_error`、断链或无法解析真实路径的条目拒绝登记，退出码 1。`drift --inventory <新盘点> --registry <登记表> [--out <文件>]` 只读对比基线，输出 `content_changed`、`symlink_target_changed`、`missing`、`unreadable` 四类 `ExternalChange` 和 `changes` 计数（缺省打印到 stdout）；不更新登记表，未登记的新文件不算漂移，有变化也只报告不自动接受。
- `tools/release.py`：M1 切片 D1 的沙箱导入、发布、部署与回滚（写入层与 deploy/rollback 在 `tools/release_deploy.py`，绑定发布校验在 `tools/release_verify.py`）。`import --registry <登记表> --inventory <盘点>` 把选中资产复制进 `--phe-home/assets/<asset_id>/`（skill 复制整个目录）；`release` 构建不可写发布 `releases/rNNNN/`，`manifest.json` 记每个文件的 sha256 和 `asset_id`，编号走高水位不复用；`deploy --agent <x> --release <编号> --registry <登记表>` 先盘点 `--home` 做漂移检查，任何 `ExternalChange` 都中止且不动链接，全部检查通过后写 journal、逐条原子替换软链、最后写 `bindings.json`，中途失败按 journal 回滚；`rollback --journal <文件>` 按 journal 倒序恢复旧状态。`--home`、`--phe-home` 必填，按文件身份（含大小写别名）判断，等于真实 home、是它的祖先，或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之内时在任何写入前退出 2；所有写入先过 `guarded_write_path()`。deploy 不改登记表，基线不变；PHE 自己部署的链接由 deploy 预检按 `bindings.json` 识别——已绑定、精确指向该绑定 release 对应文件且该发布按 manifest 校验通过的链接不算漂移，单独运行 `registry drift` 仍会把它们报成 `symlink_target_changed`。
- `tools/change.py`：闭环切片 2/3 的批准、应用、恢复与复盘（写入仍经 `tools/release_deploy.py` 的 `Sandbox`，账本事实与推导在 `tools/change_ledger.py`，复盘入口在 `tools/change_review.py`）。`apply --change <变更包> --item <P1> --approval <批准记录> --registry <登记表> --phe-home <临时 PHE_HOME> --home <临时 home>` 从目标入口当前实际生效的内容起算，用 `git apply` 把该条目 patch 打到 `<phe-home>/staging/<执行 ID>/`，按 `release import` 的同一 `asset.json` 格式重建资产，再走既有 `release` 与逐 Agent `deploy`；三个入口全部完成后才向 `<phe-home>/ledger.jsonl` 追加 `approval` 和 `apply` 事实，失败保留 journal/发布但不追加、不重试。变更包带报告路径与 sha256（apply 重算，不一致退出 1）、条目 ID、证据、`prediction`（`why` / `expected` / `verify` / 可缺的 `baseline`）、目标 asset id 和 patch；工具只要求整个 `prediction` 存在，内部键不校验、原样入账，旧变更包继续可用；批准记录用 `change_sha256` 绑定确定版本的变更包与 `items`，`source` 只有 `user` 和 `sandbox_rehearsal`。`status --phe-home` 从事实推导每个执行是 `applied` 还是 `restored`、当前生效栈，附最新 `latest_review`，并读文件给出入口现在的发布和 sha256，不写任何东西。`restore --execution x0001` 按 journal 逆序恢复，只允许恢复当前最后一条生效的执行，恢复后按 journal 的 `old_target` 重新绑定到更早的 PHE 发布，下一次 deploy 才不会被漂移预检当成 ExternalChange；恢复只追加 `restore` 事实，不表示提案无效或 retired。`review-plan --phe-home` 只读列出每个执行（含已恢复）的观察窗（应用到恢复，未恢复的 `to` 为 `null`）、原预测、证据、报告引用和全部 review 历史；`review --execution x0001 --record <JSON> --phe-home` 只追加一条 `review` 事实（`verdict` ∈ keep/adjust/revert/observe、`prediction_met` ∈ met/not_met/unknown、`reason`、`observations` 证据指针、`next`，加记录路径与原始字节 sha256），不改执行状态、不调用 restore、不碰配置和发布，同一执行可多次 review 且旧行不改写；守卫只查 `--phe-home`。R3a 起也支持目录型 skill 资产：`SKILL.md` 加它引用的文件，patch 可以增 / 删 / 改引用文件，内容与账本按整棵文件树（逐文件 sha256）重建与记账。
- `tools/personalize.py`：R2 的产品入口（计划、预览与恢复预览在 `tools/personalize_plan.py` / `tools/personalize_restore.py`，执行在 `tools/personalize_apply.py`），复用 `release.py` / `release_deploy.py` / `change.py` / `change_ledger.py` / `change_review.py` 的业务函数，不建第二套引擎。`preview` 读报告、变更包、登记表与每个入口当前生效的内容，写 `<PHE_HOME>/reports/<cycle>/preview.md`、`plan.json` 与 `plan.sha256`（不写 Host）：计划绑定报告与变更包的 sha256、每个入口的形态与内容 sha256、每条目的实际 diff 与前后 sha256、写入范围与恢复方式；共享入口分叉（某个入口读的内容和计划要改的那份不同）时直接失败，不做多来源合并。`apply` 要求批准记录绑定计划原始字节（`plan_sha256`）并覆盖该条目；上一轮已应用的条目打不上当前内容时在预览里标 `not_applicable`，不挡同包其他条目。执行前核对实际 `--phe-home` 与计划一致、每个入口的内容 sha256 与计划基线一致，只写计划列明的入口与显式 `<PHE_HOME>` 产物；变更包、报告、入口形态或内容在预览之后变了就退出 1，要求重新预览。`restore-preview` / `restore` 同理先预览（绑定账本执行、journal、入口当前内容与恢复目标内容）、再批准，执行前复核形态、链接与两侧内容；相对软链按链接所在目录解析，恢复写回原始相对路径。`status` / `review-plan` / `review` 直接读写自己的 PHE_HOME 账本，`status` 多补一列批准来源；`digest` 用 `history_digest.build_digest` 的纯逻辑把摘要写进 `<PHE_HOME>/episodes/<cycle>/digest/`；`cycle-record` 追加周期事实（`processed_evidence` 带源文件路径、行号、类别与原文时间范围，重复上一周期的证据退出 1），`cycle-plan` 读回上一周期的证据范围、预测与结论并写明记录时间不是水位。目录型 skill 与单文件指令走同一套：计划的 `asset.kind` 是 `skill`，每个入口带文件集合与整棵摘要，每个条目带逐文件 `diffs`，恢复计划同样带两侧文件集合。R3b 起 `kind=mcp` 走同一套命令：`mcp-adopt` 登记已存在的条目并写 `assets/<id>/content/server.json`，`preview` 把逻辑定义 diff 与每个 Host 的渲染差异放进同一份计划，`apply` 只替换 `mcpServers` 下托管成员的值跨度（`tools/mcp_config.py` 用 `JSONDecoder.raw_decode` 定位，不重写整份文件），journal 记 `kind=mcp_fragments` 的托管前后片段，`restore` 反向拼接并核对两侧文件 sha256；`mcp-drift` 只读报条目级漂移。写入范围由 `personalize_plan.ApprovedScope` 判定（计划内入口精确匹配 + PHE_HOME，外加原子替换的同目录临时链接名），不查旧沙箱守卫；`source=sandbox_rehearsal` 另叠加 `release_deploy.refusal_reason` 的真实环境保护，`source=user` 才按计划精确范围写。旧入口的行为与守卫不变。
- `tools/mcp_config.py`：MCP 条目的逻辑定义与两个 JSON 适配器（R3b 第一片）。`mcpServers` 下目标成员的值跨度用
  `JSONDecoder.raw_decode` 加小范围扫描定位，写入只拼接这一段，未托管 server 与文件外围字节不动；用文件自己的缩进步长
  渲染（Pi 的 HTTP 条目不写 `type`，Claude 必须写 `"type": "http"`；Claude 条目没有 `cwd` 与 `enabled`）。`env` /
  `headers` 只接受 `${NAME}` 引用，`url` 不接受 userinfo，其他字段报名字而不是猜。
- `tools/asset_content.py`：资产内容的共用层（旧沙箱 `change.py` 与产品入口 `personalize*` 都用它，不建第二套引擎）：内容文件清单、`sha256` 摘要（单文件资产取文件字节；目录资产取「相对路径 + 每个文件 sha256」的整棵摘要）、在临时目录里用 `git apply` 试打 patch、按资产类型写回。两类资产共用同一套摘要与写入，但写入政策仍分开。
- `tools/r3a/skill_host_probe.py`：R3a 的隔离 Host 探针驱动（`skill_host_probe.mjs` 在子进程里跑真实 Pi SDK）。它显式给出 `HOME`、`PI_CODING_AGENT_DIR`、cwd 与 `PI_OFFLINE` / `PI_TELEMETRY`，用 `DefaultResourceLoader` 取技能列表、用 `createAgentSession` + `session.prompt("/skill:<名字> …")` 拿展开后的模型输入，模型层换成本地 faux provider（不联网、不付费、不执行工具）。它只写 `--out`，不碰 Host 配置；报告 `checks` 逐项给布尔值，不把模拟模型当成行为证据。
- `tools/rehearse_change.py`：闭环切片 2 演练，只读真实报告与用户级 `config/AGENTS.md`，把字节复制到 `--out` 下，在临时 home 的三个入口软链上应用 P1、P2 并逆序恢复；P1/P2 的 patch 在 `tools/rehearse_change_input.py`（手工从报告提取，不解析 Markdown）。批准写 `source=sandbox_rehearsal`，命令、断言、发布、journal、账本和恢复所需文件全部留在 `--out`（本轮产物在 `experiments/loop-apply-001/`，被 Git 忽略）。
- `tools/rehearse_review.py`：闭环切片 3 演练，把切片 2 的 `experiments/loop-apply-001/phe/` 复制到 `--out/phe`（原目录不改），先 `review-plan`，再对真实演练执行写 `observe`/`unknown` 的 review；然后在 `--out/synthetic/` 下另建一套【合成】沙箱（小 base + 三个软链入口 + 独立 PHE_HOME），apply 合成条目 `S1` 后不 restore，`to` 是 `null`，取样上界取演练声明的模拟复盘时刻 `cutoff = 应用时刻 + 60 分钟`，窗口是 `[应用时刻, cutoff)`，合成会话时间戳按相对实际 `applied_at` 的偏移生成，按窗口两端取样后手算 `cwd does not exist` 次数，结论按窗口内计数推导，并放一条晚于 `cutoff` 的命中证明不计入；另用零宽窗口样例证明窗口外的命中（含恰好等于端点的一条）不计入观察。合成沙箱与真实演练账本分开。合成数据与取样逻辑在 `tools/rehearse_review_synthetic.py`，报告渲染在 `tools/rehearse_review_report.py`。命令、断言、记录和报告留在 `--out`（本轮产物在 `experiments/loop-review-001/`，被 Git 忽略）；结论只说明复盘流程可运行，不证明规则有效。
- `tools/proposal.py`：M3a 的本地草稿与只读检查（候选结构在 `tools/proposal_candidate.py`，显式 Retired 输入与关系核对在 `tools/proposal_retired.py`，共用输入校验在 `tools/proposal_input.py`），只用标准库。`draft --groups <分组 JSON> --candidate <Host 候选 JSON> [--retired <Retired 检索 JSON>] --experiment-id <id>` 只新建 `experiments/<id>/proposal.json`：形状取自 `examples/m-1/proposal.template.json`，只改实验身份、`status=draft` 和 artifact 路径，追加 `candidate` 段。Host 候选 JSON 只有 `groups`、`hypothesis`、`predictions`、`retired_review`；证据只从分组 JSON 解析 `source_path`/`line`/`ts`，绑定分组文件原始字节 sha256 和选中组 ID，不复制 `excerpt`，不接受 Host 自填引用或检索结果字段。缺假设/预测字段可保存并逐项报告；未知 metric、组不存在或同一 ID 对应多份内容、畸形 JSON/类型、已存在的实验目录（含冻结目录）和路径穿越/软链逃逸都拒绝。

  `--retired` 给出的是 Host 显式提供的有限 JSON（`schema_version: 1`、提供者声明的 `scope`、`entries` 数组）；工具按文件顺序逐条列举全部 `entries`，不推断语义相关、不排序、不截断、不扫描用户目录，也不访问真实退役记录。每个条目只保留 `retired_id`、`method`、`rejected_reason`、`retired_at`、`evidence_groups`；坏 JSON、错版本、缺字段、无时区时间、重复 `retired_id` 都拒绝。`candidate.retired` 只有两种判别清楚的状态：`unavailable`（未提供或来源读不到，带原因，绝不是 no_match）和 `completed`（带来源路径与原始字节 sha256、`scope=all_entries_in_supplied_file`、提供者的 `supplied_scope` 和逐条记录）。`entries` 为空只表示该文件为空。

  `retired_review` 是 Host 判断，不是检索结果：总体 `relation` / `reason` 加逐条 `retired_id` 关系。缺失可保存并列入 `missing`；`relation` 非法、`retired_id` 不存在或重复、引用当前候选未选中的分组、出现 `user_authorized` / `matches` / `status` 等字段都退出 1。`alternative` / `reopen` 必须有 `new_evidence_groups`，且这些分组不能出现在该记录退役时的 `evidence_groups` 里、分组时间必须晚于 `retired_at`，否则列 `retired_evidence_*` 阻塞。只检查这些必要结构和时序，不判断新证据是否语义独立或充分，也不实现手动恢复授权。

  `check --experiment-id <id>` 只读同一份 proposal（metric 白名单另读仓库模板，不读草稿自己的 `metrics`），不写侧文件、也不留 `__pycache__`，不运行 `run`/`judge`；编辑后的 `candidate.evidence` 若 `count` 与指针数不符、组 ID 重复或选中 ID 顺序不一致都拒绝，编辑后的 `candidate.retired` / `retired_review` 按与 `draft` 相同的规则检查。默认 `check` 不回读任何外部输入，报告里的 `retired_source_check` 会明确说明无法检测外部变化；`check --retired <file>` 才重算来源 sha256 并比对记录，变化或来源不可用时报 `re_search_required`、退出 1，且不改写 proposal。`freeze_ready` 在 B1 仍恒为 false（剩余预测冻结与正式核验未实现）；没有 `candidate` 的旧提案报 `legacy_proposal_out_of_scope`，不被判为失效。stdout 是 JSON，`saved`/`checked` 与 `freeze_ready` 分开；报告只覆盖候选、Retired 结构和明显的未填项，不是完整协议验证。
- `experiments/`：M-1 和 M0 的运行产物，已被 Git 忽略。提交脱敏样本需要另行批准。
- `tools/history.py`：M2 切片 A/B 的只读采集与结构分组；`collect --host {claude,codex,pi} --root <Host 配置根> --out <Episode JSON> [--since 7d|YYYY-MM-DD] [--exclude GLOB …]`。流式逐行读会话 JSONL，产出 `{"episodes":[…],"sources":[…]}`：每条 Episode 带 `host`、`source_path`、`session_id`、`line`、源文件 `sha256`、`project`、`ts`、`kind`、脱敏后的 `excerpt`（≤300 字）、`available`，`kind` 只有 `user_correction`、`tool_failure`、`retry` 三种启发式（召回率与准确率未评估）。源文件读不到或有行解析失败时，`sources` 里记 `available: false` 和 `missing_reason`，不静默跳过。Pi 默认排除 `sessions/subagent/*`，`--exclude` 在默认之外追加。`--root`、`--out` 必填；`--out` 落在真实 home 的 `.claude`、`.codex`、`.pi`、`.phe`、`.agents`、`AI/temper` 之内时在任何写入前退出 2。产物写在被 Git 忽略的 `episodes/`。切片 B 的 `group --episodes <Episode JSON…> --out <分组 JSON>` 把一份或多份 collect 产物按 host + session_id + kind 分组成 `correlation_group`（三者 sha256 前 12 位，同输入同输出）；每组记 project、count、首末 ts 和指针列表（`source_path`、`line`、`ts`、已脱敏 `excerpt`），不复制会话原文；不同会话不合并，只在 `same_project` 里互相列出同 project 同 kind 的组 id（project 为空不关联）；输入里 `available: false` 的来源汇总进 `sources_unavailable`。输入字段不合格时退出 1、不写输出。`--out` 落在真实 home 的 Agent 配置目录、与任一输入文件是同一文件、与任一会话源文件相同或落进会话源目录时退出 2。
- `tools/history.py digest --host {claude,codex,pi} --root <Host 配置根> --out-dir <目录> [--since 7d|YYYY-MM-DD] [--exclude GLOB …]`：闭环切片 1 的只读会话摘要入口，约 40KB 分块写 `<host>-N.md` 与 `<host>-index.json`；正文带 `L行号`，普通真人消息仅进入摘要，`collect/group` 的三种 kind 不变。
- `reports/`：含隐私的经验复盘报告，已被 Git 忽略。
- 被测任务所在的仓库只读，实验只在临时目录里运行。

## 许可证

[MIT](LICENSE)。
