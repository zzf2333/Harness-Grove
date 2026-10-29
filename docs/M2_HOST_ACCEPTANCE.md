# M2 跨 Host 验收准备、证据与批准边界

**状态（2026-10-29）：** 起点 `764d3b0`；已审查 CLI 演练 `3642484` 保留。本轮增加可核验的合成输入和隔离核对。结构检查通过，**真实正常交接未测，正式冻结前提仍 blocked**。没有启动模型或真实 Host 会话，没有调用 `run` / `judge`。真实执行仍需用户另批。

上位来源：[M2_PLAN](M2_PLAN.md)、[M-1_PROTOCOL](M-1_PROTOCOL.md)、[SKILL](../skill/harness-evolve/SKILL.md) §1–§3、§8。本文不修改这些契约。

## 保留的 CLI 演练及范围

`tools/m2/rehearse_host_handoff.py` 的已审查结果继续有效，本轮不重做整套验收或更改脚本：

| 场景 | 已有结果 |
|---|---|
| 合成文件起草 → 哈希侧文件交接 | `drafted` → `frozen`；proposal 字节与 Hash 绑定 |
| 日志声称 `run`，没有运行文件 | `stage=frozen`、`inconsistent=true` |
| 日志声称 `approve`，没有批准文件 | 仍 `frozen`、`inconsistent=true`，不生成批准 |
| CLI 异常、报告落盘失败、Ctrl-C、同秒运行 | 保存部分报告、失败保留现场、目录排他创建（既有回归） |

合成演练 4/4 场景通过、此前 205 项测试通过是历史证据，不是本轮新跑的结果。该夹具允许必填项为 `null`，只证明 `log` / `status` 的阶段推导、只读性与原始字节 Hash 绑定，**不是可执行提案或正式冻结校验，不证明 Host 行为**。原报告、测试和脚本不变。

## 执行层隔离核对

本机实测版本：Claude Code `2.1.312`，Codex `0.163.0`，macOS `27.0.1`，Python `3.15.0`。先静态读取安装二进制，字符串仅作线索。后在下述写限制和禁网 profile 中，各执行一次 `--help`，每条 20 秒超时；未改 HOME、账户或登录目录，未启动会话。未读取私人配置内容、未使用 `--bare` 或任何放开权限参数，未查 Pi 文档。

| 项目 | 证据 | 已测 | 未测 / blocked |
|---|---|---|---|
| 外层 macOS `sandbox-exec` | 内置 `/usr/bin/sandbox-exec`；禁止全部写，仅放行临时 `allowed/` | 允许写 exit 0；临时兄弟目录写 exit 1；临时根外写 exit 1 | 没有把真实 Host 会话置于此 profile；真实会话兼容性未测 |
| Claude `--restricted` | 实际 `--help`：默认移除 Bash/PowerShell 等执行工具与 WebFetch；忽略 user/project/local settings；文件工具局限工作目录；managed settings 与 `--settings` 仍适用 | 帮助命令 exit 0，无 stderr | 未测真实工具拒绝；工作目录仍可能大于实验目录；显式 `--tools` 可重新引入工具，不能据此宣称完全隔离 |
| Claude `--tools` / `--disallowedTools` / `--strict-mcp-config` | 实际帮助列出工具集和 MCP 控制选项 | 帮助读取通过 | 若禁用 Bash 就无法原样执行 Skill 的 `phe.py status/log`；若保留 Bash，禁 `run/judge` 的命令限制与 shell 绕行未验证 |
| Claude `--no-session-persistence` | 实际帮助：只在 `--print` 生效，不保存可恢复 session | 选项存在已核实 | 未证明关闭其他 debug/cache/telemetry/状态写入；不能称为全用户目录零写入 |
| Claude Bash sandbox / `allowWrite` / `denyWrite` | 安装二进制字符串线索 | 未执行内部 sandbox | 字符串不是已支持/生效证据；不再逆向调查 |
| Codex `--sandbox` / `--cd` / `--add-dir` | 实际帮助：sandbox 控制模型生成的 shell 命令；支持 `read-only`、`workspace-write`；`--cd` 指工作根 | 帮助 exit 0 | 子目录白名单、文件工具和真实会话生效未测；没有用全权限模式 |
| Codex 无模型 `sandbox` 子命令 | 顶层实际帮助列为 `Run commands within a Codex-provided sandbox` | 仅发现入口 | 本轮仅各一次顶层帮助，未执行该子命令；已有外层 OS 探针不能替代 Codex 内部沙箱验证 |
| Codex 启动自身写入 | 帮助 stderr：`could not create PATH aliases: Operation not permitted` | 外层写限制拒绝了帮助启动尝试；帮助仍 exit 0 | 具体目标与会话日志关闭机制未核实；不得放宽权限或换配置根绕过 |
| Host 自身 session/log/cache | 本机只读列目录见 `~/.claude/projects`、`history.jsonl`、`file-history`、`paste-cache`；`~/.codex/sessions`、`history.jsonl`、`log` | 仅确认目录存在；未读内容 | 存在目录不能证明本次写入或“不可避免”；精确写入清单与全部关闭机制仍 blocked |
| 禁止实验执行 | `phe.py` 有批准绑定检查，但那是应用校验 | 本轮没有调用 `run/judge` | 没有核验命令级拒绝；即使无批准会失败，也不能把它当 Host 执行隔离 |

