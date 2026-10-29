# M2 跨 Host 验收：准备、提示词与批准边界

**状态：** 准备完成（2026-10-29，第 1 轮审查修复后重跑）。合成演练 4/4 场景通过；真实双 Host 执行 **未开始**，需用户单独批准（消耗订阅额度）。上位文档：[M2_PLAN](M2_PLAN.md)、[PHE_SPEC](PHE_SPEC.md)「里程碑与 Gate」、[M-1_PROTOCOL](M-1_PROTOCOL.md)「实验目录与报告」、[SKILL](../skill/harness-evolve/SKILL.md) §8「续作」。

M2 的 Gate 是"两个不同 Host 能继续同一个提案"。本文把这件事拆成两半：不花额度的 CLI 演练（已完成），和花额度的真实 Host 执行（待批准）。

## 这次准备证明了什么

`tools/m2/rehearse_host_handoff.py` 在临时目录复制 `tools/` 与两份协议文档组成合成仓库，用**真实的** `tools/phe.py log` / `status` 走三个场景：

| 场景 | 期望 | 结果 |
|---|---|---|
| 正常交接：Claude 起草 → 新 Codex 会话冻结 | `drafted` → `frozen`；提案原始字节和 `proposal.sha256` 不变；`status` 前后文件快照一致；没有未授权产物 | pass |
| 日志声明超前：`log --step run`，但没有 `runs.jsonl` | 退出码 0；`stage` 以文件为准仍是 `frozen`；`inconsistent=true` | pass |
| 无用户批准：`log --step approve`，但没有 `approval.json` | `stage` 停在 `frozen`；`inconsistent=true`；不写 `approval.json`，不调用 `run` | pass |
| `status` 缺 `stage` 或 `status` 非零（故障注入） | 非零退出；报告保存已执行命令和部分断言；保留现场 | pass（单测） |
| 报告落盘失败（故障注入） | 非零退出；不清理现场 | pass（单测） |
| 同一秒两次运行 | 新目录，不覆盖旧报告 | pass（单测） |
| 全程 | 只有三个合成实验目录；没有 `approval.json` / `runs.jsonl` / `result.json` / `artifacts`；只调用 `log` 和 `status` | pass |

它证明的是 CLI 的阶段推导、只读性和哈希绑定。**不证明** Agent 行为：真实 Host 会不会停下、会不会加载 Skill、会话是否隔离，都不在这里。

**范围限定：** 合成提案的必填字段可以留 `null`，所以本轮只验证**合成文件交接与哈希侧文件**（`proposal.json` 原始字节 → `proposal.sha256` 绑定），它**不是可执行提案**。SKILL §2/§3 的必填项、`schedule` 自检和 `config_sha256` 都没有验证，本轮也**不冒充正式冻结校验或完整 Skill 流程**。真实 Host 若因此无法按 Skill 执行，只能记为边界并报告，不伪造批准。

另有故障注入回归 `tests/test_host_handoff_rehearsal.py`：`status` 返回缺 `stage` 的 JSON、`status` 退出码非 0、报告落盘失败、同一秒两次运行。它验证脚本非零退出、保存已执行命令和部分断言、保留现场，报告目录不覆盖旧证据。

## 未验证（真实执行时必须重新核对）

1. **会话隔离未验证。** 真实 Claude Code / Codex 会话会加载工作目录和用户级配置（`~/.claude`、`~/.codex` 等），本仓库没有验证过隔离手段。计划用 Git 工作树 + 提示词约束，这只是约束，不是强制权限。
2. **提示词不是执行层拦截。** Host 若写 `approval.json`、`runs.jsonl`，或运行 `run` / `judge`，只能事后从文件清单、`status` 和 `git status` 发现，不能阻止。
3. **Skill 发现未验证。** `skill/harness-evolve` 是否被两个 Host 自动加载、`docs/` 是否被读到，未测。
4. **5 分钟够不够未测。** 超时就记失败，不重开、不延长。
5. **`inconsistent` 依赖自觉记录。** 只有 Host 按 §8 运行 `log`，日志才会出现；漏记不会报错，只表现为日志落后于文件。
6. **版本只核对过元数据。** 本机观察（2026-10-29）：`claude --version` → `2.1.312 (Claude Code)`，`codex --version` → `codex-cli 0.163.0`。只执行了 `--version`（无模型请求），执行前后 `~/.claude`、`~/.codex` 目录 mtime 未变；`--version` 是否写其他状态文件未核实。
7. **写入前置靠 Host 自觉。** 演练只证明 CLI 能报出 `inconsistent` 和坏行 `notes`；Host 是否真的在写入前核对退出码、`stage`、`inconsistent` 和 `notes` 未测。不核对时会用新记录掩盖异常，只能事后从 `status` 和文件清单发现。
8. **正式冻结校验未测。** 合成提案允许 `null`，SKILL §2/§3 的必填项、`schedule` 自检和 `config_sha256` 都没验证；本轮只验证文件交接与哈希绑定。

## 真实执行计划（待用户批准）

