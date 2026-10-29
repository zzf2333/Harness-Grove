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
| [docs/M2_PLAN.md](docs/M2_PLAN.md) | M2 历史采集、证据分组、跨 Host 续作的切片与隐私约束 | 做 M2 |
| [docs/M2_HISTORY_FORMATS.md](docs/M2_HISTORY_FORMATS.md) | Claude Code、Codex、Pi 的会话目录与 JSONL 字段表（本机观察，未按官方文档核实） | 改 `tools/history.py` 或核对会话格式时 |
| [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md) | 真实实验的执行控制：主动暂停与续跑、墙钟累计口径、Target 写隔离 | 准备跑或审查 `phe.py run` |
| [docs/M1_D_DESIGN.md](docs/M1_D_DESIGN.md) | 切片 D1 的沙箱设计：守卫、目录、命令、验收 | 实现或审查切片 D |
| [docs/M1_PLAN.md](docs/M1_PLAN.md) | M1 的完成条件、切片、盘点约束、待决定的资产源 | 推进或审查 M1 |
| [docs/M0_PLAN.md](docs/M0_PLAN.md) | M0 的完成条件、切片、门槛 v2、选任务要求 | 推进或审查 M0 |
| [docs/M-1_PROTOCOL.md](docs/M-1_PROTOCOL.md) | M-1 探针的完整执行协议：路径、对照规则、spike-000 用量测量、25 次运行、预算、门槛、判定 | 准备执行或审查 M-1 |
| [examples/m-1/proposal.template.json](examples/m-1/proposal.template.json) | M-1 预注册模板（draft），未知项为 null | 填写并冻结 spike-001 时 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 各版本的设计决定 | 想知道某个决定为什么这样定 |

**阅读顺序：** 先读 PHE_SPEC 的「产品边界」「用户怎么使用」「里程碑与 Gate」，再读 M-1 协议。

## 目录约定