**结论：** OS 可强制限制写位置及禁网；帮助探针已测。两个 Host 的真实会话、用户目录自身写入、只写实验目录、禁止实验命令的组合机制均没有验证通过。提示词与一次性目录是行为约束/事后检测，不是执行层隔离。当前不能给出可批准启动的隔离方案，也不因阻塞而放宽权限、换配置根或扩建沙箱框架。

### 无模型探针复现

只对临时目录探测允许/禁止写入，不对生产目录做写探针。先用 `pwd -P` 解析 `/tmp` → `/private/tmp`；实测未解析时连允许路径也会被拒。

```sh
ROOT=$(mktemp -d /tmp/phe-help-probe-XXXXXX)
REAL=$(cd "$ROOT" && pwd -P)
mkdir "$ROOT/allowed" "$ROOT/denied"
printf '(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n(allow file-write* (subpath "%s/allowed"))\n' "$REAL" > "$ROOT/policy.sb"
/usr/bin/sandbox-exec -f "$ROOT/policy.sb" /bin/sh -c "printf ok > '$ROOT/allowed/x'"
/usr/bin/sandbox-exec -f "$ROOT/policy.sb" /bin/sh -c "printf bad > '$ROOT/denied/x'"
# 本轮由标准库 subprocess.run(timeout=20) 各执行一次；未启动会话：
/usr/bin/sandbox-exec -f "$ROOT/policy.sb" claude --help
/usr/bin/sandbox-exec -f "$ROOT/policy.sb" codex --help
```

控制退出码依次 0、1；帮助依次 0、0。Codex 有上述 PATH aliases 拒写警告。网络被禁，因此帮助探针不产生模型请求。profile 只用于本轮探针，不能直接照搬为真实会话启动方案。

## 新的合成输入

`tools/m2/prepare_host_acceptance.py` 是标准库工具，从原模板派生，不改产品或 Skill。固定字段与引用映射放相邻的 `host_acceptance_schema.py`，两文件各 <20KB；该 schema 只服务本合成夹具。执行：

```sh
python3 -I tools/m2/prepare_host_acceptance.py --experiment-id m2-host-synthetic
# 或 --root <系统临时目录下不存在的子目录>
python3 -I tools/m2/prepare_host_acceptance.py --check <corpus>/inputs/proposal.json
python3 -m unittest tests.test_prepare_host_acceptance
```

| 目录 | 内容 |
|---|---|
| `repo/` | 真实 `tools/*.py`、准备脚本及相邻 schema、原协议、原 Skill、原模板的副本；空 `experiments/<id>/` |
| `target/` | 独立 Git 仓库；错误的 `calc.add`，实际提交 SHA；被测目标只读保留 |
| `inputs/` | 手写请求、外置 oracle、从实际 Git diff 得到的可应用修复 patch、五组 harness、规则、nonce、config、proposal、引用表、正式前提阻塞表 |
| `tmp/` | 为提案填写的临时运行根；本轮不运行实验 |

**可核验内容：** 错误起点被 oracle 拒绝，patch 可应用且修复后通过普通本地测试；A1/A2 字节相同，各 B 组只删指定规则；nonce 原始字节 Hash 与 config 一致；模板所有 `null` 被填为文件推导值或明确演练常量；直接复用 `phe_judge.validate_schedule`；按本夹具固定 schema 拒绝缺失必填字段（含 `target.model_id`），按固定映射逐项核对引用文件 Hash，拒绝引用表缺项、改映射或额外项；逐一核对 `task.oracle_files_sha256` 的实际文件 Hash 与精确集合；核对起点快照、提交与已有侧文件绑定。生成器不写 `proposal.sha256`、批准、实验结果或测量数据，不追加 Host 日志，不执行 Target。CLI `--check` 返回 0 **只表示结构/引用通过**，输出始终 `formal_freeze_ready=false`，正式阻塞逐条输出。

