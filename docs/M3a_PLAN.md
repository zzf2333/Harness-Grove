# M3a 计划：从经验扫描产生可证伪提案

**状态（2026-10-30）：** 进行中。切片 A 完成：本地草稿 CLI `tools/proposal.py draft/check`。切片 B1 完成：显式 Retired 检索文件的逐条列举与关系核对（`--retired`、`retired_review`）。切片 B2（真实退役数据源与自动检索）、C（独立证据判定）暂停（2026-10-30 调整，见 [AGENTS.md](../AGENTS.md)）；D 缩小为改前写预测和验证方法。当前优先跑通经验复盘闭环：切片 1 的 `history.py digest` 已提供摘要入口，可审批报告的诊断与合并由宿主按 Skill 编排；切片 2 的 `tools/change.py apply/status/restore` 已在临时配置上跑通批准、应用与逆序恢复；切片 3 的 `tools/change.py review-plan/review` 已在切片 2 账本副本上跑通复盘记录（观察窗、原预测对照、合成计数样例），仍只记录宿主判断，不证明效果。真实配置仍未写入。**M3a 未完成**，不能把 A/B1 的报告当作完整协议验证。

**上位文档：** [PHE_SPEC](PHE_SPEC.md)「里程碑与 Gate」「独立证据与 Retired 账本」；历史输入格式见 [M2_PLAN](M2_PLAN.md)、[M2_HISTORY_FORMATS](M2_HISTORY_FORMATS.md)。

原 M3a 实验路线的 Gate：使用固定指标、冻结预测和 Retired 检索；不自动发布。2026-10-30 起优先按下述闭环切片推进，L2 对照实验可选。已完成的 A/B1 CLI 仍只读报告明显未闭合项，原有实验入口保留。

## 切片

| 切片 | 内容 | 状态 | 验收边界 |
|---|---|---|---|
| A | `tools/proposal.py draft/check`：Host 显式选组、写假设与预测，CLI 生成 `experiments/<id>/proposal.json` 并只读检查 | 完成 | 合成 CLI 测试；不联网、不调用模型、不读会话原文 |
| B1 | 显式 Retired 检索文件：`draft --retired <JSON>` 逐条列举、`retired_review` 关系核对、`check --retired` 显式复核来源 | 完成 | 只处理提供者给出的有限文件；不扫描目录、不推断语义相关、不截断 |
| B2 | Retired 真实数据源与自动检索：负样本来源、匹配规则、扫描范围 | 暂停（2026-10-30 调整，见 AGENTS.md） | 宿主显式写出检索范围与依据 |
| C | 独立证据判定：重新启用已退役方案时判断新证据是否独立、是否充分 | 暂停（2026-10-30 调整，见 AGENTS.md） | 宿主显式写出判断依据与材料缺口 |
| D | 缩小为改前写预测和验证方法 | 待按闭环报告落实 | 改前记录预期效果、验证方法；无基线写待观察 |

原 M3a 实验路线的 Gate 尚未核对；不以完成暂停项作为当前闭环切片的前置条件。以下 A/B1 记录保留其交付时的契约与限制。

## 闭环切片 1：真实会话 → 可审批报告

先用最近 7 天的真实会话生成摘要，再由宿主组织只读诊断、合并并生成逐条可审批的报告：

```bash
python3 -I tools/history.py digest --host pi --root ~/.pi/agent \
    --out-dir episodes/digest-YYYY-MM-DD --since 7d
python3 -I tools/history.py digest --host codex --root ~/.codex \
    --out-dir episodes/digest-YYYY-MM-DD --since 7d
python3 -I tools/history.py digest --host claude --root ~/.claude \
    --out-dir episodes/digest-YYYY-MM-DD --since 7d
```

`--since` 也接受 `YYYY-MM-DD`，省略时不限制时间。`--exclude GLOB` 可重复，Pi 默认排除 `sessions/subagent/*`。每个来源文件作为一个会话统计；`sessions_total` 是遍历到的来源数，时间窗内没有真人发言的记 `sessions_skipped`，读不到、有坏行或不支持 schema 的记 `sessions_unavailable`，三类与 `sessions_included` 相加等于总数。

产物为 `<host>-1.md`、`<host>-2.md`…（约 40KB 分块，会话不跨块，单会话可能超过目标大小）以及 `<host>-index.json`（时间窗、总数、入选/跳过/不可用数、各块文件名/字节数及不可用来源原因）。每节包含完整会话 id、Host、项目、首末时间、源文件绝对路径，统计窗口内用户消息、工具调用、工具失败；正文只列脱敏后的真人消息（≤300 字）、工具失败（≤200 字）和 retry 标注。工具失败按原 collect 的信号计数，Codex 同一次失败可能有两条信号。摘要没有助手上下文，诊断者必须按 `源文件路径 + L行号` 回读源 JSONL 上下文核对，材料不足就写不足。脱敏只到现有正则级别，摘要和报告仍含隐私；`episodes/`、`reports/` 均被 Git 忽略。

