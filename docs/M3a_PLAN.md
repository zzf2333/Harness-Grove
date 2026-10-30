# M3a 计划：从经验扫描产生可证伪提案

**状态（2026-10-30）：** 进行中。切片 A 完成：本地草稿 CLI `tools/proposal.py draft/check`。切片 B1 完成：显式 Retired 检索文件的逐条列举与关系核对（`--retired`、`retired_review`）。切片 B2（真实退役数据源与自动检索）、C（独立证据判定）、D（完整预测冻结）未开始。**M3a 未完成**，不能把 A/B1 的报告当作完整协议验证。

**上位文档：** [PHE_SPEC](PHE_SPEC.md)「里程碑与 Gate」「独立证据与 Retired 账本」；历史输入格式见 [M2_PLAN](M2_PLAN.md)、[M2_HISTORY_FORMATS](M2_HISTORY_FORMATS.md)。

M3a 的 Gate：使用固定指标、冻结预测和 Retired 检索；不自动发布。切片 A 只完成第一条链路：把 M2 的结构分组结果变成一份本地草稿，并只读报告明显未闭合项。

## 切片

| 切片 | 内容 | 状态 | 验收边界 |
|---|---|---|---|
| A | `tools/proposal.py draft/check`：Host 显式选组、写假设与预测，CLI 生成 `experiments/<id>/proposal.json` 并只读检查 | 完成 | 合成 CLI 测试；不联网、不调用模型、不读会话原文 |
| B1 | 显式 Retired 检索文件：`draft --retired <JSON>` 逐条列举、`retired_review` 关系核对、`check --retired` 显式复核来源 | 完成 | 只处理提供者给出的有限文件；不扫描目录、不推断语义相关、不截断 |
| B2 | Retired 真实数据源与自动检索：负样本来源、匹配规则、扫描范围 | 未开始 | 需要先定数据源和隐私边界 |
| C | 独立证据判定：重新启用已退役方案时判断新证据是否独立、是否充分 | 未开始 | 依赖 B2 的负样本 |
| D | 完整预测冻结：候选段闭合、冻结侧文件、冻结前置检查 | 未开始 | 依赖 A–C；纳入执行层核对 |

M3a 的 Gate 在 B–D 完成后才能核对。切片 A 不改变 Gate 状态。

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

**时序证据的边界：** 时序检查只读 `candidate.evidence.groups[].pointers[].ts`——这是 proposal 内可编辑的字段。默认 `check` 和 `check --retired` 都**不**回读原始分组文件、不复核原始时间，也不重算分组文件的 sha256；`retired_source_check` 的 `unchanged` 只说明 Retired 来源文件字节未变，不代表分组时间或原始证据被复核。所以 Host 把 pointers 的时间改到 `retired_at` 之后就能消掉时序阻塞，这不会被工具发现。报告 `notes` 逐条写明这两条限制。这是本切片的已知缺口，不是执行层保护；原始时间复核属于 D（冻结前置检查）。

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
- 不修改 M2 文档、资产登记表或 PHE_SPEC；本轮不改 M-1 模板。
