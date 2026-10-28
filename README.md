# Harness Grove

Harness Grove 是 Personal Harness Evolution（PHE）的开发仓库。PHE 运行在用户现有的 Agent（Codex、Claude Code、Pi 等）里，用证据、有限成本的实验和人工批准，持续维护个人 Harness：指令、Skills、Hooks、MCP 等配置。

## 当前状态

- 只有设计文档和实验模板，**还没有代码**。
- M-1 已完成：spike-001 判定 **go**（2026-10-28，25 次运行，7 项门槛全部通过）；真实规则信号为 inconclusive。下一阶段是 M0。运行产物位于被忽略的 `experiments/`。

## 文档地图

| 文件 | 内容 | 什么时候读 |
|---|---|---|
| [docs/PHE_SPEC.md](docs/PHE_SPEC.md) | 产品边界、用户流程、架构、术语与状态、生命周期、资产库、安全、里程碑 | 想了解项目做什么、长期怎么设计 |
| [docs/M-1_PROTOCOL.md](docs/M-1_PROTOCOL.md) | M-1 探针的完整执行协议：路径、对照规则、spike-000 用量测量、25 次运行、预算、门槛、判定 | 准备执行或审查 M-1 |
| [examples/m-1/proposal.template.json](examples/m-1/proposal.template.json) | M-1 预注册模板（draft），未知项为 null | 填写并冻结 spike-001 时 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 各版本的设计决定 | 想知道某个决定为什么这样定 |

**阅读顺序：** 先读 PHE_SPEC 的「产品边界」「用户怎么使用」「里程碑与 Gate」，再读 M-1 协议。

## 目录约定

- `tools/m1/run_spike.py`：M-1 最小 Runner，只用标准库；一次调用跑一个 slot，产出写到 `experiments/<id>/`。
- `experiments/`：M-1 和 M0 的运行产物，已被 Git 忽略。提交脱敏样本需要另行批准。
- 被测任务所在的仓库只读，实验只在临时目录里运行。
