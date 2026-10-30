# M3a 计划：从经验扫描产生可证伪提案

**状态（2026-10-30）：** 进行中。切片 A 完成：本地草稿 CLI `tools/proposal.py draft/check`。切片 B（Retired 真实检索）、C（reopen / alternative 处理）、D（完整预测冻结）未开始。**M3a 未完成**，不能把切片 A 的报告当作完整协议验证。

**上位文档：** [PHE_SPEC](PHE_SPEC.md)「里程碑与 Gate」「独立证据与 Retired 账本」；历史输入格式见 [M2_PLAN](M2_PLAN.md)、[M2_HISTORY_FORMATS](M2_HISTORY_FORMATS.md)。

M3a 的 Gate：使用固定指标、冻结预测和 Retired 检索；不自动发布。切片 A 只完成第一条链路：把 M2 的结构分组结果变成一份本地草稿，并只读报告明显未闭合项。

## 切片

| 切片 | 内容 | 状态 | 验收边界 |
|---|---|---|---|
| A | `tools/proposal.py draft/check`：Host 显式选组、写假设与预测，CLI 生成 `experiments/<id>/proposal.json` 并只读检查 | 完成 | 合成 CLI 测试；不联网、不调用模型、不读会话原文 |
| B | Retired 真实检索：负样本数据源、匹配规则、关系输出 | 未开始 | 需要先定数据源和隐私边界 |
| C | `reopen` / `alternative` 处理：重新启用已退役方案时要求新证据或用户授权 | 未开始 | 依赖 B 的负样本 |
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

## 不做（本切片）

- 不实现 Retired 检索、`reopen` / `alternative` 关系或空 `matches` 占位。
- 不填 M-1 专属段，不生成冻结侧文件，不批准，不运行 Target 或调用模型。
- 不复制或提交真实会话、真实分组产物或私人实验数据。