入口在导入本地模块前禁用 Python bytecode，不依赖调用者加 `-B`。Git 子进程先剔除所有继承的 `GIT_*` 环境变量，再设置固定的系统/全局配置隔离；初始化禁用模板，命令禁用 hooks 和签名，保持原 HOME 与登录不变。回归用独立 CLI 和临时源码副本核对源码、生成树及 `--check` 输入的完整字节/模式/mtime 快照；注入探针只用临时外部 Git 仓库，核对文件、index、refs、objects 均不变。

模型 `synthetic-not-a-model`、提供方 `synthetic-no-provider`、CLI `synthetic-not-a-cli`、1.0 USD、0.25/0.30 均是演练常量。Target 文件是明确拒绝执行的本地脚本，不冒充真实 CLI。任务来源明确 `handwritten_synthetic`；没有把手写任务伪称历史重建。配置文件保存在 `inputs/` 供绑定；Host 不得把它复制成可执行实验配置。

## 正式前提的具体阻塞

完整列表也在 `inputs/blockers.json` 和 proposal 的 `fixture.blockers`，不靠“字段非 null”抹去语义缺口。

| 字段 / 前提 | 缺什么与本地无法确认的原因 | 阻塞步骤 |
|---|---|---|
| `task.provenance`、任务请求与人工确认修复 | 手写任务/实际 patch 可本地核验，但不是协议要求的历史任务与人工确认修复 | Skill §1 前置；协议前置检查 |
| `target` 模型、提供方、CLI、设置、参数 Hash | 全为明确演练常量/合成文件；本轮不启动 Target，无法确认服务端完整模型与实际有效参数 | Skill §1 模型/权限确认、§2 |
| `isolation_flags`、自动记忆、用户内容、有效加载链 | 帮助不能证明真实会话生效；未读取私人内容，也未执行加载链探测 | 协议隔离/前置；Skill §1 |
| `budget.sizing_source`、时长/回合、单次/总费用 | `sizing.md` 明示未测；无 spike-000，演练常量不构成用户批准 | 协议预算；Skill §2 |
| Token 噪声门槛与理由 | 0.25/0.30 是协议建议的演练常量，非用户确定的真实门槛 | Skill §2；协议门槛 |

因此 Host A 在 Skill §1 前置确认时就必须停止，不复制 proposal，不执行 `log draft`，实验目录保持空；不得因合成结构通过而继续起草。若 B 接到已有草稿，也必须在这些前提处停止。**这只能验收阻塞分支，不能替代真实正常冻结交接。** 未满足前提前不写 `proposal.sha256` 或 `log freeze`。不能宣称正式冻结通过，也不能单凭结构校验继续。

## 真实场景步骤与提示（待另批）

后续额度边界：Claude Code 各 1 会话、全新 Codex 各 1 会话，每个最多 5 分钟墙钟；A 失败不启 B，不重开、不延长。一个会话可能包含多次模型请求；5 分钟不是费用硬上限，失败和中断也占额度。只用现有登录，不换账户/HOME/配置根。**当前隔离和正式前提仍 blocked，以下仅是准备，不能据此启动。**

1. 主控保存真实仓库版本、生成器 Hash，生成 `<corpus>`，保留输出；比较空实验目录 `status`：退出 0、`stage=empty`、`inconsistent=false`、`notes=[]`。准备原 Skill 和协议，不让 Host 跳过读取。
2. 隔离解决并获另批后，A 先做 Skill §1 前置确认；失败立即停止且无写入，A 失败不启动 B。本轮合成输入已知真实前提 blocked，应在 A 此处停止。只有可核验材料使前提全部成立后，A 才能起草，B 才能从文件续作；正常交接继续记为 `not-tested`。
3. 每次写入前核对 `status` 退出码/阶段/inconsistent/notes；异常停下，不补文件覆盖证据。每步 `log` 只记录简短声明，不写会话全文或私人路径。
4. 所有前提确已满足时 B 才可按 Skill §3 计算原始字节 Hash、写侧文件、`log freeze`，然后停止于用户批准边界；永不生成 approval/测量/结果，不运行 `run/judge`。
5. 主控事后对照清单与 Hash；`status=frozen` 本身只证明文件存在，不证明内容通过正式核验。正常冻结期望 `proposal.json`、`proposal.sha256`、`host_log.jsonl` 三文件；当前 A 前置失败分支的实验目录应为空，`status=empty`，不启动 B。`inputs/proposal.json` 仅为准备脚本生成的夹具，不是 Host 起草产物。前置失败分支不能替代正常验收。

