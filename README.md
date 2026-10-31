# Harness Grove

Harness Grove 是 Personal Harness Evolution（PHE）的开发仓库。PHE 是面向所有用户的开源产品：用户安装后，结合自己已有的完整 Agent Harness（指令、Skills、MCP、Hooks、扩展、工具配置）、行为习惯和会话反馈，持续个性化优化，帮助驾驭 Agent。它用可追溯的证据、默认轻量的评估（值得时才做有限成本的实验）和人工批准来改动配置；资产管理是基础，评测是手段。

产品当前还在 R1：仓库里有可复用的零件和沙箱闭环，`tools/workspace.py` 提供克隆后即可用的初始化与只读发现入口（**不是安装包**），**没有任何真实配置被写入**。排程见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 克隆后开始使用（R1：初始化 + 只读发现）

这叫“克隆后开始使用”，不是安装包：没有安装命令，没有默认 PHE_HOME，也不写任何真实 Host 配置。
下面整段流程只写一个新开的临时工作区 `$WORK`：合成 Host 树、仓库外的 PHE_HOME、无关的 cwd。
命令可以直接复制，把 `SRC` 换成你 clone 下来的仓库路径（`-I` 让 Python 不读当前目录与用户 site-packages）。

```bash
WORK=$(cd "$(mktemp -d)" && pwd -P); SRC=/path/to/Harness-Grove
mkdir -p "$WORK/host/.claude/skills/demo" "$WORK/host/.claude/rules" "$WORK/host/.pi/agent" \
    "$WORK/host/.codex" "$WORK/project/.claude/skills/proj-skill" "$WORK/elsewhere"
printf '共享指令：回答先给结论。\n' > "$WORK/shared-AGENTS.md"
printf '# Claude 指令\n' > "$WORK/host/.claude/CLAUDE.md"
printf -- '---\nname: demo\n---\n演示 skill\n' > "$WORK/host/.claude/skills/demo/SKILL.md"
printf '{"servers": {"demo": {"command": "demo"}}}\n' > "$WORK/host/.pi/agent/mcp.json"
printf '风格规则\n' > "$WORK/host/.claude/rules/style.md"
ln -s "$WORK/shared-AGENTS.md" "$WORK/host/.pi/agent/AGENTS.md"
ln -s "$WORK/shared-AGENTS.md" "$WORK/host/.codex/AGENTS.md"
printf '项目指令\n' > "$WORK/project/AGENTS.md"
printf -- '---\nname: proj-skill\n---\n' > "$WORK/project/.claude/skills/proj-skill/SKILL.md"
cd "$WORK/elsewhere"

python3 -I "$SRC/tools/workspace.py" init --phe-home "$WORK/phe"
python3 -I "$SRC/tools/workspace.py" discover --phe-home "$WORK/phe" \
    --home "$WORK/host" --project "$WORK/project"
```

`init` 建出 `<PHE_HOME>/assets/`、`<PHE_HOME>/releases/` 和格式版本标记 `<PHE_HOME>/workspace.json`；
重复运行保留已有数据，不迁移、不覆盖。登记表、账本与绑定按实际操作创建，`init` 不预建。
`--phe-home` 也可以用 `PHE_HOME` 环境变量给，命令行优先；两者都没给就报错退出 2，不会默认落到 `~/.phe`。

`discover` 只读盘点，把结果写进 PHE_HOME：

| 文件 | 内容 |
|---|---|
| `<PHE_HOME>/reports/discovery.md` | 人可读清单：来源路径、软链目标、Host 入口、逻辑类型、登记状态、能力限制、`load_chain unverified` |
| `<PHE_HOME>/reports/discovery.json` | 同一份结果的结构化版本：ObservedAsset 字段 + 管理状态 + 登记状态 + 覆盖摘要与扫描范围 |

看报告：`less "$WORK/phe/reports/discovery.md"`。它不改输入文件、软链、登记表或配置，同一 fixture 连跑两次字节一致。

可选：把共享指令登记成基线（`registry.py adopt` 只写登记表，不改被登记文件），再跑一次 `discover`，
报告里那条资产会显示“已登记 asset-0001（Active）”，其余仍是“未登记”：

