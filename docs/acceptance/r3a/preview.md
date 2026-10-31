# 应用预览 · 周期 c1

- 生成时间：2026-10-31T05:53:31+00:00（工具按输入生成，不是自动诊断；报告本身是宿主写的材料）
- PHE_HOME：`$WORK/phe`
- 报告：`$WORK/out/report.md` sha256 `50158c567edc2d0a577150130a515764e343f397c9e4ccc3decf559ebd550e61`
- 变更包：`$WORK/out/change.json` sha256 `7b030599237c64c3aaec501700f8b4df16e2c6ee20852f1ee77635bd781e1f78`
- 计划 sha256：`fad0c0b35da8361d20514478eab0253ef2beaf39bf700d5626c4e1347437acb5`（批准记录绑定这份计划的原始字节）
- 资产：`asset-0001` ← `$WORK/skill/demo`（多个入口共享）

## 入口与当前内容

| Agent | 入口 | 当前形态 | 当前文件 | 当前 sha256 | 应用后 |
|---|---|---|---|---|---|
| pi | `$WORK/home/.pi/agent/skills/r3a-demo` | 软链 → `$WORK/skill/demo` | 3 个 | `9b7d5b4a6ef218500a46e24cac5452694bc589ed3c8197d5fc71f7565eb1e985` | 指向新发布 |
| shared | `$WORK/home/.agents/skills/r3a-demo` | 软链 → `$WORK/skill/demo` | 3 个 | `9b7d5b4a6ef218500a46e24cac5452694bc589ed3c8197d5fc71f7565eb1e985` | 指向新发布 |

共享关系：这些入口当前读同一份内容；应用后它们都指向同一份新发布，共享来源文件本身不改。

## 逐条 diff 与三行说明

### P1

- 为什么改：合成证据要求正文与引用文件一起更新
- 改后期待什么：两个入口读到同一份新发布，草稿引用文件消失
- 凭什么判断：读两个入口的文件集合与每个文件字节；Host 加载与收益记 unknown
- 证据：$WORK 合成报告：示例偏好
- 当前内容 sha256：`9b7d5b4a6ef218500a46e24cac5452694bc589ed3c8197d5fc71f7565eb1e985` → 应用后 `1ccb9472a9b079e3d2ea8e54fed65d6452075f7a18ec1a4ede0b5dccef5078f2`
- 改动文件：`SKILL.md`（修改）、`references/draft.md`（删除）、`references/extra.md`（新增）、`references/notes.md`（修改）

`SKILL.md`

```diff
--- a/SKILL.md
+++ b/SKILL.md
@@ -5,4 +5,4 @@
 
 # r3a-demo
 
-回答前先读 references/notes.md。
+回答前先读 references/notes.md 与 references/extra.md。
```

`references/draft.md`

```diff
--- a/references/draft.md
+++ b/references/draft.md
@@ -1 +0,0 @@
-草稿：这条引用没有被 SKILL.md 用到。
```

`references/extra.md`

```diff
--- a/references/extra.md
+++ b/references/extra.md
@@ -0,0 +1,2 @@
+补充笔记：先复述问题。
+结论放在最前面。
```

`references/notes.md`

```diff
--- a/references/notes.md
+++ b/references/notes.md
@@ -1 +1 @@
-笔记：先给结论。
+笔记：先给结论，再复述问题。
```

## 缺项清单（正常契约，补全后再批准更稳）

- 无

## 写入范围

Host 入口（只这些）：

- `$WORK/home/.pi/agent/skills/r3a-demo`
- `$WORK/home/.agents/skills/r3a-demo`

PHE_HOME 产物（相对 `<PHE_HOME>`）：

- `assets/asset-0001/content/（整棵替换）`
- `目录资产整棵重建（含引用文件）`
- `assets/asset-0001/asset.json`
- `releases/<下一个编号>/ 与 releases/high_water.json`
- `staging/<执行 ID>/`
- `journal/<时间戳>.json`
- `bindings.json`
- `ledger.jsonl`

恢复方式：恢复把每个入口链接指回应用前的目标（journal 记录）；共享来源文件本身不改，原字节不变。恢复也要单独预览并再次批准。

## 说明

- 应用成功只说明入口指向了 PHE 发布；Host 是否加载、行为是否改善都没有验证，复盘记 `observe` / `unknown`。
- 计划绑定变更包与报告的 sha256，以及每个入口当前内容的 sha256：输入或目标变了就要重新预览，不能拿旧计划执行另一份 diff。
- 变更包引用的报告必须是当前这一版；缺项只是提示，不代表条目不可批准。
