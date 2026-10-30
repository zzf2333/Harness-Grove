# 轻量评估闭环示例（合成材料，可逐条复跑）

这条示例把 PHE 的默认轻量评估流程从头到尾走一遍：本地会话摘要 → 宿主回读原文写可审批报告 →
盘点/登记 → 生成变更包与演练批准 → 按批准应用 → 账本 → 复盘记录 → 逆序恢复回原字节。

全部材料虚构：会话、配置、报告都是合成的，不含任何真实内容。

## 会写哪些目录，不会碰什么

所有写入都在一个新开的临时工作区 `$WORK`（`mktemp -d`）里，它至少有这几个子目录：

| 路径 | 谁写 | 内容 |
|---|---|---|
| `$WORK/lightweight-loop/` | 你（`cp -R`） | 样例副本：会话 JSONL、配置、样例报告 |
| `$WORK/temper/config/AGENTS.md` | 你（`cp`） | 三个 Agent 入口软链共享的配置文件 |
| `$WORK/home/` | 你（软链）+ `change.py` | 模拟 home：`.pi/agent/AGENTS.md`、`.codex/AGENTS.md`、`.claude/CLAUDE.md` |
| `$WORK/registry/` | `inventory.py`、`registry.py` | 盘点 JSON 与登记表 |
| `$WORK/inputs/` | `render_inputs.py` | 变更包、演练批准、W3 的 review 记录 |
| `$WORK/phe/` | `change.py` | PHE_HOME：资产、发布、journal、账本 `ledger.jsonl` |
| `$WORK/digest/` | `history.py digest` | 会话摘要 `pi-1.md` 与 `pi-index.json` |

不碰真实 `~/.pi`、`~/.codex`、`~/.claude`、`~/AI/temper`：`change.py` 的沙箱守卫在写入前就拒绝这些路径，
本流程也不需要它们。不启动 Host、不调用模型、不联网、不提交任何东西。

## 样例内容

| 文件 | 说明 |
|---|---|
| `pi-root/sessions/--home-demo-proj--/<时间戳>_<会话 id>.jsonl` | 合成的 pi 会话（13 行）：L6 用户说“回答先给结论”，L9 与 L12 用户叫停 Agent 顺手改相邻文件 |
| `config/AGENTS.md` | 5 行配置：第 1、3 行是重复句，第 2 行与偏好冲突，第 4 行是越界约束 |
| `host-report.md` | **宿主手写**的样例报告：W1 偏好、W2 清理重复、W3 收益待验证的假设 |
| `render_inputs.py` | 把报告落成机读输入（变更包、演练批准、review 记录），只用标准库 |

`render_inputs.py` 的三个 patch 与 `tests/test_change_lightweight.py` 的 `PREFERENCE_PATCH` /
`CLEANUP_PATCH` / `ASSUMPTION_PATCH` 逐字节相同：测试是机器断言，本示例是给第一次使用的人逐条跑的同一流程。

## 前置

- 在仓库根执行；`python3` 可用；`git` 可用（`change.py apply` 用 `git apply` 打 patch）。
- 每步都标了 **[工具生成]**（命令产出文件）或 **[宿主判断]**（要读材料自己写）。

## 步骤

### 0. 建临时工作区

```bash
WORK=$(cd "$(mktemp -d)" && pwd -P)
echo "$WORK"
```

`pwd -P` 不能省：macOS 上 `mktemp -d` 给的 `/var/folders/...` 会解析成 `/private/var/folders/...`，
而 `registry.py adopt --group` 要和盘点里记录的 resolved path 逐字比对。

### 1. 复制样例并搭出三个入口 [工具生成]

```bash
SAMPLE=$WORK/lightweight-loop
cp -R examples/lightweight-loop "$SAMPLE"
mkdir -p "$WORK/temper/config" "$WORK/home/.pi/agent" "$WORK/home/.codex" "$WORK/home/.claude" \
    "$WORK/registry" "$WORK/phe" "$WORK/inputs" "$WORK/digest"
cp "$SAMPLE/config/AGENTS.md" "$WORK/temper/config/AGENTS.md"
ln -s "$WORK/temper/config/AGENTS.md" "$WORK/home/.pi/agent/AGENTS.md"
ln -s "$WORK/temper/config/AGENTS.md" "$WORK/home/.codex/AGENTS.md"
ln -s "$WORK/temper/config/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"
```