```bash
python3 -I "$SRC/tools/inventory.py" --home "$WORK/host" --project "$WORK/project" \
    --out "$WORK/phe/registry/inventory.json"
python3 -I "$SRC/tools/registry.py" adopt --inventory "$WORK/phe/registry/inventory.json" \
    --registry "$WORK/phe/registry/assets.json" --group "$WORK/shared-AGENTS.md"
python3 -I "$SRC/tools/workspace.py" discover --phe-home "$WORK/phe" \
    --home "$WORK/host" --project "$WORK/project"
```

这一次运行告诉你什么：机器上有哪些 Harness 资产、从哪个文件或软链来、哪个 Host 入口在读、PHE 到哪一步能管
（`沙箱可托管` / `部分可托管` / `仅观察` / `未支持`）。它**不**证明这些配置被 Host 加载过：每条都标 `load_chain unverified`。
MCP 只有文件级观察，Hooks 没有候选路径所以列“未支持”。下一步是 R2（用会话证据生成可逐条批准的改动），
那一轮的写入入口还没有提供。本流程的验收记录见 [docs/acceptance/r1.md](docs/acceptance/r1.md)。
看完后清理：`rm -rf "$WORK"`（全部内容都只在这个临时目录里）。

## 当前状态

已交付的是零件与沙箱闭环，完成状态只在各自声明的范围内成立：