- `tools/m1/run_spike.py`：M-1 最小 Runner，只用标准库；一次调用跑一个 slot，产出写到 `experiments/<id>/`。
- `tools/phe.py`：M0 评测入口；`run <实验目录>` 按已批准 proposal 调度，`judge <实验目录> [--out <结果路径>]` 判定。组角色由 `harness.variants[*].remove` 推导（两个空列表为基线，`real_rule` 为真实规则，`required_control` / `redundant_control` 可缺省）；次数、顺序、数值门槛和预算读取 proposal，执行参数读取 config。`gates.version` 缺省为 v1，新模板使用 v2；缺少控制组的对应门槛输出 `not_applicable`。`config.target_argv` 可替代默认 Claude Code 命令，供不调用模型的本地验证。`run --pause-after-new-slots N` 在完成 N 个新 slot 后、启动下一个 Target 前主动暂停：退出码 0、不写 `stop.json`，续跑按原 schedule 跳过已完成；墙钟预算按 `runs.jsonl` 里已记录的执行时长为累计，人工等待不计入。`config.sandbox` 声明后用固定路径的 macOS `/usr/bin/sandbox-exec` 在执行层把 Target 及其子进程、外置验收器和后续 Git 采集限制为只能写 runner 为本 slot 创建的三个临时目录（工作目录、专用 tmp、输出子目录）；沙箱程序和写目录都不可配置，每个 slot 启动前用实际 policy 和完整允许清单实测，新实验还冻结 runner 模块 Hash，不可用或配置越权就拒绝启动；验收前覆盖可信测试时按真实路径核对目的文件，逃出验收副本的链接、绝对路径和 `..` 直接拒绝并记下原因（详见 [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md)）。`log <实验目录> --host {claude,codex,pi} --step {draft,freeze,approve,run,judge,report} [--note <说明>]` 追加一条续作记录到 `experiments/<id>/host_log.jsonl`（在 proposal 冻结哈希之外，不改 proposal.json）；`status <实验目录>` 只读推导阶段（`empty` → `drafted` → `frozen` → `approved` → `running` → `judged` → `reported`），输出 `stage`、各文件是否存在的 `evidence`、`last_log`、`inconsistent`（日志声称的步骤比文件证据靠后）和 `notes`（坏行行号、证据链缺口），不写任何文件。旧 `tools/m1/` 入口保留供冻结实验引用。
- `tools/inventory.py`：M1 切片 A 的只读盘点；`--home`、`--project`（可多个）指定扫描根，`--out` 写 ObservedAsset JSON（缺省打印到 stdout）。每条记录带软链目标、断链标记、Hash、`status: unmanaged`、`load_chain: unverified`；顶层 `groups` 按真实路径聚合共享条目。`agent` 取 `claude`、`codex`、`pi` 或 `shared`：`shared` 表示跨工具共享目录（目前是 `~/.agents/skills`，Codex 和 Pi 都读、Claude Code 不读），这类记录另带 `readers`，单一 Agent 的目录不记（`readers` 来自本机观察，未按官方文档核实）。`settings*.json`、`.mcp.json`、`config.toml` 只记 Hash 和顶层键名，不记值。
- `tools/registry.py`：M1 切片 B/C 的登记与漂移检测；`adopt --inventory <盘点> --registry <登记表> (--path <p> | --group <resolved_path>)` 把选中的盘点条目登记为 `Active(origin=imported, evidence_status=unverified)`。资产按真实路径认定，`observed` 记录所有指向它的 agent/path；只写登记表，不改被登记文件。默认登记表 `registry/assets.json`（被 Git 忽略）。同一真实路径不重复登记；`access_error`、断链或无法解析真实路径的条目拒绝登记，退出码 1。`drift --inventory <新盘点> --registry <登记表> [--out <文件>]` 只读对比基线，输出 `content_changed`、`symlink_target_changed`、`missing`、`unreadable` 四类 `ExternalChange` 和 `changes` 计数（缺省打印到 stdout）；不更新登记表，未登记的新文件不算漂移，有变化也只报告不自动接受。
- `tools/release.py`：M1 切片 D1 的沙箱导入、发布、部署与回滚（写入层与 deploy/rollback 在 `tools/release_deploy.py`，绑定发布校验在 `tools/release_verify.py`）。`import --registry <登记表> --inventory <盘点>` 把选中资产复制进 `--phe-home/assets/<asset_id>/`（skill 复制整个目录）；`release` 构建不可写发布 `releases/rNNNN/`，`manifest.json` 记每个文件的 sha256 和 `asset_id`，编号走高水位不复用；`deploy --agent <x> --release <编号> --registry <登记表>` 先盘点 `--home` 做漂移检查，任何 `ExternalChange` 都中止且不动链接，全部检查通过后写 journal、逐条原子替换软链、最后写 `bindings.json`，中途失败按 journal 回滚；`rollback --journal <文件>` 按 journal 倒序恢复旧状态。`--home`、`--phe-home` 必填，按文件身份（含大小写别名）判断，等于真实 home、是它的祖先，或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之内时在任何写入前退出 2；所有写入先过 `guarded_write_path()`。deploy 不改登记表，基线不变；PHE 自己部署的链接由 deploy 预检按 `bindings.json` 识别——已绑定、精确指向该绑定 release 对应文件且该发布按 manifest 校验通过的链接不算漂移，单独运行 `registry drift` 仍会把它们报成 `symlink_target_changed`。
- `experiments/`：M-1 和 M0 的运行产物，已被 Git 忽略。提交脱敏样本需要另行批准。
- `tools/history.py`：M2 切片 A/B 的只读采集与结构分组；`collect --host {claude,codex,pi} --root <Host 配置根> --out <Episode JSON> [--since 7d|YYYY-MM-DD] [--exclude GLOB …]`。流式逐行读会话 JSONL，产出 `{"episodes":[…],"sources":[…]}`：每条 Episode 带 `host`、`source_path`、`session_id`、`line`、源文件 `sha256`、`project`、`ts`、`kind`、脱敏后的 `excerpt`（≤300 字）、`available`，`kind` 只有 `user_correction`、`tool_failure`、`retry` 三种启发式（召回率与准确率未评估）。源文件读不到或有行解析失败时，`sources` 里记 `available: false` 和 `missing_reason`，不静默跳过。Pi 默认排除 `sessions/subagent/*`，`--exclude` 在默认之外追加。`--root`、`--out` 必填；`--out` 落在真实 home 的 `.claude`、`.codex`、`.pi`、`.phe`、`.agents`、`AI/temper` 之内时在任何写入前退出 2。产物写在被 Git 忽略的 `episodes/`。切片 B 的 `group --episodes <Episode JSON…> --out <分组 JSON>` 把一份或多份 collect 产物按 host + session_id + kind 分组成 `correlation_group`（三者 sha256 前 12 位，同输入同输出）；每组记 project、count、首末 ts 和指针列表（`source_path`、`line`、`ts`、已脱敏 `excerpt`），不复制会话原文；不同会话不合并，只在 `same_project` 里互相列出同 project 同 kind 的组 id（project 为空不关联）；输入里 `available: false` 的来源汇总进 `sources_unavailable`。输入字段不合格时退出 1、不写输出。`--out` 落在真实 home 的 Agent 配置目录、与任一输入文件是同一文件、与任一会话源文件相同或落进会话源目录时退出 2。
- 被测任务所在的仓库只读，实验只在临时目录里运行。