报告写到 `reports/<YYYY-MM-DD>.md`，每条包含：编号、目标文件、实际 diff，以及三行评估说明——为什么改（附证据指针）、改后期待什么（含不能丢的约束）、凭什么判断（已核对的证据与未知项）；收益测不出就写“收益待验证”，不编造实测改善（评估口径见 [PHE_SPEC §2.5](PHE_SPEC.md#25-怎么评估一条改动默认轻量值得时才实验2026-10-30)，2026-10-30 起替代原“问题、证据、预期效果、验证方法”字段）。Retired 检索和独立性由宿主显式说明依据，L2 实验只在有争议的改动上选用。切片 1 只读诊断，不改配置、不批准、不应用；用户逐条批准与账本/恢复留给切片 2。

验收：对真实最近 7 天三家 Host 各跑一次；记录会话数、入选数、块数和总字节；抽查报告引用能按源文件路径与行号回到原文，并核对诊断所需上下文。摘要 CLI 冒烟只证明采集可运行，不代表诊断报告已完成，也不证明建议有效。流程见 [Skill §11](../skill/harness-evolve/SKILL.md#11-经验复盘闭环切片-1)。

## 闭环切片 2：批准、应用与恢复（仅临时配置）

切片 1 的报告只读。切片 2 把报告条目显式翻译成机读变更包，按批准应用到临时配置，并留下可恢复的事实记录。真实 `~/.pi`、`~/.codex`、`~/.claude` 与 temper 仍未写入。

### 命令

```bash
python3 -I tools/change.py apply --change <变更包 JSON> --item P1 --approval <批准 JSON> \
    --registry <登记表> --phe-home <临时 PHE_HOME> --home <临时 home>
python3 -I tools/change.py status --phe-home <临时 PHE_HOME>
python3 -I tools/change.py restore --execution x0001 --phe-home <临时 PHE_HOME> --home <临时 home>
```

`--home`、`--phe-home` 沿用 release 的沙箱守卫：必填，等于真实 home、是其祖先或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之内时在任何写入前退出 2。`status` 只读，不需要 `--home`。

### 输入工件

变更包由宿主显式准备，工具不解析 Markdown：

```json
{
  "schema_version": 1,
  "report": {"path": "<报告绝对路径>", "sha256": "<报告原始字节>"},
  "items": [
    {"item_id": "P1", "evidence": ["<源文件绝对路径>:L804"],
     "prediction": {"expected": "...", "verify": "...", "baseline": "待观察"},
     "target": {"asset_id": "asset-0001"},
     "patch": "--- a/config/AGENTS.md\n+++ b/config/AGENTS.md\n@@ ..."}
  ]
}
```

`apply` 会重算 `report.sha256`，不一致就退出 1：变更包必须引用当前这一版报告。

批准记录绑定确定版本的变更包和获批条目：

```json
{"schema_version": 1, "approval_id": "...", "source": "user",
 "change_sha256": "<变更包原始字节>", "items": ["P1"], "note": "用户原话或演练说明"}
```

`source` 只有 `user` 和 `sandbox_rehearsal`。演练批准必须写 `sandbox_rehearsal`，账本照原样记录，不能拿它冒充用户批准。`--item` 不在 `items` 里，或 `change_sha256` 与当前变更包不符，`apply` 退出 1，不改文件、不追加事实。

### 一次 apply 做什么

1. 从登记表取目标资产，读它 `observed` 第一个入口当前**实际生效**的文件（入口是软链时按真实路径取），作为这次应用的基础内容。
2. 在 `<phe-home>/staging/<执行 ID>/` 里用 `git apply` 把该条目的 patch 打到这份内容上；打不上就退出 1，不写资产、不建发布。
3. 按 `release import` 的同一 `asset.json` 格式重建 `assets/<asset_id>/content/`，再走既有 `release` 构建不可变发布。
4. 对资产的每个 Agent 入口各 `deploy` 一次（本机是 `claude`、`codex`、`pi` 三个软链入口），三份 journal 记为**一次应用**。
5. 三个入口都完成后才向 `<phe-home>/ledger.jsonl` 追加 `approval` 和 `apply` 两条事实。中途失败按既有 deploy 行为保留 journal、发布和已完成的绑定，不自动重试、不回滚已成功的 Agent。

不从 `release import` 起步的原因：它拒绝覆盖已有资产，且固定从登记表的 `resolved_path` 复制；那条路径在部署后已不是实际生效的内容。每次 apply 都从当前生效内容重建资产，已撤销的内容才不会随下一次发布被带回。

### 账本与状态

`<phe-home>/ledger.jsonl` 只追加事实，一行一条 JSON：

- `approval`：`execution_id`、`approval_id`、`source`、`proposal_id`、`approved_items`、`change_path`、`change_sha256`、`at`。
- `apply`：`execution_id`、`proposal_id`、`source`、`report`、`evidence`、`prediction`、`target`（asset id、`resolved_path`、三个入口路径）、`release`、`journals`（相对 `phe-home`）、`before_sha256`、`after_sha256`、`at`。
- `restore`：`execution_id`、`proposal_id`、`journals`、`restored_sha256`、恢复后生效的 `release`（回到原文件时为 `null`）、`at`。

执行 ID 是 `x0001` 起的高水位，和提案 ID（`P1`）不同：同一提案可以先后有多次应用。`journals` 存相对路径，账本跟着 `<phe-home>` 走，不把临时绝对路径当永久证据。`status` 从事实推导每个执行是 `applied` 还是 `restored`、当前生效的执行栈，并读文件给出每个入口现在的生效发布和 sha256；它不写任何东西，也不存重复可推导的状态。

### 恢复语义

- 同一资产只支持**逆序恢复**：只能恢复当前最后一条生效的执行。恢复 `B` 保留 `A`（链接回到 `A` 的发布），再恢复 `A` 回到原文件。不实现“保留 B 单独撤销 A”，也不做补丁重排。
- 恢复只追加一条 `restore` 事实，不改写历史行，也不表示提案无效或 retired；效果结论留给切片 3 对照预测。
- 最小修正：既有 `rollback` 会 `unbind` 这些路径，恢复到的若是更早一次 PHE 发布，`bindings.json` 就与实际链接不一致，下一次 `deploy` 的漂移预检会把它当成 `ExternalChange` 中止（已实测复现）。`restore` 在 rollback 之后按 journal 的 `old_target` 重新绑定到本 `phe-home` 的发布，正常路径因此可以连续 apply / restore。

### 边界

- 只覆盖单个 `instructions` 资产 + 三个 Agent 的临时软链入口；skill 资产、普通文件入口、多资产发布不支持，`phe-home` 里只放这一个资产。
- 只在临时沙箱跑。写真实 `~/.pi`、`~/.codex`、`~/.claude` 或 temper 仍需用户按具体 diff、目标路径和恢复方式单独批准，本轮未做。
- 依赖 `git apply` 打 patch，不自带 diff 解析。
- 畸形输入、并发 apply、权限与攻击场景不在本轮范围，未测。

### 验收

- `tests/test_change.py`：合成两条不同改动 + 三个 Agent 入口，一次顺序 E2E 覆盖 只批准 P1 → P1 生效 P2 不生效 → 批准并应用 P2 → 恢复 P2 后 P1 保留 → 从当前版本应用 P3（hunk 以 P2 改过的行作上下文，复用陈旧暂存内容会打不上）→ 逆序恢复 P3、P1 回到原字节；并断言账本来源、执行 ID、前后 hash、`status` 与文件一致，以及逆序恢复和批准覆盖范围的拒绝。
- `tools/rehearse_change.py`：用真实切片 1 报告 `reports/2026-10-30.md` 的 P1/P2，在真实 `config/AGENTS.md` 的只读副本上跑同一流程。输入变更包、批准记录、发布、journal、账本、命令日志和断言报告全部留在 `experiments/loop-apply-001/`（被 Git 忽略），恢复所需文件不清理。演练批准写 `source=sandbox_rehearsal`。

## 闭环切片 3：复盘账本、对照原预测

切片 2 的账本只记事实，不给效果结论。切片 3 让下次复盘能读到原预测、取样范围和既往判断，并把自己的结论写回同一本账。

### 命令

```bash
python3 -I tools/change.py review-plan --phe-home <PHE_HOME>
python3 -I tools/change.py review --execution x0001 --record <review 记录 JSON> --phe-home <PHE_HOME>
```

`review-plan` 只读。`review` 只追加账本事实，不写配置、不改发布：守卫只查 `--phe-home`（不需要 `--home`），等于真实 home、是其祖先或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之内时在任何写入前退出 2。

### `review-plan` 输出

每个执行（含已恢复）列出：`execution_id`、`proposal_id`、`state`、`applied_at`、`restored_at`、`prediction`、`evidence`、`report`（报告路径与 sha256）、`observation_window`（`from` = 应用时刻，`to` = 恢复时刻；未恢复为 `null`，取样上界改取本次复盘时刻）、该执行全部历史 `reviews`（按账本顺序）和 `latest_review`（无则 `null`）。顶层 `note` 写明：观察窗只是候选取样范围，不证明 Host 加载过配置；review 只记录判断和证据指针。

### review 记录

宿主显式写一份 JSON，`review` 原样存进账本：

```json
{
  "verdict": "observe",
  "prediction_met": "unknown",
  "reason": "只在临时沙箱应用，真实 Host 从未加载，无基线",
  "observations": ["<源文件绝对路径>:L804", "<工件路径>"],
  "next": "用户批准真实应用后，按 prediction.verify 在观察窗内统计"
}
```

| 字段 | 约定 |
|---|---|
| `verdict` | `keep` / `adjust` / `revert` / `observe`，其他值退出 1 |
| `prediction_met` | `met` / `not_met` / `unknown`，其他值退出 1 |
| `reason` | 文字说明 |
| `observations` | 证据指针列表，形如 `<源文件绝对路径>:L<行号>` 或工件路径；可为空列表 |
| `next` | 下一步或还缺什么证据 |

账本追加一条 `review` 事实：`execution_id`、`proposal_id`、`record`（记录内容）、`record_path`（记录文件路径）、`record_sha256`（记录原始字节）、`at`。同一执行可以多次 review，旧行不改写；`status` 每个执行增加 `latest_review`（无则 `null`）。

### 语义

- review 只记录宿主的判断：工具不判断效果，不改 `applied` / `restored` 状态，不调用 `restore`，不碰配置和发布。
- `verdict=revert` 只是建议，用户批准后才走 `restore`；已恢复的执行 revert 不重复恢复。
- `verdict=observe` 表示证据不足、暂不下结论；对已恢复的执行表述为“待补证据”。
- `prediction_met` 只说原预测是否达到，不是因果证明。
- 写入临时配置不等于 Host 已加载：观察窗只是候选取样范围。

### 边界

- 没有自动取样、自动统计或自动判定：观察窗、证据指针和计数由宿主给出并核对原文。取样按源记录自身的 `ts` 核对半开区间 `[from, 上界)`：`to` 为 `null` 时上界取本次复盘时刻，不取复盘之后的记录；恰好等于上界的记录不算窗口内。`history.py --since` 只到日期粒度，只能粗筛，不得只按会话结束时间判断。
- review 记录不构成效果证据，也不代表真实 Host 加载过配置；账本仍只记事实。
- 畸形 record、并发写入、权限与攻击场景不在本轮范围，未测。

### 验收

- `tests/test_change.py`：在既有顺序 E2E 的 fixture 上加一条正常链路用例：apply → `review`（`observe` / `unknown`）→ 再 `review`（`keep` / `met`），两条历史都保留、`latest_review` 为后者；`review-plan` 回显原预测、证据、报告引用和观察窗；`restore` 后 `review-plan` / `status` 仍读得到 review 且观察窗收到恢复时刻；review 前后目标文件 sha256、发布链接和执行状态不变。
- `tools/rehearse_review.py`：把切片 2 演练的 `experiments/loop-apply-001/phe/` 复制到 `experiments/loop-review-001/phe`（原目录不改），先 `review-plan`，再对真实演练执行 `x0001` 写 `observe` / `unknown`；然后在 `experiments/loop-review-001/synthetic/` 下另建一套【合成】沙箱（小 base、三个软链入口、独立 PHE_HOME），apply 合成条目 `S1` 后不 restore，`to` 是 `null`，取样上界取演练声明的【模拟复盘时刻】`cutoff = applied_at + 60 分钟`，窗口因此是 `[应用时刻, cutoff)`；合成会话时间戳按相对实际 `applied_at` 的偏移生成，按窗口两端取样后手算 `cwd does not exist` 次数，结论按窗口内计数推导，并放一条晚于 `cutoff` 的命中证明不计入。另用零宽窗口样例证明切片 2 `x0001` 的 `[T, T)` 窗口内观察数为 0（含恰好等于端点的一条），结论只能是 `observe` / `unknown`。49 条断言全部成立；合成产物与真实演练账本分开，结论只说明复盘流程可运行，不证明规则有效。工件留在 `experiments/loop-review-001/`（被 Git 忽略），不清理。报告渲染在 `tools/rehearse_review_report.py`。

## 切片 A：本地草稿（完成）

### 命令

```bash
python3 -I tools/proposal.py draft \
    --groups <分组 JSON> \
    --candidate <Host 候选 JSON> \
    --experiment-id <新实验 ID>

python3 -I tools/proposal.py check --experiment-id <已有实验 ID>
```

仓库根由 `tools/proposal.py` 的源码位置推导，没有 `--root` 之类的绕过参数。`draft` 只写 `<仓库根>/experiments/<id>/proposal.json`；`check` 只读同一路径。实现分为 `tools/proposal.py`（CLI、模板装配、路径守卫）和 `tools/proposal_candidate.py`（选组、证据绑定、候选检查、报告）。

### 完整示例（手写合成材料）

分组 JSON 来自 `python3 -I tools/history.py group --episodes <Episode JSON…> --out <分组 JSON>`。示例里只要保留 `groups` 列表和选中组的字段；`episodes` 里的 `excerpt` 不会被复制。

```json
{
  "groups": [
    {
      "correlation_group": "aaaaaaaaaaaa",
      "host": "claude",
      "session_id": "11111111-1111-4111-8111-111111111111",
      "project": "/home/alice/proj",
      "kind": "user_correction",
      "count": 2,
      "first_ts": "2026-10-28T01:00:00Z",
      "last_ts": "2026-10-28T01:00:05Z",
      "episodes": [
        {"source_path": "/home/alice/.claude/projects/-home-alice-proj/aaaa.jsonl", "line": 3, "ts": "2026-10-28T01:00:00Z", "excerpt": "合成摘录，不会被复制"},
        {"source_path": "/home/alice/.claude/projects/-home-alice-proj/aaaa.jsonl", "line": 9, "ts": "2026-10-28T01:00:05Z", "excerpt": "合成摘录，不会被复制"}
      ],
      "same_project": []
    }
  ],
  "sources_unavailable": []
}
```

Host 候选 JSON 只有三个字段：

```json
{
  "groups": ["aaaaaaaaaaaa"],
  "hypothesis": {
    "observation": "同一会话里连续三次纠正同类格式问题",
    "change": "把该格式约束写成一条更短的规则",
    "expected_effect": "同类纠正减少，测试通过数不下降"
  },
  "predictions": [
    {
      "metric": "test_pass_v1",
      "expected_change": "不下降",
      "falsified_when": "处理组比基线低 2 次以上"
    }
  ]
}
```

字段约定：

- `groups` 必填，显式选中的 `correlation_group` 数组，不能为空。组不存在，或同一个 ID 在输入里对应多份不同内容（歧义），直接拒绝，不猜。
- `hypothesis` 可缺。`observation`、`change`、`expected_effect` 缺失、为 `null` 或空串时保存为 `null`，并在报告里逐项列出。
- `predictions` 可缺。每一项的 `metric` 必填，且必须是模板 `metrics` 里已有的键（`test_pass_v1`、`calibration_contract_pass_v1`、`total_tokens_v1`、`wall_time_v1`、`estimated_cost_usd_v1`）；`expected_change`、`falsified_when` 可缺并报告。白名单只取仓库模板 [examples/m-1/proposal.template.json](../examples/m-1/proposal.template.json)，`draft` 和 `check` 同源：往草稿自己的 `metrics` 里加键不能放行未知指标。
- 未支持字段一律拒绝：Host 自填 `evidence`、`retired` 声明、`measured_value` 等都不接收。证据只从 `--groups` 解析。
- 损坏 JSON、畸形类型（例如 `groups` 不是数组、`hypothesis` 不是对象、`line` 不是正整数）退出 1，不写任何产物。

### 生成的提案

- 形状取自 [examples/m-1/proposal.template.json](../examples/m-1/proposal.template.json)，不裁剪 `metrics`。只改 `experiment_id`、`status=draft`、`paths.artifact_root_relative_to_phe_repo`，并新增 `candidate`。
- M-1 专属段（`target`、`task`、`rules`、`harness`、`experiment`、`gates` 等）保持模板原值或 `null`。本切片不填这些字段，也不把它们当作 M3a 的执行计划或冻结值。
- `candidate.evidence` 绑定分组文件的原始字节 sha256、选中组 ID 和指针（`source_path`、`line`、`ts`）。`count` 由实际指针数推导，不采用输入里的计数字段；`check` 要求 `count` 等于 `pointers` 数量、同一组 ID 只出现一次、`selected_group_ids` 与 `groups` 顺序和内容一致，编辑后任何一项不一致都拒绝。不复制 `excerpt`，不读会话原文。
- `candidate.host_judgment.source = "host_judgment_not_measurement"`：假设与预测是 Host 判断，不是实测。
- `candidate.retired = {"status": "unavailable", "reason": "尚无Retired数据源及检索实现"}`。不写空 `matches`，也不接受 Host 声明已经检索。

### stdout 报告

`draft` 和 `check` 输出同一形状的 JSON，保存/检查成功与可冻结明确分开：

| 键 | 含义 |
|---|---|
| `action` | `draft` 或 `check` |
| `saved` / `checked` | 本次写入 / 只读检查成功；只出现对应的一个，恒为 `true`（失败不输出报告，退出码非 0） |
| `freeze_ready` | 本切片恒为 `false`（旧提案不在范围内时为 `null`） |
| `freeze_ready_blockers` | `retired_unavailable`、`candidate_incomplete`、`template_null_fields` 等阻塞标识 |
| `missing` | 候选段缺项：假设字段、预测列表或预测字段为空 |
| `null_fields` | 模板区递归扫描到的 `null` 字段路径（不含 `candidate` 段） |
| `retired` / `evidence` | 绑定信息原样回显，便于人工核对 |
| `notes` | 检查范围、Hash 语义与 Host 判断的说明 |

退出码：`0` 成功；`1` 输入或结构错误；`2` 路径、覆盖或软链逃逸拒绝。失败时只写 stderr，不留下半份产物。

### 路径与覆盖

- `--experiment-id` 只接受字母数字开头、后续允许 `.` `_` `-` 的目录名；含路径分隔符、`..` 或前导点直接拒绝。
- `experiments/` 本身是软链、不是目录时拒绝写入；实验目录已存在（含大小写变体、冻结目录）时拒绝覆盖。
- 先完成全部输入校验，再创建目录；写入用同目录临时文件加原子替换。写入失败时删除本次新建的实验目录。

### 检查范围

- `check` 只读 `experiments/<id>/proposal.json` 和仓库模板的 `metrics`（metric 白名单与 `draft` 同源），不回读分组文件、会话原文或任何 Host 配置，不写侧文件、`approval.json` 或 `proposal.sha256`，也不运行 `run` / `judge`。
- `draft` 和 `check` 在导入相邻模块前关闭字节码缓存，不在仓库副本里留下 `__pycache__` 或 `.pyc`；只读性用不排除任何文件的完整文件树快照核对。
- 没有 `candidate` 段的旧提案不在检查范围：`scope=legacy_proposal_out_of_scope`、`freeze_ready=null`。新检查不使旧提案失效。
- 报告只覆盖候选段和明显的未填项，不是完整协议验证。分组文件 sha256 只绑定输入字节，不验证语义质量、证据强度、指标选择或预测是否合理；指针仍可能在源文件变化后失效。
- 本切片没有 Retired 数据源和检索，所以 `freeze_ready` 永远不是 `true`。
- Skill 口径：带 `candidate` 的草稿进入 freeze 前必须先 `check`，未闭合不得冻结。这是流程约束，不是执行层封锁——本切片不阻止宿主手工写 `proposal.sha256`。

### 隐私

- 只复制指针和分组元数据，不复制摘录或会话正文；`excerpt` 留在被 Git 忽略的 `episodes/` 产物里。
- 示例和测试全部手写合成，不含真实路径、用户名、邮箱或会话内容。`experiments/` 仍被 Git 忽略。

### 验证

```bash
python3 -m unittest discover -s tests -t tests
ruff check .
ruff format --check .
```

`tests/proposal_fixture.py` 提供临时仓库副本和合成输入（`tools/` 与 `examples/` 复制到临时目录），`tests/test_proposal.py` 与 `tests/test_proposal_check.py` 走真实 CLI：有效引用保存、缺假设/预测报告、未知 metric / 组不存在 / 组歧义 / 畸形输入拒绝、Retired 永远阻塞、已有或冻结目录不覆盖、目录越界拒绝；`check` 拒绝编辑后的草稿（改自己 `metrics` 放行未知指标、`count` 与指针数不符、组 ID 重复）、不回读分组文件，且 `draft`/`check` 结束后完整文件树里没有字节码缓存；旧提案不在范围。可复跑的脱敏验收记录写在仓库外的临时目录。

## 不做（切片 A）

- 不实现 Retired 检索、`reopen` / `alternative` 关系或空 `matches` 占位。
- 不填 M-1 专属段，不生成冻结侧文件，不批准，不运行 Target 或调用模型。
- 不复制或提交真实会话、真实分组产物或私人实验数据。

## 切片 B1：显式 Retired 检索输入与关系核对（完成）

### 命令

```bash
python3 -I tools/proposal.py draft \
    --groups <分组 JSON> \
    --candidate <Host 候选 JSON> \
    --retired <Retired 检索 JSON> \
    --experiment-id <新实验 ID>

python3 -I tools/proposal.py check --experiment-id <已有实验 ID>
python3 -I tools/proposal.py check --experiment-id <已有实验 ID> --retired <Retired 检索 JSON>
```

`--retired` 在两条命令里都是可选的。`draft` 不带它时保留切片 A 的 `unavailable`。`check` 不带它时不回读任何外部输入；带它时只复核来源字节 Hash 和记录一致性，不更新 proposal。

### Retired 检索输入（schema 版本 1）

输入是 Host 显式给出的一个有限 JSON 文件。工具按文件里的顺序逐条列举**全部** `entries`，不推断语义相关、不排序、不截断、不选 topK，也不扫描用户目录或真实退役记录。示例见 [examples/m3a/retired.sample.json](../examples/m3a/retired.sample.json)。

```json
{
  "schema_version": 1,
  "scope": "合成示例：本文件只列出提供者给出的 2 条退役记录，不代表完整历史",
  "entries": [
    {
      "retired_id": "retired-format-guard-001",
      "method": "把格式要求展开成整段长规则",
      "rejected_reason": "规则过长，同类纠正次数没有下降",
      "retired_at": "2026-10-20T00:00:00Z",
      "evidence_groups": ["dddddddddddd"]
    }
  ]
}
```

| 字段 | 约定 |
|---|---|
| `schema_version` | 必须是 JSON 整数 `1`；`1.0`、`1e0`、`true` 和 `2` 都拒绝，不做迁移 |
| `scope` | 必填非空字符串。它只是提供者声明的检索范围，**不表示完整历史** |
| `entries` | 必填数组，合法空数组就是真正完成过的检索结果（`completed`），但只代表该文件为空 |
| `retired_id` | 稳定 ID，同一文件内重复即拒绝 |
| `method` | 被退役方法的人类可读描述 |
| `rejected_reason` | 当初否决它的原因 |
| `retired_at` | 退役时间，带时区的 ISO 日期时间（`Z` 或 `+08:00`）；无时区、不可解析都拒绝 |
| `evidence_groups` | 退役时可用的证据 `correlation_group` ID 数组，可以为空；同一组 ID 重复即拒绝 |

`draft --retired` 的三种输入结果：

| 输入状态 | 行为 |
|---|---|
| 未提供 `--retired` | 保存 `candidate.retired = {"status": "unavailable", "reason": "未提供 --retired 文件，本工具不做自动检索或扫描"}` |
| 路径存在但读不到（缺失、目录、无权限） | 照常保存草稿，`status = unavailable` 并把原因写进 `reason`；**绝不能写成 no_match** |
| 读不到但 Host 已写 `retired_review` | 退出 1，不写产物：没有可核对的检索记录，不接受无法核验的关系声明 |
| 可读但损坏或不合法（坏 JSON、错版本、缺字段、非法时间、重复 `retired_id`、未支持字段） | 退出 1，不写任何产物 |

### `candidate.retired`

```json
{
  "status": "completed",
  "source": {"path": "<命令行给出的路径>", "sha256": "<文件原始字节 SHA-256>"},
  "scope": "all_entries_in_supplied_file",
  "supplied_scope": "<文件里的 scope>",
  "entries": [
    {
      "retired_id": "retired-format-guard-001",
      "method": "把格式要求展开成整段长规则",
      "rejected_reason": "规则过长，同类纠正次数没有下降",
      "retired_at": "2026-10-20T00:00:00Z",
      "evidence_groups": ["dddddddddddd"]
    }
  ]
}
```

- 状态只有判别清楚的两种：`unavailable`（只有 `status`、`reason`）和 `completed`（附来源、范围、记录）。没有 `matches`、`top_k` 或 `no_match` 之类的模糊状态。
- `scope = all_entries_in_supplied_file` 由工具写死，表示“这份文件被完整列举”，不是语义搜索。`supplied_scope` 原样回显提供者声明。
- `entries` 与来源逐字段一致（只保留上表字段），不复制摘录或额外元数据。`status = completed` 且 `entries = []` 只表示该文件为空。
- `source.sha256` 只绑定来源文件的原始字节，不验证内容是否真实、是否完整或语义是否独立。

### Host 候选 JSON 的 `retired_review`

Host 只在候选 JSON 里声明关系，不写 `matches`、`status` 或任何检索结果字段。`unrelated` / `alternative` / `reopen` 是 Host 判断，不是检索无命中的证据。

```json
{
  "groups": ["aaaaaaaaaaaa", "bbbbbbbbbbbb"],
  "hypothesis": {"observation": "…", "change": "…", "expected_effect": "…"},
  "predictions": [{"metric": "test_pass_v1", "expected_change": "不下降", "falsified_when": "处理组比基线低 2 次以上"}],
  "retired_review": {
    "relation": "alternative",
    "reason": "本候选用更短的规则达到同一目的，与被退役的长规则不同",
    "entries": [
      {
        "retired_id": "retired-format-guard-001",
        "relation": "alternative",
        "reason": "同一目标，机制不同，且用了退役之后的新证据",
        "new_evidence_groups": ["aaaaaaaaaaaa"]
      },
      {
        "retired_id": "retired-checklist-002",
        "relation": "unrelated",
        "reason": "对象不同：本候选不涉及输出前检查清单"
      }
    ]
  }
}
```

| 规则 | 行为 |
|---|---|
| 总体 `relation` / `reason` | 必须声明，缺失或空串保存为 `null` 并列入 `missing`。`entries` 为空（没有检索记录）时总体关系只能是 `unrelated`，否则拒绝 |
| 逐条 review | 以 `retired_id` 对应每条检索记录。缺少某条记录的 review、缺少该条的 `relation` / `reason` 都保存并列入 `missing` |
| 非法值 | `relation` 不在三个取值里、`retired_id` 不存在、review 里 `retired_id` 重复、`new_evidence_groups` 引用当前候选未选中的分组、出现未支持字段（含 `user_authorized`、`matches`、`status`）都退出 1，不写产物 |
| `unrelated` | 不需要新证据；检索到的记录仍原样保留在 `candidate.retired.entries` 里 |
| `alternative` / `reopen` | 必须给出 `new_evidence_groups`（至少一个当前候选已选分组）。缺了列 `retired_new_evidence_required` 阻塞 |
| 新证据时序 | 每条引用分组必须 **不** 在该记录的 `evidence_groups` 里（否则 `retired_evidence_group_reused`），且分组里每个指针的时间都必须晚于 `retired_at`（时间未知 `retired_evidence_time_unknown`，不晚于 `retired_evidence_not_after_retirement`）。时间取自 proposal 里可编辑的指针，见下方「时序证据的边界」。这些只列阻塞，不拒绝保存 |
| 未提供的边界 | 只检查上述必要结构和时序：不判断新证据是否语义独立、是否充分，也不接受 `user_authorized=true` 之类的手动恢复声明（手动恢复不在本切片） |

### 检查与报告

`draft` 和 `check` 共用同一套结构校验（`tools/proposal_retired.py`），因此手工编辑过的 proposal 也按同一规则检查，不信任草稿里自造的枚举或模板。

**时序证据的边界：** 时序检查只读 `candidate.evidence.groups[].pointers[].ts`——这是 proposal 内可编辑的字段。默认 `check` 和 `check --retired` 都**不**回读原始分组文件、不复核原始时间，也不重算分组文件的 sha256；`retired_source_check` 的 `unchanged` 只说明 Retired 来源文件字节未变，不代表分组时间或原始证据被复核。所以 Host 把 pointers 的时间改到 `retired_at` 之后就能消掉时序阻塞，这不会被工具发现。报告 `notes` 逐条写明这两条限制。这是本切片的已知缺口，不是执行层保护；原始时间复核原计划属于 D（冻结前置检查），现已随完整冻结暂停；闭环报告改由诊断者按指针回读上下文核对。

`freeze_ready` 在切片 B1 仍恒为 `false`：`freeze_ready_blockers` 固定包含 `prediction_freeze_not_implemented`（预测冻结和正式核验未实现），再叠加下面这些 Retired 相关项：

| 阻塞标识 | 含义 |
|---|---|
| `retired_unavailable` | 没有可用的 Retired 检索结果（未提供、读不到，或旧草稿） |
| `retired_review_incomplete` | 总体或逐条关系、理由缺项 |
| `retired_new_evidence_required` | 声明 `alternative` / `reopen` 却没给新证据分组 |
| `retired_evidence_group_reused` | 新证据引用了该记录退役时已有的分组 |
| `retired_evidence_time_unknown` | 引用分组的时间缺失或不可解析 |
| `retired_evidence_not_after_retirement` | 引用分组不晚于 `retired_at` |

报告在切片 A 的键之外新增：

| 键 | 含义 |
|---|---|
| `retired` | 存储的 `candidate.retired` 原样回显（检索事实） |
| `retired_review` | 规范化的 Host 关系判断；未提供时为 `null` |
| `retired_findings` | 逐条关系问题的可读说明（含 `retired_id`），与上面的阻塞标识对应 |
| `retired_source_check` | `draft` 为 `not_performed`；默认 `check` 为 `not_performed` 并说明**默认 check 不回读外部输入，无法检测外部变化**；`check --retired` 为 `unchanged` / `changed` / `unavailable`。`unchanged` 只覆盖 Retired 来源文件，不覆盖分组时间 |

`check --retired <file>` 只读复核：重算来源字节 sha256 并与草稿绑定比对，重新解析记录并与存储的 `entries`、`supplied_scope` 比对。`changed` 或 `unavailable` 时报告 `action_required: re_search_required`、`proposal_updated: false`，退出 1；报告仍是完整 JSON（与 `draft` 输入错误不同：这是已完成的复核结论，不是输入被拒绝），proposal 字节不变。复核通过时 `status: unchanged`、退出 0。来源不可用或损坏不写成 no_match，也不自动改写草稿。草稿本身是 `unavailable`（旧切片 A 草稿）时，`check --retired` 只回 `not_performed` 并说明无需复核，不迁移、不重写。

### 完整可跑示例

示例文件全部手写合成，不含真实退役记录、真实路径或会话内容：

- [examples/m3a/groups.sample.json](../examples/m3a/groups.sample.json)：切片 A 的分组输入
- [examples/m3a/candidate.sample.json](../examples/m3a/candidate.sample.json)：带 `retired_review` 的 Host 候选
- [examples/m3a/retired.sample.json](../examples/m3a/retired.sample.json)：Retired 检索输入

```bash
cd <仓库副本>
python3 -I tools/proposal.py draft \
    --groups examples/m3a/groups.sample.json \
    --candidate examples/m3a/candidate.sample.json \
    --retired examples/m3a/retired.sample.json \
    --experiment-id m3a-b1-demo
python3 -I tools/proposal.py check --experiment-id m3a-b1-demo \
    --retired examples/m3a/retired.sample.json
```

示例的 `retired_review` 把一条记录判为 `alternative`（引用的分组时间晚于 `retired_at`，且不在该记录的 `evidence_groups` 里），另一条判为 `unrelated`。因此示例报告里 `retired_findings` 为空，但 `freeze_ready` 仍是 `false`。

### 验证

```bash
python3 -m unittest discover -s tests -t tests
ruff check .
ruff format --check .
```

`tests/proposal_fixture.py` 增加合成 Retired 输入，`tests/test_proposal_retired.py`（draft）与 `tests/test_proposal_retired_check.py`（check）走真实 CLI 覆盖：未提供/不存在/目录来源保存 `unavailable` 且不是 no_match、合法空来源需要 Host 声明并保留阻塞、畸形/重复/非法字段拒绝、记录逐条列举且理由来自源、命中缺关系阻塞、`unrelated` 保留命中事实、`reopen`/`alternative` 缺新证据/旧时间/同组阻塞、有效新证据放行（仍不可冻结）、伪造 ID/字段拒绝、显式 `check --retired` 报告来源变化且文件不变、默认 `check` 不回读来源、改草稿指针时间可消掉时序阻塞且报告写明限制、`schema_version` 为 `1.0` / `1e0` / `true` 时退出 1 且不留产物、旧 `unavailable` 草稿照常阻塞、全树无 `.pyc`。示例文件本身也由一条 CLI 测试保证可跑。

### 本切片的边界

- 没有自动 Retired 检索或数据源接入（B2 未做），不扫描目录，不猜相关。
- 不判断新证据的语义独立性或充分性，不实现手动恢复授权（C 未做），`user_authorized` 之类字段直接拒绝。
- 预测冻结、冻结侧文件、正式核验未实现（D 未做），`freeze_ready` 恒为 false。
- 不迁移或重写切片 A 的旧 `unavailable` 草稿；它们照常列 `retired_unavailable` 阻塞。
- 不新增目录覆盖能力：`experiments/<id>` 已存在或已冻结时仍拒绝写入。
- B1 当时未修改 M2 文档、资产登记表或 PHE_SPEC；本轮闭环调整不改 M-1 模板。
