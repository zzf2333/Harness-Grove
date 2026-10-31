# M0 计划：最小评测链路

**状态：** 完成条件 1、2、4 已达到（2026-10-28）；条件 3 只验证到 dry-run：Skill 已于 2026-10-28 经用户批准以软链安装到 `~/.claude/skills/harness-evolve` 和 `~/.agents/skills/harness-evolve`（指向本仓库 `skill/harness-evolve`，删软链即撤销），尚未在真实宿主会话里加载和走完流程。切片 A、B 完成；切片 C 第 1 次 c-001（fb77805d）因任务过难判 pivot，第 2 次 c-002（2010f3a9）在生产等价基线上判 go，真实规则信号 inconclusive。报告在被忽略的 `experiments/c-00{1,2}/report.md`。
**上位文档：** [PHE_SPEC](PHE_SPEC.md) 的「历史阶段与 Gate」、[M-1 协议](M-1_PROTOCOL.md)。

M0 把 M-1 的一次性脚本变成可复用的最小评测链路：宿主 Agent 能起草并冻结 proposal，调用同一套 Runner 和判定，读到报告。M0 不做发布，不改任何生产配置。

## 完成条件

1. 判定和调度的组名、次数、顺序、门槛和预算全部从 `proposal.json` 读取，脚本里没有写死的 `A1`、`25` 这类值。
2. 对 `experiments/spike-001` 重新判定，得到的 `result.json` 与原结果逐字段一致（故障可复现）。
3. 宿主 Agent 能按 `harness-evolve` Skill 的步骤完成起草 → 冻结 → 运行 → 读报告；没有批准 Hash 就不启动。
4. 在一个更难的新任务上，用不加人工控制的生产等价基线完成一次复验。

## 切片

| 切片 | 内容 | 验收 | 是否消耗额度 |
|---|---|---|---|
| A | `tools/phe.py`，子命令 `run` / `judge`，参数从 proposal 读取；门槛集带版本号 | 单元测试覆盖 go / pivot / invalid_run 和每条门槛单独失败；spike-001 重新判定结果一致 | 否 |
| B | `skill/harness-evolve/SKILL.md`：起草 → 冻结 → 用户批准 → `run` → `judge` | 已按 Skill 步骤用切片 A 的脚本走完 dry-run：`judge` 判 go；改错 approval Hash 后 `run` 在启动前被拒绝 | 否 |
| C | 生产等价基线复验：原版 AGENTS.md，A1/A2/B_real 各 5 次 | 按门槛 v2 得出判定和报告 | **是，需用户批准任务和额度** |

## 门槛 v2（只对新 proposal 生效，不回改 spike-001）

- spike-001 中，中性的冗余控制组 Token 也比基线低约 16%，和 B_real 差不多。v1 只检查冗余组的通过数，没检查它的 Token。
- v2：`N_cost = max(N_gap, 2·N_mad, |1 − T[B_redundant]/T[A]|)`；没有冗余组时退回 v1 公式。
- `follow_up_candidate` 的节约必须超过 v2 的 N_cost。
- `gates.version` 缺省按 v1；新 proposal 模板使用 v2。没有必要控制组或冗余控制组时，对应门槛标为 `not_applicable`。`proposal.rules` 不含 `required_control` 时，`contract_pass = test_pass`；有该规则时仍要求正确回执，回执原始字段始终记录。

## 选任务的要求（切片 C）

- 父提交上，oracle 至少有 2 个用例失败；修复提交至少涉及 2 个源文件。spike-001 的任务每组都是 5/5 通过，看不出质量差异。
- 优先找能找到用户原话或 reviewer 原始发现的任务。

## 工具权限（来自 spike-001 的 55 次 Bash 拒绝）

- 44 次是 Agent 按 Sutra `AGENTS.md`「每个切片完成后本地提交」想执行 `git add/commit`。这个行为来自被测 Harness，不是 Runner 缺陷。是否放行临时仓库内的提交，在切片 C 冻结前决定，并写进 proposal。
- 约 6 次是 `pnpm exec vitest`、`pnpm --dir … exec vitest` 和 `cd … &&` 组合命令。白名单补上 `Bash(pnpm exec vitest *)` 和 `Bash(pnpm --dir * exec vitest *)`。

## 已知缺口（留给 M1/M2）

- 选任务标准（≥2 个失败用例、≥2 个源文件）预测不了难度：c-001 基线 1/10，spike-001 与 c-002 所有组 5/5。两次 go 都在偏容易的任务上，看不到质量差异。
- 真实规则（Sutra AGENTS.md 第 18 行）两次都是 inconclusive，规则保持不变。
- L2 主张累计 pivot 1 次（c-001），go 不清零。
- c-002-sizing 用旧脚本 `tools/m1/run_spike.py`，只用于定预算，不参与判定。
- `--max-budget-usd` 在订阅登录下是否真正截断，从未触发，未验证。
- 实验证据只在本机被忽略的 `experiments/`，换机器无法复现判定。