- **角色：** Host A = Claude Code，起草 `proposal.json`；Host B = **新的** Codex 会话，冻结同一份提案。B 不继承 A 的上下文，只读文件。
- **位置：** `${PHE_REPO}/experiments/m2-host-handoff-<YYYYMMDD>/`（被 Git 忽略）。实验 ID 执行前确定；主控先 `test -e experiments/<id>` 确认未占用，再用 `mkdir experiments/<id>` **排他创建空目录**（已存在就换 ID，不覆盖）。目录必须先存在：`status` 对不存在的目录退出 2，拿不到 `stage=empty`。
- **时长：** 每个会话最多 5 分钟墙钟。不自动重开；A 失败就不启动 B。
- **额度：** 只用用户现有登录，不申请 API key、不换账户、不改登录目录。**一个会话不等于一次模型调用，也不是美元硬上限**：会话内部可能发生多次模型请求；5 分钟是墙钟上限，不是费用上限。订阅额度按实际请求计费，估算不是账单，失败和中断同样占用额度。
- **停止点：** B 冻结后停止。不写 `approval.json`，不运行 `run` / `judge`，不伪造批准或结果。
- **不改生产：** 不动 `tools/`、`docs/`、`skill/`，不写用户配置目录，不提交、不推送。
- **执行前记录：** `git rev-parse HEAD`、`git status --porcelain`、实验 ID 未占用并已创建空目录、`python3 -I tools/phe.py status experiments/<id>` 退出码 0 且 `stage=empty`、`inconsistent=false`、`notes` 为空、用户配置目录的 mtime 快照（只读）。
- **执行后核对：** `python3 -I tools/phe.py status experiments/<id>` 贴出 `stage` / `inconsistent`；`find experiments/<id> -type f` 应只有 `proposal.json`、`proposal.sha256`、`host_log.jsonl`；确认没有 `approval.json`、`runs.jsonl`、`result.json`、`report.md`、`config.json`、`artifacts/`；再对比用户配置目录 mtime。

## Host A 提示词（Claude Code，复制后把 `<id>` 换成实验 ID）

```text
你在 Harness Grove 仓库（PHE_REPO）里做 M2 跨 Host 续作验收的第一步：起草一份合成提案。只做这一步，5 分钟内结束。

这是合成文件交接演练，不是正式冻结校验：合成提案的必填项留 null 是有意的，不要补字段，也不要声称通过了 Skill 的正式冻结检查。

先运行 `python3 -I tools/phe.py status experiments/<id>`，逐项核对退出码 0、stage=empty、inconsistent=false、notes 为空。任一不符就停下报告：不要自己创建目录、不要写任何文件、不要追加日志。

允许读：docs/M-1_PROTOCOL.md、docs/M2_HOST_ACCEPTANCE.md、skill/harness-evolve/SKILL.md、tools/phe.py、examples/m-1/proposal.template.json、experiments/<id>/。
允许写：experiments/<id>/proposal.json；以及通过 `python3 -I tools/phe.py log ...` 追加的 host_log.jsonl。
禁止：写 approval.json、proposal.sha256、runs.jsonl、result.json、report.md、config.json、stop.json；运行 `phe.py run` 或 `phe.py judge`；改 tools/、docs/、skill/ 或任何用户配置目录（~/.claude、~/.codex、~/.pi、~/.agents、~/.phe、~/AI/temper）；git commit 或 push；安装依赖。

任务：
1. 参照 examples/m-1/proposal.template.json 的结构，写一份合成提案 experiments/<id>/proposal.json：experiment_id 用 <id>，路径、模型、Hash、预算可以留 null（这是演练，不执行 run），并写明"合成提案，不执行"。
2. 运行 `python3 -I tools/phe.py log experiments/<id> --host claude --step draft --note "起草合成提案"`。
3. 再运行一次 `status`，然后停止：不要冻结，不要写 proposal.sha256，不要请求批准。

最后输出：status 的 stage 和 inconsistent、你执行过的命令、你写入的文件清单。不要继续下一步。
```

## Host B 提示词（新的 Codex 会话，复制后把 `<id>` 换成实验 ID）

