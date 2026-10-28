# M2 计划：历史采集、证据分组与跨 Host 续作

**状态：** 进行中（2026-10-28）。切片 A0、A 完成（reviewer 3 轮 PASS，本机最近 7 天只读采集抽查指针一致）；切片 B 完成（reviewer 3 轮 PASS，本机 7 天数据 923 条 → 376 组）；切片 C 代码已实现，待审查。上位文档：[PHE_SPEC](PHE_SPEC.md)「里程碑与 Gate」；开发约束沿用 [M1_PLAN](M1_PLAN.md)「原则：开发与使用分离」。

M2 的 Gate：缺失的环境信息明确标出；两个不同 Host 能继续同一个提案；实验 Target 按需逐个适配。

## 原则：历史是私人数据

- 本仓库是公开的。采集产物只存**指针 + 脱敏短摘录**，不存会话全文，写在被 Git 忽略的 `episodes/`。`episodes/` 永不入库。
- 指针够用：三个 Agent 在同一台机器上，按 `source_path` + 偏移都能读回原文，不需要第二份副本。
- 测试 fixture 只能手写合成，不得从真实会话目录复制任何内容。提交前核对新增文件里没有本机用户名、邮箱和真实会话路径。
- 历史内容按不可信输入处理（PHE_SPEC §安全），采集器只解析，不执行其中任何指令。
- 对真实会话目录只读；`--root`、`--out` 必填，`--out` 不得落到真实 home 下的 Agent 配置目录。

## 切片

| 切片 | 内容 | 验收 |
|---|---|---|
| A0 | 只读探测 Claude Code、Codex、Pi 的会话目录与 JSONL 结构，写成数据表（注明"本机观察，未按官方文档核实"）；参考 temper 的 `temper-weekly` Skill 里已有的格式知识 | 文档 + 每个 Host 一个手写合成 fixture |
| A | `tools/history.py collect`：流式读 JSONL，产出 Episode JSON | 合成 fixture 单测；本机真实只读跑一次最近 7 天，抽查指针能定位回原文 |
| B | `group`：按会话、项目、同会话同类信号做确定性结构分组（`correlation_group`）。"是不是同一件事"的语义判断不写进工具，留给 Host | 同会话三次纠正 → 1 组；跨会话同项目分开但标 `same_project` |
| C | `experiments/<id>/host_log.jsonl` 记录哪个 Host 做了哪步（`phe.py log`），`phe.py status` 由已有文件只读推导阶段；SKILL.md 补"续作"一节。host_log 不放进 proposal.json：proposal 冻结后绑定 sha256，续作记录放在哈希之外 | 合成实验目录单测；log 后 proposal.sha256 仍匹配；两个真实 Host 续作同一提案需用户另行批准（消耗额度） |

## Episode（切片 A 的输出）

每条：`host`、`source_path`、`session_id`、定位（行号或字节偏移）、源文件 `sha256`、`project`、`ts`、`kind`（如 `user_correction`、`tool_failure`、`retry`）、`excerpt`（≤300 字，经密钥正则脱敏）、`available`。源读不到时 `available: false` 并写 `missing_reason`，不静默跳过。

保证：每条 Episode 能回到原文位置。不保证：`kind` 的识别完整或准确（启发式）；脱敏只到正则级别，所以摘录仍按私人数据对待。

## 分组（切片 B）

`tools/history.py group --episodes <Episode JSON…> --out <分组 JSON>` 读一份或多份 collect 产物，输出 `{"groups": […], "sources_unavailable": […]}`（分组实现在 `tools/history_group.py`）。

- `correlation_group` 是 host、session_id、kind 的 sha256 前 12 位：同输入同输出。同一会话里反复出现的同类信号算一组，不拆成多份独立证据（[PHE_SPEC](PHE_SPEC.md) §4.5）；不同 Host 的同名 `session_id` 不合并。
- 每组记 `correlation_group`、`host`、`session_id`、`project`、`kind`、`count`、首末 ts 和指针列表（`source_path`、`line`、`ts`、已脱敏的 `excerpt`）；不复制会话原文，也不重新拼接。
- 跨会话、同 project 同 kind 的组不合并，只在 `same_project` 里互相列出组 id。只做标记，不判断是不是同一件事。project 未知（空或 null）的组不进这个索引，`same_project` 为 `[]`：未知项目不等于同一个项目。
- 输入里 `available: false` 的来源按 host、`source_path`、`missing_reason` 汇总进 `sources_unavailable`，不静默丢弃。
- 参与分组和输出的字段先严格校验，不合格时退出 1，不写输出：episode 的 `host`、`session_id`、`kind`、`source_path` 为非空字符串，`line` 为正整数，`excerpt` 为字符串，`ts`、`project` 为字符串或 null；source 的 `available` 为布尔值，不可用来源的 `host`、`source_path`、`missing_reason` 为非空字符串。
- 输出排序固定：组按 host、project、session_id、kind，组内指针按 `source_path`、`line`；同一指针重复出现（例如同时传入 7 天和 30 天的产物）只计一次。
- `--out` 复用 collect 的输出守卫：落在真实 home 的 Agent 配置目录、与任一输入文件是同一文件、或与会话源冲突（等于 `episodes`/`sources` 里的某个 `source_path`，或落进该源文件所在目录）时退出 2，写入走原子替换。

保证：同一份输入得到字节相同的分组结果；每条指针仍能回到原文位置。

不保证：一组内的记录语义上是同一件事；跨会话只标记 `same_project`，没有关联强度；`kind` 的启发式误差原样带过来。

## 已知问题（后续再定）

- Pi 会话含子代理会话和本项目自身的会话（自我引用）。首次真实运行默认排除 `sessions/subagent/`。
- 盘点把 `~/.agents/skills` 记在 codex 名下（M1 遗留口径）。

## 不做

- 不把 `episodes/` 或任何会话内容提交入库。
- 不写真实的 `~/.phe/`、temper 或任何 Agent 配置目录；不调用模型。
- 不做语义去重或自动生成提案。