- 产品路线：[docs/ROADMAP.md](docs/ROADMAP.md) R1–R4。R1（独立开始使用）的初始化与只读发现入口已交付：`tools/workspace.py init/discover` 在沙箱验收（见 [docs/acceptance/r1.md](docs/acceptance/r1.md)），报告写在显式 PHE_HOME 的 `reports/` 下；仍未提供安装包、默认 PHE_HOME、真实 Host 写入与加载验证。R2 的闭环代码已在沙箱跑通，安装用户在授权范围内更新自己配置的入口还没提供——现有 `release_deploy` 守卫明确拒绝真实 home 及 Host 入口路径，开发与验收继续用隔离模拟用户环境。
- M1 的 A–D1 已在沙箱完成（盘点、登记、漂移检测、发布/部署/回滚）；D2（MCP、hooks、Codex config.toml）未开始，接管真实环境需用户另批，见 [docs/M1_PLAN.md](docs/M1_PLAN.md)。
- M2 已完成（按原定 Gate 结项，2026-10-30）：历史采集、结构分组、`log`/`status` 续作记录、Codex Target 最小接口已完成。两个真实 Host 续作同一提案已通过：S1 阻塞交接（Pi 起草、真实 Codex 会话从文件续读并停在阻塞处），S2 正常冻结交接（Pi 起草、Codex 独立复核后冻结，主控补核快照绑定；未批准或运行 Target），见 [docs/M2_PLAN.md](docs/M2_PLAN.md)。Target 按需逐个适配：Claude Code 有 M-1/M0 真实运行证据；真实 Claude Code Target 实验 m2-real-002 启动 1/15 次后因服务端拒绝订阅登录（403）停在 `pivot:telemetry_missing`，加载链阻塞保留。Codex Target 真实运行、费用与隔离验证是后续适配项，不追加为 Gate。
- M3a（历史阶段，未完成）：切片 A 完成，新增 `tools/proposal.py draft/check` 从 M2 分组产物生成经验候选提案并只读检查缺项；切片 B1 完成，`draft --retired <JSON>` / `check --retired <JSON>` 处理 Host 显式给出的有限 Retired 检索文件并核对 `unrelated` / `alternative` / `reopen` 关系。自动 Retired 检索（B2）、自动独立证据判断（C）暂停，D 缩小为改前写三行说明（为什么改 / 期待 / 凭什么判断），见 [docs/M3a_PLAN.md](docs/M3a_PLAN.md)。
- 闭环切片 1（沙箱实现，R2 的零件）：`tools/history.py digest` 把真实会话整理为带源文件/行号指针的脱敏 Markdown 分块；宿主按 [Skill §11](skill/harness-evolve/SKILL.md#11-经验复盘闭环切片-1) 只读诊断、核对原文并生成可审批报告。已在真实会话上跑过摘要，报告与真实验收未在真实配置上执行。
- 闭环切片 2（沙箱实现，R2 的零件）：`tools/change.py apply/status/restore` 把报告条目按批准应用到临时 home 的三个 Agent 入口，账本 `<phe-home>/ledger.jsonl` 只追加批准/应用/恢复事实，恢复按执行逆序；真实配置未写入（`change.py` 当前只在沙箱运行，守卫拒绝真实入口）。见 [docs/M3a_PLAN.md](docs/M3a_PLAN.md#闭环切片-2批准应用与恢复仅临时配置) 与 [Skill §12](skill/harness-evolve/SKILL.md#12-经验复盘应用闭环切片-2)。
- 闭环切片 3（沙箱实现，R2 的零件）：`tools/change.py review-plan/review` 读账本给出每个执行的观察窗与原预测（三行说明），把宿主的保留/调整/撤销/继续观察连同证据指针与记录 sha256 追加为 `review` 事实；`status` 附最新 review。工具只记录、不判断效果，`revert` 仍需用户批准后才 `restore`。演练在切片 2 账本副本上跑通，见 [docs/M3a_PLAN.md](docs/M3a_PLAN.md#闭环切片-3复盘账本对照原预测) 与 [Skill §13](skill/harness-evolve/SKILL.md#13-复盘记录闭环切片-3)。
- M-1 已完成：spike-001 判定 **go**（2026-10-28，25 次运行，7 项门槛全部通过）；真实规则信号为 inconclusive。M0 已完成：生产等价基线复验 c-002 判定 go（15 次，规则信号 inconclusive）。运行产物位于被忽略的 `experiments/`。

## 文档地图

| 文件 | 内容 | 什么时候读 |
|---|---|---|
| [docs/ROADMAP.md](docs/ROADMAP.md) | **当前唯一排程**：产品目标、三个空间、Harness 资产现状矩阵、R1–R4 各自场景/复用/缺口/验收/依赖/完成标准 | 想知道现在做什么、下一步做什么 |
| [docs/acceptance/r1.md](docs/acceptance/r1.md) | R1 验收记录：初始化与只读发现的命令、退出码、断言、剩余缺口与未验证项 | 想复核 R1 交付或重跑首次使用流程时 |
| [docs/PHE_SPEC.md](docs/PHE_SPEC.md) | 产品契约：边界、三个空间、用户流程、架构、术语与状态、生命周期、资产库共同契约、安全、历史阶段与评估口径 | 想了解产品边界与长期怎么设计 |
| [skill/harness-evolve/SKILL.md](skill/harness-evolve/SKILL.md) | 可移植 Agent Skill：起草 → 冻结 → 用户批准 → `run` → `judge` 的操作顺序与硬规则 | 准备在宿主 Agent 里跑一次规则消融评测时 |
| [docs/M2_PLAN.md](docs/M2_PLAN.md) | M2 历史采集、证据分组、跨 Host 续作的切片与隐私约束（历史阶段） | 做 M2 相关维护 |
| [docs/M3a_PLAN.md](docs/M3a_PLAN.md) | M3a 阶段计划：切片 A/B1 契约、闭环切片 1–3 的沙箱实现（历史阶段） | 改 `proposal.py` / `change.py` |
| [docs/M2_HISTORY_FORMATS.md](docs/M2_HISTORY_FORMATS.md) | Claude Code、Codex、Pi 的会话目录与 JSONL 字段表（本机观察，未按官方文档核实） | 改 `tools/history.py` 或核对会话格式时 |
| [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md) | 真实实验的执行控制：主动暂停与续跑、墙钟累计口径、Target 写隔离 | 准备跑或审查 `phe.py run` |
| [docs/M1_D_DESIGN.md](docs/M1_D_DESIGN.md) | 切片 D1 的沙箱设计：守卫、目录、命令、验收 | 实现或审查切片 D |
| [docs/M1_PLAN.md](docs/M1_PLAN.md) | M1 的完成条件、切片、盘点约束（A–D1 沙箱完成，D2 未开始；历史阶段） | 推进或审查 M1 遗留项 |
| [docs/M0_PLAN.md](docs/M0_PLAN.md) | M0 的完成条件、切片、门槛 v2、选任务要求 | 推进或审查 M0 |
| [docs/M-1_PROTOCOL.md](docs/M-1_PROTOCOL.md) | M-1 探针的完整执行协议：路径、对照规则、spike-000 用量测量、25 次运行、预算、门槛、判定 | 准备执行或审查 M-1 |
| [examples/m-1/proposal.template.json](examples/m-1/proposal.template.json) | M-1 预注册模板（draft），未知项为 null | 填写并冻结 spike-001 时 |
| [examples/m3a/](examples/m3a/) | M3a 合成输入示例：分组 JSON、带 `retired_review` 的 Host 候选、显式 Retired 检索文件 | 跑通 `draft --retired` / `check --retired` 时 |
| [examples/lightweight-loop/](examples/lightweight-loop/) | 轻量评估闭环的可复跑示例：合成 pi 会话、最小配置、宿主手写报告与 `render_inputs.py`，全部只写临时目录 | 想从头走一遍摘要 → 报告 → 批准 → 应用 → 复盘 → 恢复时 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 各版本的设计决定 | 想知道某个决定为什么这样定 |

**阅读顺序：** 先读 ROADMAP 的产品目标与 R1–R4，再读 PHE_SPEC 的「产品边界」「三个空间与数据归属」「评估口径」；M-1 协议和各 M 阶段计划只在需要那部分证据时读。

## 仓库与用户资产的区别

本仓库是**源码**：`tools/` 是产品代码，`skill/harness-evolve/` 是可移植 Skill 源码，`examples/` 是手写合成示例，测试只写临时目录。目标是用户的 Harness 资产、发布、绑定、账本、证据摘要与报告都在仓库外的 PHE_HOME（产品规划默认 `~/.phe`；当前只有克隆后的 `tools/workspace.py init --phe-home <目录>`，没有安装包，也没有创建过默认目录）；真实 Host 入口是 `~/.pi`、`~/.codex`、`~/.claude`、`~/.agents` 及项目级目录。规范上，新增提交不引入用户会话、真实配置或本机绝对路径。现状：早期 `experiments/` 下用 `git add -f` 加入的工件含本机绝对路径，`episodes/`、`reports/` 仍是开发期写入位置；两者都未清理，不把清理当作已完成。

## 目录约定

- `tools/workspace.py`：R1 的用户入口，克隆后从任意工作目录用 `python3 -I <源码>/tools/workspace.py` 运行，不是安装包。`init --phe-home <目录>`（或 `PHE_HOME` 环境变量，命令行优先；没有默认目录）建出 `assets/`、`releases/` 与格式版本标记 `workspace.json`；已有工作区保留数据、不迁移，版本不符时拒绝并说明。`discover --phe-home <目录> --home <显式 Host 根> [--project <根> …] [--registry <登记表>]` 复用 `inventory.py` 的候选路径表与分组做只读盘点，读 `<PHE_HOME>/registry/assets.json`（缺省位置）标记登记状态，把 `reports/discovery.md`（人可读）和 `reports/discovery.json`（结构化，不含运行时间所以可复现）写进 PHE_HOME；不 adopt、不导入、不部署、不改输入。覆盖摘要按逻辑类型列“发现 N 条 / 未发现 / 未支持”，Hooks 这类没有候选路径的类型记“未支持”、不猜路径；每条记录带管理状态（`沙箱可托管` / `部分可托管` / `仅观察` / `未支持`）与 `load_chain unverified`。项目级范围只在传了 `--project` 时扫描，没传时报告写“未扫描”，不写成“没有”。
- `tools/m1/run_spike.py`：M-1 最小 Runner，只用标准库；一次调用跑一个 slot，产出写到 `experiments/<id>/`。
- `tools/phe.py`：M0 评测入口；`run <实验目录>` 按已批准 proposal 调度，`judge <实验目录> [--out <结果路径>]` 判定。组角色由 `harness.variants[*].remove` 推导（两个空列表为基线，`real_rule` 为真实规则，`required_control` / `redundant_control` 可缺省）；次数、顺序、数值门槛和预算读取 proposal，执行参数读取 config。`gates.version` 缺省为 v1，新模板使用 v2；缺少控制组的对应门槛输出 `not_applicable`。Target 由 `proposal.target.agent` 选择：`claude-code` 保持旧默认命令；`codex` 使用 `codex exec --json --ephemeral --ignore-user-config -s workspace-write -C <work> -m <proposal.target.model_id> -o <output>/last-message.txt <prompt>`。`config.target_agent` 只用于声明同一选择，与 proposal 冲突时拒绝启动；`config.target_argv` 只替代 Claude 默认命令供不调用模型的本地验证，Codex 拒绝这个覆盖、只用规范 argv（本地契约验证在测试层替换 subprocess），`argv.json` 记录完整实际 argv。Codex JSONL 取最后一次 `turn.completed` 的 thread 累计用量，TOK 只算 input+output；缺 usage、坏行、非零退出和失败事件保留 error 语义，不补零，最后一个 turn 未闭合也按截断处理。费用没有来源，保持 `None` 并继续 `pivot:telemetry_missing`；不提供 max-turns 预算控制。Codex 指纹只覆盖 `~/.codex/AGENTS.md` 和 `config.toml`，不读 auth。真实 Codex 会话隔离与费用仍未验证（详见 [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md)）。`run --pause-after-new-slots N` 在完成 N 个新 slot 后、启动下一个 Target 前主动暂停：退出码 0、不写 `stop.json`，续跑按原 schedule 跳过已完成；墙钟预算按 `runs.jsonl` 里已记录的执行时长为累计，人工等待不计入。`config.sandbox` 声明后用固定路径的 macOS `/usr/bin/sandbox-exec` 在执行层把 Target 及其子进程、外置验收器和后续 Git 采集限制为只能写 runner 为本 slot 创建的三个临时目录（工作目录、专用 tmp、输出子目录）；沙箱程序和写目录都不可配置，每个 slot 启动前用实际 policy 和完整允许清单实测，新实验还冻结 runner 模块 Hash（Codex 实验无论有无 `sandbox` 都要求完整三项，要求取自冻结 proposal 的原始 `target.agent`），不可用或配置越权就拒绝启动；验收前覆盖可信测试时按真实路径核对目的文件，逃出验收副本的链接、绝对路径和 `..` 直接拒绝并记下原因（详见 [docs/M2_RUN_CONTROLS.md](docs/M2_RUN_CONTROLS.md)）。`log <实验目录> --host {claude,codex,pi} --step {draft,freeze,approve,run,judge,report} [--note <说明>]` 追加一条续作记录到 `experiments/<id>/host_log.jsonl`（在 proposal 冻结哈希之外，不改 proposal.json）；`status <实验目录>` 只读推导阶段（`empty` → `drafted` → `frozen` → `approved` → `running` → `judged` → `reported`），输出 `stage`、各文件是否存在的 `evidence`、`last_log`、`inconsistent`（日志声称的步骤比文件证据靠后）和 `notes`（坏行行号、证据链缺口），不写任何文件。旧 `tools/m1/` 入口保留供冻结实验引用。
- `tools/inventory.py`：M1 切片 A 的只读盘点；`--home`、`--project`（可多个）指定扫描根，`--out` 写 ObservedAsset JSON（缺省打印到 stdout）。每条记录带软链目标、断链标记、Hash、`status: unmanaged`、`load_chain: unverified`；顶层 `groups` 按真实路径聚合共享条目。`agent` 取 `claude`、`codex`、`pi` 或 `shared`：`shared` 表示跨工具共享目录（目前是 `~/.agents/skills`，Codex 和 Pi 都读、Claude Code 不读），这类记录另带 `readers`，单一 Agent 的目录不记（`readers` 来自本机观察，未按官方文档核实）。`settings*.json`、`.mcp.json`、`config.toml` 只记 Hash 和顶层键名，不记值。
- `tools/registry.py`：M1 切片 B/C 的登记与漂移检测；`adopt --inventory <盘点> --registry <登记表> (--path <p> | --group <resolved_path>)` 把选中的盘点条目登记为 `Active(origin=imported, evidence_status=unverified)`。资产按真实路径认定，`observed` 记录所有指向它的 agent/path；只写登记表，不改被登记文件。默认登记表 `registry/assets.json`（被 Git 忽略）。同一真实路径不重复登记；`access_error`、断链或无法解析真实路径的条目拒绝登记，退出码 1。`drift --inventory <新盘点> --registry <登记表> [--out <文件>]` 只读对比基线，输出 `content_changed`、`symlink_target_changed`、`missing`、`unreadable` 四类 `ExternalChange` 和 `changes` 计数（缺省打印到 stdout）；不更新登记表，未登记的新文件不算漂移，有变化也只报告不自动接受。
- `tools/release.py`：M1 切片 D1 的沙箱导入、发布、部署与回滚（写入层与 deploy/rollback 在 `tools/release_deploy.py`，绑定发布校验在 `tools/release_verify.py`）。`import --registry <登记表> --inventory <盘点>` 把选中资产复制进 `--phe-home/assets/<asset_id>/`（skill 复制整个目录）；`release` 构建不可写发布 `releases/rNNNN/`，`manifest.json` 记每个文件的 sha256 和 `asset_id`，编号走高水位不复用；`deploy --agent <x> --release <编号> --registry <登记表>` 先盘点 `--home` 做漂移检查，任何 `ExternalChange` 都中止且不动链接，全部检查通过后写 journal、逐条原子替换软链、最后写 `bindings.json`，中途失败按 journal 回滚；`rollback --journal <文件>` 按 journal 倒序恢复旧状态。`--home`、`--phe-home` 必填，按文件身份（含大小写别名）判断，等于真实 home、是它的祖先，或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之内时在任何写入前退出 2；所有写入先过 `guarded_write_path()`。deploy 不改登记表，基线不变；PHE 自己部署的链接由 deploy 预检按 `bindings.json` 识别——已绑定、精确指向该绑定 release 对应文件且该发布按 manifest 校验通过的链接不算漂移，单独运行 `registry drift` 仍会把它们报成 `symlink_target_changed`。
- `tools/change.py`：闭环切片 2/3 的批准、应用、恢复与复盘（写入仍经 `tools/release_deploy.py` 的 `Sandbox`，账本事实与推导在 `tools/change_ledger.py`，复盘入口在 `tools/change_review.py`）。`apply --change <变更包> --item <P1> --approval <批准记录> --registry <登记表> --phe-home <临时 PHE_HOME> --home <临时 home>` 从目标入口当前实际生效的内容起算，用 `git apply` 把该条目 patch 打到 `<phe-home>/staging/<执行 ID>/`，按 `release import` 的同一 `asset.json` 格式重建资产，再走既有 `release` 与逐 Agent `deploy`；三个入口全部完成后才向 `<phe-home>/ledger.jsonl` 追加 `approval` 和 `apply` 事实，失败保留 journal/发布但不追加、不重试。变更包带报告路径与 sha256（apply 重算，不一致退出 1）、条目 ID、证据、`prediction`（`why` / `expected` / `verify` / 可缺的 `baseline`）、目标 asset id 和 patch；工具只要求整个 `prediction` 存在，内部键不校验、原样入账，旧变更包继续可用；批准记录用 `change_sha256` 绑定确定版本的变更包与 `items`，`source` 只有 `user` 和 `sandbox_rehearsal`。`status --phe-home` 从事实推导每个执行是 `applied` 还是 `restored`、当前生效栈，附最新 `latest_review`，并读文件给出入口现在的发布和 sha256，不写任何东西。`restore --execution x0001` 按 journal 逆序恢复，只允许恢复当前最后一条生效的执行，恢复后按 journal 的 `old_target` 重新绑定到更早的 PHE 发布，下一次 deploy 才不会被漂移预检当成 ExternalChange；恢复只追加 `restore` 事实，不表示提案无效或 retired。`review-plan --phe-home` 只读列出每个执行（含已恢复）的观察窗（应用到恢复，未恢复的 `to` 为 `null`）、原预测、证据、报告引用和全部 review 历史；`review --execution x0001 --record <JSON> --phe-home` 只追加一条 `review` 事实（`verdict` ∈ keep/adjust/revert/observe、`prediction_met` ∈ met/not_met/unknown、`reason`、`observations` 证据指针、`next`，加记录路径与原始字节 sha256），不改执行状态、不调用 restore、不碰配置和发布，同一执行可多次 review 且旧行不改写；守卫只查 `--phe-home`。本轮只支持单个 instructions 资产 + 三个 Agent 软链入口。
- `tools/rehearse_change.py`：闭环切片 2 演练，只读真实报告与用户级 `config/AGENTS.md`，把字节复制到 `--out` 下，在临时 home 的三个入口软链上应用 P1、P2 并逆序恢复；P1/P2 的 patch 在 `tools/rehearse_change_input.py`（手工从报告提取，不解析 Markdown）。批准写 `source=sandbox_rehearsal`，命令、断言、发布、journal、账本和恢复所需文件全部留在 `--out`（本轮产物在 `experiments/loop-apply-001/`，被 Git 忽略）。
- `tools/rehearse_review.py`：闭环切片 3 演练，把切片 2 的 `experiments/loop-apply-001/phe/` 复制到 `--out/phe`（原目录不改），先 `review-plan`，再对真实演练执行写 `observe`/`unknown` 的 review；然后在 `--out/synthetic/` 下另建一套【合成】沙箱（小 base + 三个软链入口 + 独立 PHE_HOME），apply 合成条目 `S1` 后不 restore，`to` 是 `null`，取样上界取演练声明的模拟复盘时刻 `cutoff = 应用时刻 + 60 分钟`，窗口是 `[应用时刻, cutoff)`，合成会话时间戳按相对实际 `applied_at` 的偏移生成，按窗口两端取样后手算 `cwd does not exist` 次数，结论按窗口内计数推导，并放一条晚于 `cutoff` 的命中证明不计入；另用零宽窗口样例证明窗口外的命中（含恰好等于端点的一条）不计入观察。合成沙箱与真实演练账本分开。合成数据与取样逻辑在 `tools/rehearse_review_synthetic.py`，报告渲染在 `tools/rehearse_review_report.py`。命令、断言、记录和报告留在 `--out`（本轮产物在 `experiments/loop-review-001/`，被 Git 忽略）；结论只说明复盘流程可运行，不证明规则有效。
- `tools/proposal.py`：M3a 的本地草稿与只读检查（候选结构在 `tools/proposal_candidate.py`，显式 Retired 输入与关系核对在 `tools/proposal_retired.py`，共用输入校验在 `tools/proposal_input.py`），只用标准库。`draft --groups <分组 JSON> --candidate <Host 候选 JSON> [--retired <Retired 检索 JSON>] --experiment-id <id>` 只新建 `experiments/<id>/proposal.json`：形状取自 `examples/m-1/proposal.template.json`，只改实验身份、`status=draft` 和 artifact 路径，追加 `candidate` 段。Host 候选 JSON 只有 `groups`、`hypothesis`、`predictions`、`retired_review`；证据只从分组 JSON 解析 `source_path`/`line`/`ts`，绑定分组文件原始字节 sha256 和选中组 ID，不复制 `excerpt`，不接受 Host 自填引用或检索结果字段。缺假设/预测字段可保存并逐项报告；未知 metric、组不存在或同一 ID 对应多份内容、畸形 JSON/类型、已存在的实验目录（含冻结目录）和路径穿越/软链逃逸都拒绝。

  `--retired` 给出的是 Host 显式提供的有限 JSON（`schema_version: 1`、提供者声明的 `scope`、`entries` 数组）；工具按文件顺序逐条列举全部 `entries`，不推断语义相关、不排序、不截断、不扫描用户目录，也不访问真实退役记录。每个条目只保留 `retired_id`、`method`、`rejected_reason`、`retired_at`、`evidence_groups`；坏 JSON、错版本、缺字段、无时区时间、重复 `retired_id` 都拒绝。`candidate.retired` 只有两种判别清楚的状态：`unavailable`（未提供或来源读不到，带原因，绝不是 no_match）和 `completed`（带来源路径与原始字节 sha256、`scope=all_entries_in_supplied_file`、提供者的 `supplied_scope` 和逐条记录）。`entries` 为空只表示该文件为空。

  `retired_review` 是 Host 判断，不是检索结果：总体 `relation` / `reason` 加逐条 `retired_id` 关系。缺失可保存并列入 `missing`；`relation` 非法、`retired_id` 不存在或重复、引用当前候选未选中的分组、出现 `user_authorized` / `matches` / `status` 等字段都退出 1。`alternative` / `reopen` 必须有 `new_evidence_groups`，且这些分组不能出现在该记录退役时的 `evidence_groups` 里、分组时间必须晚于 `retired_at`，否则列 `retired_evidence_*` 阻塞。只检查这些必要结构和时序，不判断新证据是否语义独立或充分，也不实现手动恢复授权。

  `check --experiment-id <id>` 只读同一份 proposal（metric 白名单另读仓库模板，不读草稿自己的 `metrics`），不写侧文件、也不留 `__pycache__`，不运行 `run`/`judge`；编辑后的 `candidate.evidence` 若 `count` 与指针数不符、组 ID 重复或选中 ID 顺序不一致都拒绝，编辑后的 `candidate.retired` / `retired_review` 按与 `draft` 相同的规则检查。默认 `check` 不回读任何外部输入，报告里的 `retired_source_check` 会明确说明无法检测外部变化；`check --retired <file>` 才重算来源 sha256 并比对记录，变化或来源不可用时报 `re_search_required`、退出 1，且不改写 proposal。`freeze_ready` 在 B1 仍恒为 false（剩余预测冻结与正式核验未实现）；没有 `candidate` 的旧提案报 `legacy_proposal_out_of_scope`，不被判为失效。stdout 是 JSON，`saved`/`checked` 与 `freeze_ready` 分开；报告只覆盖候选、Retired 结构和明显的未填项，不是完整协议验证。
- `experiments/`：M-1 和 M0 的运行产物，已被 Git 忽略。提交脱敏样本需要另行批准。
- `tools/history.py`：M2 切片 A/B 的只读采集与结构分组；`collect --host {claude,codex,pi} --root <Host 配置根> --out <Episode JSON> [--since 7d|YYYY-MM-DD] [--exclude GLOB …]`。流式逐行读会话 JSONL，产出 `{"episodes":[…],"sources":[…]}`：每条 Episode 带 `host`、`source_path`、`session_id`、`line`、源文件 `sha256`、`project`、`ts`、`kind`、脱敏后的 `excerpt`（≤300 字）、`available`，`kind` 只有 `user_correction`、`tool_failure`、`retry` 三种启发式（召回率与准确率未评估）。源文件读不到或有行解析失败时，`sources` 里记 `available: false` 和 `missing_reason`，不静默跳过。Pi 默认排除 `sessions/subagent/*`，`--exclude` 在默认之外追加。`--root`、`--out` 必填；`--out` 落在真实 home 的 `.claude`、`.codex`、`.pi`、`.phe`、`.agents`、`AI/temper` 之内时在任何写入前退出 2。产物写在被 Git 忽略的 `episodes/`。切片 B 的 `group --episodes <Episode JSON…> --out <分组 JSON>` 把一份或多份 collect 产物按 host + session_id + kind 分组成 `correlation_group`（三者 sha256 前 12 位，同输入同输出）；每组记 project、count、首末 ts 和指针列表（`source_path`、`line`、`ts`、已脱敏 `excerpt`），不复制会话原文；不同会话不合并，只在 `same_project` 里互相列出同 project 同 kind 的组 id（project 为空不关联）；输入里 `available: false` 的来源汇总进 `sources_unavailable`。输入字段不合格时退出 1、不写输出。`--out` 落在真实 home 的 Agent 配置目录、与任一输入文件是同一文件、与任一会话源文件相同或落进会话源目录时退出 2。
- `tools/history.py digest --host {claude,codex,pi} --root <Host 配置根> --out-dir <目录> [--since 7d|YYYY-MM-DD] [--exclude GLOB …]`：闭环切片 1 的只读会话摘要入口，约 40KB 分块写 `<host>-N.md` 与 `<host>-index.json`；正文带 `L行号`，普通真人消息仅进入摘要，`collect/group` 的三种 kind 不变。
- `reports/`：含隐私的经验复盘报告，已被 Git 忽略。
- 被测任务所在的仓库只读，实验只在临时目录里运行。

## 许可证

[MIT](LICENSE)。
