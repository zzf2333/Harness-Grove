# Harness Grove

Harness Grove 是 Personal Harness Evolution（PHE）的开发仓库。PHE 运行在用户现有的 Agent（Codex、Claude Code、Pi 等）里，用证据、有限成本的实验和人工批准，持续维护个人 Harness：指令、Skills、Hooks、MCP 等配置。

## 当前状态

- 已有设计文档、实验模板和标准库评测入口 `tools/phe.py`（M0 切片 A）。
- M-1 已完成：spike-001 判定 **go**（2026-10-28，25 次运行，7 项门槛全部通过）；真实规则信号为 inconclusive。M0 已完成：生产等价基线复验 c-002 判定 go（15 次，规则信号 inconclusive）。运行产物位于被忽略的 `experiments/`。

## 文档地图

| 文件 | 内容 | 什么时候读 |
|---|---|---|
| [docs/PHE_SPEC.md](docs/PHE_SPEC.md) | 产品边界、用户流程、架构、术语与状态、生命周期、资产库、安全、里程碑 | 想了解项目做什么、长期怎么设计 |
| [skill/harness-evolve/SKILL.md](skill/harness-evolve/SKILL.md) | 可移植 Agent Skill：起草 → 冻结 → 用户批准 → `run` → `judge` 的操作顺序与硬规则 | 准备在宿主 Agent 里跑一次规则消融评测时 |
| [docs/M1_D_DESIGN.md](docs/M1_D_DESIGN.md) | 切片 D1 的沙箱设计：守卫、目录、命令、验收 | 实现或审查切片 D |
| [docs/M1_PLAN.md](docs/M1_PLAN.md) | M1 的完成条件、切片、盘点约束、待决定的资产源 | 推进或审查 M1 |
| [docs/M0_PLAN.md](docs/M0_PLAN.md) | M0 的完成条件、切片、门槛 v2、选任务要求 | 推进或审查 M0 |
| [docs/M-1_PROTOCOL.md](docs/M-1_PROTOCOL.md) | M-1 探针的完整执行协议：路径、对照规则、spike-000 用量测量、25 次运行、预算、门槛、判定 | 准备执行或审查 M-1 |
| [examples/m-1/proposal.template.json](examples/m-1/proposal.template.json) | M-1 预注册模板（draft），未知项为 null | 填写并冻结 spike-001 时 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 各版本的设计决定 | 想知道某个决定为什么这样定 |

**阅读顺序：** 先读 PHE_SPEC 的「产品边界」「用户怎么使用」「里程碑与 Gate」，再读 M-1 协议。

## 目录约定

- `tools/m1/run_spike.py`：M-1 最小 Runner，只用标准库；一次调用跑一个 slot，产出写到 `experiments/<id>/`。
- `tools/phe.py`：M0 评测入口；`run <实验目录>` 按已批准 proposal 调度，`judge <实验目录> [--out <结果路径>]` 判定。组角色由 `harness.variants[*].remove` 推导（两个空列表为基线，`real_rule` 为真实规则，`required_control` / `redundant_control` 可缺省）；次数、顺序、数值门槛和预算读取 proposal，执行参数读取 config。`gates.version` 缺省为 v1，新模板使用 v2；缺少控制组的对应门槛输出 `not_applicable`。`config.target_argv` 可替代默认 Claude Code 命令，供不调用模型的本地验证。旧 `tools/m1/` 入口保留供冻结实验引用。
- `tools/inventory.py`：M1 切片 A 的只读盘点；`--home`、`--project`（可多个）指定扫描根，`--out` 写 ObservedAsset JSON（缺省打印到 stdout）。每条记录带软链目标、断链标记、Hash、`status: unmanaged`、`load_chain: unverified`；顶层 `groups` 按真实路径聚合共享条目。`settings*.json`、`.mcp.json`、`config.toml` 只记 Hash 和顶层键名，不记值。
- `tools/registry.py`：M1 切片 B/C 的登记与漂移检测；`adopt --inventory <盘点> --registry <登记表> (--path <p> | --group <resolved_path>)` 把选中的盘点条目登记为 `Active(origin=imported, evidence_status=unverified)`。资产按真实路径认定，`observed` 记录所有指向它的 agent/path；只写登记表，不改被登记文件。默认登记表 `registry/assets.json`（被 Git 忽略）。同一真实路径不重复登记；`access_error`、断链或无法解析真实路径的条目拒绝登记，退出码 1。`drift --inventory <新盘点> --registry <登记表> [--out <文件>]` 只读对比基线，输出 `content_changed`、`symlink_target_changed`、`missing`、`unreadable` 四类 `ExternalChange` 和 `changes` 计数（缺省打印到 stdout）；不更新登记表，未登记的新文件不算漂移，有变化也只报告不自动接受。
- `experiments/`：M-1 和 M0 的运行产物，已被 Git 忽略。提交脱敏样本需要另行批准。
- 被测任务所在的仓库只读，实验只在临时目录里运行。