三个入口是软链，指向同一个共享文件，这就是本示例要改的那一个资产。

### 2. 生成会话摘要 [工具生成]

```bash
python3 -I tools/history.py digest --host pi --root "$SAMPLE/pi-root" \
    --since 2026-10-01 --out-dir "$WORK/digest"
cat "$WORK/digest/pi-index.json"
cat "$WORK/digest/pi-1.md"
```

预期：索引里 `sessions_total` 与 `sessions_included` 都是 1；摘要正文有 4 条 `用户`，行号是 L2、L6、L9、L12。
摘要不含助手输出，所以任何结论都要靠第 3 步回读原文。

### 3. 回读原文并读报告 [宿主判断]

```bash
sed -n '2p;6p;9p;12p' "$SAMPLE/pi-root/sessions/--home-demo-proj--/2026-10-28T09-00-00-000Z_7c1d0c4e-2b5a-4f36-9d2e-6a1b0c3d5e7f.jsonl"
sed -n '1p;2p;3p;4p' "$WORK/temper/config/AGENTS.md"
cat "$SAMPLE/host-report.md"
```

这一步没有工具能替你做：摘要只给 `L<行号>` 和源文件路径，判断要靠回读原文。`host-report.md` 是已经写好的
样例报告（并明确标注它是宿主手写、不是工具自动诊断的产物）。真实使用时，报告写到仓库的 `reports/<日期>.md`，
且**不在这一步应用任何改动**。

### 4. 盘点 home 并登记要改的资产 [工具生成]

```bash
python3 -I tools/inventory.py --home "$WORK/home" --out "$WORK/registry/inventory.json"
python3 -I tools/registry.py adopt --inventory "$WORK/registry/inventory.json" \
    --registry "$WORK/registry/assets.json" --group "$WORK/temper/config/AGENTS.md"
ASSET_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"][0]["asset_id"])' "$WORK/registry/assets.json")
echo "$ASSET_ID"
```

预期：`adopt` 打印 `adopted asset-0001 ... (3 个 observed path)`，`$ASSET_ID` 是 `asset-0001`。

### 5. 生成变更包、演练批准与 W3 的 review 记录 [工具生成]

```bash
python3 -I examples/lightweight-loop/render_inputs.py --work "$WORK" --asset-id "$ASSET_ID"
```

写三个文件：`$WORK/inputs/change.json`（报告路径与原始字节 sha256、W1/W2/W3 的证据指针、三行说明、patch）、
`approval-rehearsal.json`（`source=sandbox_rehearsal`，只批 W1 与 W3）、`review-W3.json`（`observe` / `unknown`）。
打印出的 `change_sha256` 会写进批准记录，用来绑定这一版变更包。

### 6. 应用 W1、W3（W2 不批不应用） [工具生成]

```bash
python3 -I tools/change.py apply --change "$WORK/inputs/change.json" --item W1 \
    --approval "$WORK/inputs/approval-rehearsal.json" --registry "$WORK/registry/assets.json" \
    --phe-home "$WORK/phe" --home "$WORK/home"
python3 -I tools/change.py apply --change "$WORK/inputs/change.json" --item W3 \
    --approval "$WORK/inputs/approval-rehearsal.json" --registry "$WORK/registry/assets.json" \
    --phe-home "$WORK/phe" --home "$WORK/home"
```

预期：两条都是 `execution_id` 为 `x0001`、`x0002`，`release` 为 `r0001`、`r0002`，`source` 为
`sandbox_rehearsal`。这是演练批准，不是用户批准。

### 7. 看账本推导出的状态 [工具生成]

```bash
python3 -I tools/change.py status --phe-home "$WORK/phe"
```

预期：两个执行都是 `applied`，`active` 两条，三个入口的 `release` 是 `r0002`，`latest_review` 还是 `null`。
`status` 只读，不写任何文件。

### 8. 核对三个入口的实际字节 [工具生成]

```bash
for f in "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.codex/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"; do
    echo "== $f"; cat "$f"; done
cmp "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.codex/AGENTS.md"
cmp "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"
grep -c '^先给结论。$' "$WORK/temper/config/AGENTS.md"
```