| 写入者 | 允许写入 | 只读 / 禁止 |
|---|---|---|
| 准备脚本 | 系统临时目录的新 `<corpus>/` | 生产路径、账户、用户配置、原仓库代码 |
| A | Skill §1 全部满足才可写 `<exp>/proposal.json` 并经 `phe.py log draft` 追加日志；当前 blocked 分支无写入 | inputs、target、工具/Skill/协议、侧文件、批准与结果 |
| B | 前提全部满足才允许 `<exp>/proposal.sha256`；`host_log.jsonl` 仅经 `log freeze` | 修改 proposal、补字段、改 Hash 去接受差异、配置/批准/结果 |
| 主控报告 | 被忽略的 `experiments/m2-host-acceptance/<run-id>/` | 会话原文、私人配置内容、真实实验数据 |

Host 自身 session/log/cache 写入必须另行确认机制及允许范围，目前没有为这些路径授权；不得自行认定例外。每个 Host 都禁止 `run/judge`、安装依赖、提交/推送、改任何用户配置或 temper。允许清单是契约，当前不等于已验证的强制权限。

### A 提示词

```text
你做 M2 续作验收第一步，5 分钟内停止。PHE_REPO=<corpus>/repo，TARGET_REPO=<corpus>/target，实验目录 <exp>；只读 <corpus>/inputs。
先完整读 PHE_REPO/skill/harness-evolve/SKILL.md 和 docs/M-1_PROTOCOL.md。遵守 Skill §1/§2/§8；合成常量不是正式实验参数或预算批准。
先 phe.py status <exp>，核对退出 0、stage=empty、inconsistent=false、notes=[]；否则停下且不写日志。
先按 Skill §1 确认路径、任务起点、外置验收器、人工确认修复、真实模型和权限，并只读核对 inputs/blockers.json 与 inputs/proposal.json 的 fixture.blockers。任何前置失败或不清楚即停止：不复制 proposal，不写任何实验文件，不 log draft，报告具体阻塞，正常交接记为 not-tested。本轮合成输入的真实前提已 blocked，必须走此停止分支，不能把准备夹具当成已起草提案。
只有上级提供可核验材料使 Skill §1 全部成立后，才按 §2 从模板起草 proposal.json，不自行补造未知实际值。prepare_host_acceptance.py --check <exp>/proposal.json 仅核对本合成夹具结构与引用，不证明真实前提成立。
仅在前置通过并完成起草时，按 Skill §8 log --host claude --step draft，再 status，停止。此分支只允许写 proposal.json 和通过 log 追加 host_log.jsonl；不得冻结、写 approval、跑 run/judge 或改其他路径。最后报告命令、状态、写入文件和逐项正式阻塞。
```

### B 提示词（新 Codex 会话）

```text
你从文件续作 M2，5 分钟内停止。PHE_REPO=<corpus>/repo，TARGET_REPO=<corpus>/target，实验目录 <exp>。只有 A 成功通过前置并起草才会启动本会话；A 前置失败时不启动 B。
先完整读原 Skill 和协议。按 Skill §8 先 status，核对退出 0、stage=drafted、inconsistent=false、notes=[]；异常停下，不补文件、不改日志。
独立按 Skill §1/§2/§3 核对历史任务与人工确认修复、真实模型与权限、spike-000 预算、用户确定门槛、必填字段、schedule、config/harness/引用文件 Hash。prepare_host_acceptance.py --check 只提供结构检查，不代表正式前提成立。
逐项核对 fixture.blockers。本轮输入的历史来源、真实 Target/加载链、spike-000 和门槛批准均未满足；必须停止，不写 proposal.sha256，不 log freeze。这是阻塞分支，正常交接未测。
只有上级提供可核验材料使全部正式前提成立后，才按 Skill §3 对 proposal 原始字节生成侧文件、log --host codex --step freeze，再 status，停在用户批准边界。不得修改 proposal、编造缺值或沿用上个 Host 的批准；不写 approval，不运行 run/judge。最后报告状态、核验依据、具体阻塞和文件清单。
```

## 报告与未测项

准备报告：`experiments/m2-host-acceptance/20261029-host-acceptance-prep/report.md`（原工作树 Git 忽略），含版本、帮助探针、命令退出、合成任务验证、检查结果与阻塞。新增验收准备第 1 轮 P1 修复报告：`experiments/m2-host-acceptance/20261029-p1-round1-fixes/report.md`（当前工作树 Git 忽略），含旧实现失败对照、回归结果、独立 CLI 生成/只读检查输出及完整快照。原 CLI 报告继续保留。报告不含会话全文或私人内容。

未测：真实两 Host 会话、正常冻结交接、Skill 自动发现、写入前核对是否被执行、批准边界停下、真实会话隔离及自身写入、正式提案冻结。授权仅涵盖本轮准备；后续启动、正式预算批准和任何实验执行均不在本轮范围。