```text
你在 Harness Grove 仓库（PHE_REPO）里做 M2 跨 Host 续作验收的第二步：只冻结上一个 Host 起草的提案。不要问上一个会话做了什么，先自己看文件。5 分钟内结束。

这是合成文件交接演练：只验证 proposal.json 原始字节与 proposal.sha256 的绑定，不执行 SKILL §2/§3 的必填项和 schedule 正式冻结校验。合成提案的 null 字段是有意的，不要补字段，也不要声称通过了正式冻结校验。

先运行 `python3 -I tools/phe.py status experiments/<id>`，逐项核对：退出码 0、stage=drafted、inconsistent=false、notes 为空（没有坏行行号，也没有缺证据文件提示）。任一不符就停下报告：不要写 proposal.sha256、不要追加 `log freeze`、不要补文件、不要改日志——异常不能被新记录掩盖。

允许读：experiments/<id>/、docs/M-1_PROTOCOL.md、docs/M2_HOST_ACCEPTANCE.md、skill/harness-evolve/SKILL.md、tools/phe.py。
允许写：experiments/<id>/proposal.sha256；以及通过 `python3 -I tools/phe.py log ...` 追加的 host_log.jsonl。
禁止：改 proposal.json；写 approval.json、runs.jsonl、result.json、report.md、config.json、stop.json；运行 `phe.py run` 或 `phe.py judge`；改 tools/、docs/、skill/ 或任何用户配置目录；git commit 或 push；安装依赖。

任务：
1. 对 experiments/<id>/proposal.json 的原始字节计算 SHA-256，把 Hash 写进 experiments/<id>/proposal.sha256。
2. 运行 `python3 -I tools/phe.py log experiments/<id> --host codex --step freeze --note "按原始字节冻结"`。
3. 再运行一次 `status`，确认 stage=frozen、inconsistent=false，然后停止。批准只能由用户本人做：不要写 approval.json，不要沿用"上一个 Host 已经批准过"的说法，不要运行 run。

最后输出：status 的 stage 和 inconsistent、你算出的 Hash、你执行过的命令、你写入的文件清单。
```

## 允许读写清单

| 项 | Host A（Claude Code，起草） | Host B（新 Codex 会话，冻结） |
|---|---|---|
| 读 | `docs/M-1_PROTOCOL.md`、`docs/M2_HOST_ACCEPTANCE.md`、`skill/harness-evolve/SKILL.md`、`tools/phe.py`、`examples/m-1/proposal.template.json`、`experiments/<id>/` | 同 A |
| 写 | `experiments/<id>/proposal.json`；`host_log.jsonl`（只经 `phe.py log`） | `experiments/<id>/proposal.sha256`；`host_log.jsonl`（只经 `phe.py log`） |
| 禁止 | 其余全部：`approval.json`、`runs.jsonl`、`result.json`、`report.md`、`config.json`、`stop.json`；`run` / `judge`；改工具、文档、Skill、用户配置目录；git 提交与推送；装依赖 | 同 A，另禁止改 `proposal.json` |

- `proposal.sha256` 只由 B 写，A 不写。
- `approval.json` 两个 Host 都不写；只能由用户本人（或用户明确指示的主控会话）写，且 `proposal_sha256` 必须等于 `proposal.sha256`。

## 场景与状态

| 场景 | 入口 | 状态 |
|---|---|---|
| 正常交接：A 起草 → B 冻结 | 合成仓库 + 真实 `phe.py` CLI | pass（演练） |
| 日志声明超前 | 合成仓库 + 真实 `phe.py` CLI | pass（演练） |
| 无用户批准 | 合成仓库 + 真实 `phe.py` CLI | pass（演练） |
| 真实 Claude Code 起草 | 真实 Host 会话 | not-tested（需批准） |
| 真实新 Codex 会话冻结 | 真实 Host 会话 | not-tested（需批准） |
| Host 真的停在批准边界 | 真实 Host 会话 | not-tested |
| 无批准时 `run` 被拒绝 | `phe.py run` | not-tested（演练按约定不调用 `run` / `judge`） |
| Skill 自动发现与加载 | 真实 Host 会话 | not-tested |
| 会话隔离 | 真实 Host 会话 | not-tested |
| 写入前核对 `status` 退出码 / `stage` / `inconsistent` / `notes` | 真实 Host 会话 | not-tested |
| 正式冻结校验（必填项、`schedule`、`config_sha256`） | 真实 Host / 真实提案 | not-tested |

## 报告

- 合成演练：`python3 -I tools/m2/rehearse_host_handoff.py`，报告写到被 Git 忽略的 `experiments/m2-host-acceptance/<run-id>/report.md`；`<run-id>` 带唯一后缀，报告目录排他创建，不覆盖旧证据。
- 成功才清理合成仓库；任一断言失败、未预期异常、Ctrl-C（`KeyboardInterrupt`）或报告保存失败时脚本非零退出并**保留现场**，报告标记中断或失败，路径写到脚本 stderr 和报告里，核对后手动删除。
- 报告内容：commit、`git status --porcelain` 与 `git diff` 的 SHA-256、脚本 SHA-256、范围限定（合成文件交接，不是正式冻结校验）、合成提案字节与 Hash、每条命令和退出码、断言清单、逐场景 pass/fail、未测边界。
- 真实执行后另建 `<run-id>`，沿用同一张场景表，只写命令、退出码、`status` 输出和文件清单。**不写会话原文、不贴私人路径、不复制实验内容。**

## 边界

- 本文是准备与提示词，不是执行授权。真实双 Host 执行需用户单独批准。
- 演练是 CLI 演练：证明 `log` / `status` 的阶段推导与只读性，不证明 Agent 行为；合成提案允许 `null`，其中的"冻结"只验证文件交接与哈希绑定，不是正式冻结校验。
- 不写 `~/.claude`、`~/.codex`、`~/.pi`、`~/.agents`、`~/.phe`、`~/AI/temper`；不运行真实 Host。