预期：三份内容一致；第 2 行变成“先给结论，再复述问题。”，第 4 行末尾多了“也不要顺手做相邻改动”；
`grep -c` 输出 `2`，说明未批准的 W2 没有生效，重复句 `先给结论。` 还在两处。

### 9. 读复盘计划 [工具生成]

```bash
python3 -I tools/change.py review-plan --phe-home "$WORK/phe"
```

预期：两个执行各自带原预测（`why` / `expected` / `verify` / `baseline` 原文）、证据指针、报告引用、
观察窗 `from` 是应用时刻、`to` 为 `null`、`reviews` 为空。注意工具自己的 `note`：观察窗只是候选取样范围，
不证明 Host 加载过配置。

### 10. 写 review 记录 [宿主判断]

```bash
cat "$WORK/inputs/review-W3.json"
```

样例记录已预写：`verdict=observe`、`prediction_met=unknown`，因为改动只进了临时沙箱，Host 没加载，
观察窗内也没有该环境的会话；样例会话的时间早于应用时刻，**不能当观察证据**。真实复盘时，这一步要按
`prediction.verify` 回读观察窗内的会话、自己写这份 JSON。

### 11. 把 review 追加进账本 [工具生成]

```bash
python3 -I tools/change.py review --execution x0002 --record "$WORK/inputs/review-W3.json" --phe-home "$WORK/phe"
```

预期：输出一条 `kind=review` 事实，带 `record`、`record_path`、`record_sha256`；`status` 里 `x0002` 的
`latest_review` 变成这条。`review` 不改文件、不改发布、不改执行状态。

### 12. 逆序恢复 [工具生成]

```bash
python3 -I tools/change.py restore --execution x0002 --phe-home "$WORK/phe" --home "$WORK/home"
python3 -I tools/change.py restore --execution x0001 --phe-home "$WORK/phe" --home "$WORK/home"
```

顺序不能颠倒：同一资产只支持逆序恢复。先恢复 `x0002`，内容回到 W1 之后（`release: r0001`）；再恢复 `x0001`，
内容回到原文件（`release: null`）。

### 13. 核对回到原字节 [工具生成]

```bash
cmp "$WORK/home/.pi/agent/AGENTS.md" examples/lightweight-loop/config/AGENTS.md
cmp "$WORK/home/.codex/AGENTS.md" examples/lightweight-loop/config/AGENTS.md
cmp "$WORK/home/.claude/CLAUDE.md" examples/lightweight-loop/config/AGENTS.md
shasum -a 256 "$WORK/home/.pi/agent/AGENTS.md" "$WORK/temper/config/AGENTS.md" examples/lightweight-loop/config/AGENTS.md
ls -l "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.codex/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"
```

预期：三条 `cmp` 都无输出（相同），三个 sha256 一致，三个入口仍是软链、指回
`$WORK/temper/config/AGENTS.md`。

### 14. 清理 [工具生成]

```bash
python3 -I tools/change.py status --phe-home "$WORK/phe"
chmod -R u+w "$WORK"
rm -rf "$WORK"
```

删之前先看一眼最终状态（两个执行都是 `restored`、`active` 为空）。`releases/` 里的发布按设计是只读的
（`r0001`、`r0002` 的目录不可写），直接 `rm -rf` 会删不掉文件并留下半个目录，所以先 `chmod -R u+w` 放开写权限。
`rm -rf` 只删 `$WORK`。

## 读结果时要记住

- **批准与 `apply` 成功不等于效果已验证。** 它们只说明改动写进了临时配置，没说明它有用。
- **`source=sandbox_rehearsal` 不是用户批准。** 账本照原样记演练来源，不升级成用户批准。
- **W3 的复盘结论保持 `observe` / `unknown`。** 没有观察窗内的证据时，写 `met` 就是把待验证说成已验证。
- **样例会话在应用之前，不能当观察证据。** 会话时间 2026-10-28，应用时刻是复跑当天。
- 本示例只覆盖“单个 instructions 资产 + 三个 Agent 软链入口”，并且只在临时目录里跑。真实配置的写入要
  用户按具体 diff、目标路径和恢复方式单独批准。

相关文档：[PHE_SPEC §2.5](../../docs/PHE_SPEC.md)、[M3a_PLAN 闭环切片 1–3](../../docs/M3a_PLAN.md)、
[Skill §11–§13](../../skill/harness-evolve/SKILL.md)。
