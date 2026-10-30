# CHANGELOG

## 2026-10-30 · 轻量评估闭环的可复跑示例

- 新增 `examples/lightweight-loop/`：一份合成 pi 会话（13 行，L6 是“回答先给结论”的偏好，L9、L12 是用户叫停越界改相邻文件）、5 行最小配置 `config/AGENTS.md`（与 `tests/test_change_lightweight.py` 的 `LIGHT_BASE` 逐字节相同）、宿主手写的样例报告 `host-report.md`（W1 偏好 / W2 清理重复 / W3 收益待验证的假设，标注“不是工具自动诊断”）、`render_inputs.py`（标准库，不 import `tests/`，生成变更包、`source=sandbox_rehearsal` 的演练批准与 W3 的 `observe`/`unknown` review 记录）和 `README.md`。三个 patch 与测试里的 `PREFERENCE_PATCH` / `CLEANUP_PATCH` / `ASSUMPTION_PATCH` 逐字节相同；测试是机器断言，示例是给人逐条跑的同一流程。
- README 每一节标“工具生成”或“宿主判断”，命令可逐条复制；先说明只写 `mktemp -d` 建的临时工作区、不碰真实 `~/.pi`、`~/.codex`、`~/.claude`、`~/AI/temper`，再串起 `history.py digest` → 回读 `L` 行号 → 盘点/登记 → `change.py apply`（只批 W1、W3）→ `status` → `review-plan` → `review` → 逆序 `restore` → `cmp` 回原字节。写清批准与 `apply` 成功不等于效果已验证、`sandbox_rehearsal` 不是用户批准、W3 复盘保留 `unknown`、样例会话在应用之前不能当观察证据。
- 以首次使用者身份在全新临时目录只按 README 复跑，发现并修正两处文档问题：`mktemp -d` 要用 `pwd -P`，否则 `registry.py adopt --group` 与盘点里的 resolved path 对不上会退出 1；清理步骤要先 `chmod -R u+w` 再 `rm -rf`，因为发布目录按设计只读，只写 `rm -rf` 会留下半个临时目录并退出 1（首次运行的实际阻塞）。`tools/` 未改。
- 验收证据（命令与退出码、摘要、apply/status/review-plan/review/restore 的 JSON、账本快照、19 条检查、报告）留在被 Git 忽略的 `experiments/lightweight-loop-acceptance/`，本次用 `git add -f` 精确加入这些证据文件。
- 未验证：真实配置写入、真实 Host 是否加载改后配置、观察窗内的行为统计、`source=user` 的用户批准路径与 L2 对照实验；示例只证明闭环能跑通，不证明改动有效。

## 2026-10-30 · 轻量评估口径贯通到变更包与复盘

- 消除与 PHE_SPEC §2.5 冲突的旧口径：Proposal 不再要求“能计算指标才能生成”或“每条都带实验验收方法”。报告示例、架构图、资产生命周期、M3a Gate 改为默认三行说明与最小评估；M-1、L2 Gate 的原协议保留，并标明选择实验时适用。
- 变更包复用现有字段承载三行说明：`prediction.why`（为什么改）、`expected`（改后期待什么，含不能丢的约束）、`verify`（凭什么判断，含未知项或“收益待验证”）、可缺的 `baseline`。工具原本只要求整个 `prediction` 存在，内部键不校验、原样入账，所以没改 `tools/change.py`：旧变更包继续可用，偏好和清理条目不做行为实验也能走完批准、应用、记账、复盘、恢复。
- “未验证采纳”用 `approval(source=user)` + `apply` 事实 + 没有效果证据表达；`review.verdict` 不新增值，收益没测时仍是 `observe` / `prediction_met=unknown`。文档写明批准或应用成功不等于效果已验证，偏好符合用户意图不等于证明提升任务表现，沙盒演练批准仍是 `sandbox_rehearsal`。
- 新增 `tests/test_change_lightweight.py`（复用 `tests/test_change.py` 的 fixture，只换合成内容）一条按 §2.5 口径的正常链路用例：偏好 / 清理重复 / 减少失误的假设三条写三行说明，只批准偏好与假设 → 应用 → 对假设写 `observe` / `unknown` 的 review → 逆序恢复回原字节；断言账本保留 `source=user` 与三行说明原文、清理条目未生效、没有字段把批准或应用写成效果已验证，并覆盖旧格式变更包仍可用。
- 未验证：没有对真实会话跑这条链路，也没有做行为实验；工具行为未改，没有新的执行路径需要真实验证。

## 2026-10-30 · 评估口径：默认轻量，值得时才实验

- PHE_SPEC 新增 §2.5：每条建议只写三行评估说明（为什么改、改后期待什么、凭什么判断）；按改动性质（偏好 / 清理 / 失误或效率假设）选最小必要评估；L1/L2/L3 不要求每条走完，L2 沙盒对照只在用户点名、有争议或后果严重时做；量化不了的写“收益待验证”，可由用户做未验证采纳。写入实验有效性检查（不要求基线先失败，判定识别不同写法、认不出记“无法判定”）、结果分三行报告、节省 token 的做法与常见误读。
- Skill §11 与 M3a_PLAN 切片 1 的报告字段改为三行评估说明，替代原“问题、证据、预期效果、验证方法”。
- 依据：2026-10-30 独立沙盒真实 pi 对 P1/P2 的三轮对照（`experiments/loop-host-001`~`003`，共 22 次主会话）没有得出可用的效果结论，成本高于两条短规则可能的收益。这三轮未按 M-1 协议判定，不计入 L2 主张的 pivot 次数。本次只改文档，工具行为不变。

## 2026-10-30 · 闭环切片 3：复盘账本与 review 记录

- 新增 `tools/change.py review-plan/review`（复盘入口在 `tools/change_review.py`，账本事实与推导拆到 `tools/change_ledger.py`）。`review-plan --phe-home` 只读列出每个执行（含已恢复）的 `execution_id`、`proposal_id`、`state`、`applied_at`、`restored_at`、原预测、证据、报告引用、观察窗（应用到恢复，未恢复为 `null`，取样上界取本次复盘时刻）、全部历史 review 和最新一条，并说明观察窗只是候选取样范围、不证明 Host 加载过配置。
- `review --execution x0001 --record <JSON> --phe-home` 只追加一条 `kind=review` 事实：记录内容、记录文件路径与原始字节 sha256、`at`。`verdict` 只有 `keep` / `adjust` / `revert` / `observe`，`prediction_met` 只有 `met` / `not_met` / `unknown`；`observations` 是证据指针列表（可为空），`next` 写下一步或缺什么。工具只记录、不判断效果：不改 `applied`/`restored` 状态、不调用 `restore`、不碰配置和发布；同一执行可多次 review，旧行不改写。`status` 每个执行增加 `latest_review`（无则 `null`）。守卫只查 `--phe-home`，不需要 `--home`。
- 语义写进文档：`verdict=revert` 只是建议，用户批准后才 `restore`，已恢复的执行不重复恢复；`observe` 表示证据不足，对已恢复执行表述为“待补证据”；`prediction_met` 只说原预测是否达到，不是因果证明；写入临时配置 ≠ Host 已加载。
- `tests/test_change.py` 增加一条正常链路用例：apply → review（`observe`/`unknown`）→ review（`keep`/`met`），两条历史保留、`latest_review` 为后者；`review-plan` 回显原预测、证据、报告引用与观察窗；`restore` 后 `review-plan` 与 `status` 仍读得到 review，且 review 前后目标文件 sha256、发布链接和执行状态不变。
- 新增 `tools/rehearse_review.py`（合成样例在 `tools/rehearse_review_synthetic.py`）：复制切片 2 的 `experiments/loop-apply-001/phe/` 到 `experiments/loop-review-001/phe`（原目录不改），对真实演练执行 `x0001` 写 `observe`/`unknown` 的 review；另在 `experiments/loop-review-001/synthetic/` 下建一套独立【合成】沙箱（小 base + 三个软链入口 + 全新 PHE_HOME），apply 合成条目 `S1` 后不 restore，`to` 是 `null`，取样上界取演练声明的【模拟复盘时刻】`cutoff = applied_at + 60 分钟`，窗口因此是 `[应用时刻, cutoff)`，合成会话时间戳按相对实际 `applied_at` 的偏移生成，按窗口两端取样后手算 `cwd does not exist` 次数，结论按窗口内计数推导（本例窗口前 2 次、窗口内 2 次、窗口后 1 次 → `adjust`/`not_met`），晚于 `cutoff` 的命中不计入；另用零宽窗口样例证明切片 2 `x0001` 的 `[T, T)` 窗口内观察数为 0（含恰好等于端点的一条），结论只能是 `observe`/`unknown`。49 条断言全部成立，工件不清理；合成产物与真实演练账本分开，只证明复盘流程可运行，不证明规则有效。报告渲染在 `tools/rehearse_review_report.py`。
- 边界：没有自动取样、自动统计或自动判定，观察窗与计数由宿主给出；review 记录不构成效果证据。取样按源记录自身的 `ts` 核对半开区间 `[from, 上界)`：`to` 为 `null` 时上界取本次复盘时刻，不取复盘之后的记录；恰好等于上界的记录不算窗口内。`history.py --since` 只到日期粒度、只能粗筛。切片 2 两个执行的 `applied_at` 与 `restored_at` 同秒，窗口退化为 `[T, T)`，真实复盘要在应用后、恢复前取样。畸形 record、并发写入、权限与攻击场景不在本轮范围。

## 2026-10-30 · 闭环切片 2：批准、应用与逆序恢复（仅临时配置）

- 新增 `tools/change.py apply/status/restore`。`apply` 读宿主显式准备的机读变更包（报告路径与 sha256、条目 ID、证据、预测/验证方法、目标 asset id、patch）和批准记录（`change_sha256` 绑定确定版本的变更包、`items` 绑定获批条目、`source` 只有 `user` 与 `sandbox_rehearsal`），从目标入口当前实际生效的内容起算，用 `git apply` 打 patch，按 `release import` 的同一 `asset.json` 格式重建资产，再复用既有 `release` 与逐 Agent `deploy`；三个入口全部完成后才追加事实。`status` 从事实推导 `applied`/`restored` 与生效栈，并读文件核对生效发布和 sha256；`restore` 按 journal 逆序恢复。
- 账本 `<phe-home>/ledger.jsonl` 只追加 `approval`/`apply`/`restore` 事实：执行 ID（`x0001` 高水位，与提案 ID 不同）、批准来源、报告与变更包 sha256、预测、实际发布号、相对 journal 路径、前后内容 sha256、恢复后生效发布。不存可推导的状态，恢复不表示提案无效或 retired。
- 每次 apply 从当前生效内容重建资产，避免“rollback 只退链接、下一次 release 把已撤销内容带回”；同一资产只支持按执行逆序恢复，不实现保留 B 单独撤销 A，也不做补丁重排。
- 修一个正常路径缺口：既有 `rollback` 会 `unbind`，恢复到的若是更早一次 PHE 发布，下一次 `deploy` 的漂移预检会把它当成 `ExternalChange` 中止（已实测复现）。`restore` 在 rollback 后按 journal 的 `old_target` 重新绑定到本 PHE_HOME 的发布；没有绕过或放宽既有保护。
- 新增 `tests/test_change.py`：合成两条改动 + 三个 Agent 入口，一次顺序 E2E 覆盖只批准 P1 → 批准并应用 P2 → 恢复 P2 保留 P1 → 从当前版本应用 P3（hunk 以 P2 改过的行作上下文）→ 逆序恢复回原字节，并断言账本来源/执行 ID/前后 hash、`status` 与文件一致、逆序与批准覆盖范围的拒绝。
- 新增 `tools/rehearse_change.py` 与 `tools/rehearse_change_input.py`：用真实切片 1 报告 `reports/2026-10-30.md` 的 P1/P2，在真实 `config/AGENTS.md` 的只读副本上跑同一流程，64 条断言全部成立；工件（输入变更包、演练批准、发布、journal、账本、命令日志、断言报告）留在 `experiments/loop-apply-001/`（被 Git 忽略）。演练批准写 `source=sandbox_rehearsal`，不是用户批准。
- 边界：只覆盖单个 instructions 资产 + 三个 Agent 的临时软链入口，只在临时沙箱跑；skill 资产、普通文件入口、多资产发布、写真实 `~/.pi`/`~/.codex`/`~/.claude`/temper 都不在本轮。效果未验证：P1/P2 的预测基线仍是“待观察”，没有对照实验；三入口是否被对应 Host 实际加载也未验证。

## 2026-10-30 · 闭环切片 1：真实会话摘要入口

- 新增 `history.py digest` 与 `tools/history_digest.py`，复用来源遍历、时间窗、Host 解析、脱敏和输出守卫，按会话生成约 40KB 的 Markdown 分块与索引。正文只列真人用户发言、工具失败、retry 标注，带源文件绝对路径和 `L行号`；无真人发言的会话只在索引计数。`Session.human_message` 提供普通真人消息，`collect/group` 的三种 kind 保持不变。
- 新增三家 Host 的合成真实 CLI 测试，覆盖真人/注入区分、失败与原文行号、会话跳过、分块与 collect 契约。真实最近 7 天只读冒烟产物存于主仓库被忽略的 `episodes/digest-2026-10-30/`，不提交会话内容。
- PHE_SPEC、M3a_PLAN、README、Skill 同步闭环优先级：B2/C 暂停，D 缩小为改前写预测与验证方法，L2 实验可选。Skill §11 增加按块只读诊断、回读源上下文与合并报告流程，报告包含实际 diff 和验证方法，无基线写待观察；`reports/` 加入忽略。当前只交付摘要零件，诊断报告与配置应用尚未执行。

## 2026-10-30 · M3a 切片 B1：显式 Retired 检索输入与关系核对

- `draft` 新增可选 `--retired <JSON>`：Host 显式给出的有限文件（`schema_version: 1`、`scope`、`entries`）按原顺序逐条列举，不推断语义相关、不排序、不截断、不选 topK，也不扫描目录或真实退役记录。`candidate.retired` 只有 `unavailable`（未提供或读不到，带原因）和 `completed`（来源路径 + 原始字节 sha256、`scope=all_entries_in_supplied_file`、`entries`）两种状态；只提供路径但读不到时保存 `unavailable` 并写原因，不冒充 no_match。`entries` 为空只表示该文件为空。
- Host 候选新增可选 `retired_review`：总体 `relation` / `reason` 加逐条 `retired_id` 关系（`unrelated` / `alternative` / `reopen`），**不接收** `matches`、`status`、`user_authorized` 等字段。缺声明/理由可保存并列入 `missing`；非法枚举、未知或重复 `retired_id`、引用当前候选未选中的分组直接拒绝。`alternative` / `reopen` 要求引用退役之后、且不在该记录 `evidence_groups` 里的分组，否则列 `retired_evidence_group_reused` / `retired_evidence_time_unknown` / `retired_evidence_not_after_retirement` 阻塞。只校验必要结构和时序，不声称新证据语义独立或充分，也不实现手动恢复。
- `check` 默认仍不回读外部输入，报告用 `retired_source_check=not_performed` 明确说明无法检测外部变化；新增 `check --retired <file>` 显式重算来源 sha256 并比对存储的记录，变化或来源不可用时报 `re_search_required`、退出 1，且不改写 proposal。存储的 Retired 结构与 Host 判断用与 `draft` 相同的规则检查，不信任草稿自造的枚举或模板。
- `freeze_ready` 在 B1 仍恒为 false，新增固定阻塞 `prediction_freeze_not_implemented`；旧切片 A 的 `unavailable` 草稿照常列阻塞，不迁移、不重写。目录覆盖能力不变（已有或冻结目录仍拒绝写入），`dont_write_bytecode` 保留。
- 新增 `tools/proposal_input.py`（共用输入校验）、`tools/proposal_retired.py`（Retired 输入、关系核对、来源复核），`tools/proposal_candidate.py` 只保留候选段与报告；新增 `examples/m3a/` 三份可直接跑的合成输入示例（分组、带 `retired_review` 的候选、Retired 检索）。README、SKILL §10、`docs/M3a_PLAN.md` 同步。真实的 Retired 数据源/自动检索（B2）、独立证据判断（C）、完整预测冻结（D）未开始，**M3a 未完成**。
- 修两个 P2 缺口：`candidate.retired.schema_version` 只接受 JSON 整数 `1`，`1.0` / `1e0` / `true` 均退出 1 且不留产物（原先 `1.0 == 1` 被放行）；报告 `notes` 与 `docs/M3a_PLAN.md` 写明时序检查只依据 proposal 内可编辑的 `pointers.ts`，默认 `check` 与 `check --retired` 都不回读原始分组文件、不复核原始时间，`retired_source_check=unchanged` 只说明 Retired 来源文件字节未变。

## 2026-10-30 · M3a 切片 A 与 M2 结项

- 新增 `tools/proposal.py draft/check`（候选结构在 `tools/proposal_candidate.py`）：从 M2 分组产物生成经验候选提案。证据只复制 `source_path`/`line`/`ts` 和分组文件原始字节 sha256，不复制摘录；复用 M-1 模板完整形状与 `metrics`，只改实验身份、`status=draft` 和 artifact 路径，并追加 `candidate`。
- 缺假设/预测字段可保存并逐项报告；未知 metric、组不存在、同一 ID 对应多份内容、畸形 JSON/类型、已存在目录（含冻结目录）和路径穿越/软链逃逸拒绝，输入错误不留下半份产物。`candidate.retired` 固定 `unavailable`，本切片 `freeze_ready` 永远不是 true，报告只覆盖候选与明显未填项，不是完整协议验证。
- `check` 的 metric 白名单只取仓库模板：改草稿自己的 `metrics` 不能放行未知指标；证据计数必须等于指针数、组 ID 不得重复；`draft`/`check` 导入相邻模块前关闭字节码缓存，不在仓库副本里留下 `__pycache__`/`.pyc`。
- `check` 只读新 proposal，不回读分组文件或会话原文，不写侧文件、批准或冻结 Hash；没有 `candidate` 的旧提案报 `legacy_proposal_out_of_scope`，不因新检查失效。Skill 补“带 candidate 的草稿先 check”的流程约束，并说明它不是执行层封锁。
- 新增 `docs/M3a_PLAN.md`：切片 A 完成，B（Retired 真实检索）、C（reopen/alternative 处理）、D（完整预测冻结）未开始，M3a 未完成；示例全部为手写合成材料。
- M2 按原定 Gate 结项：S1 阻塞交接、S2 正常冻结交接通过；Target 按需逐个适配。Codex Target 真实运行、费用/隔离验证和 m2-real-002 的加载链阻塞保留为后续事项，不追加为 Gate。未重跑 S2。

## 2026-10-30 · m2-handoff-002 S2

- Pi 起草、真实 Codex Host 独立复核后冻结同一提案，proposal 字节不变，只新增冻结侧文件并追加日志；未批准或运行 Target。
- 原文件清单 Hash 在交接时未复现；主控找回原始算法，在两份独立重建副本上复现冻结值后接收 S2。保留交接时的未验证记录及补核证据。
- 区分冻结前的配置、来源、权限与 Hash 核对，以及运行前的实际加载验证；运行阻塞继续保留。Codex Host 验收不等于 Codex Target 实测。

## 2026-10-30 · m2-handoff-001 S1

- 首次真实跨 Host 交接：Pi 起草合成草案，真实 Codex 会话（用户批准的一次 `codex exec` 例外）只凭文件读取阶段、核对 Skill §1–§3，结论为不能冻结并停止；文件快照除报告外不变。
- 只证明阻塞交接；正常冻结交接（S2）仍 blocked，M2 Gate 未通过。
- 新增 `ruff.toml`（line-length=120）和 MIT `LICENSE`。

## 2026-10-30 · M2 状态核对

- M2_PLAN 新增 Gate 进度表，记录 m2-real-002：Claude Code Target 启动 1/15 次，服务端 403 拒绝订阅登录，停在 `pivot:telemetry_missing`，未续跑。全部步骤由 Pi 完成，不算跨 Host 证据。
- M2_HOST_ACCEPTANCE 新增「验收口径」：Host 交接证据与正式实验前提分开记录；交接验收不要求 `run`/`judge`，`--host` 标签不算 Host 身份证据；正式前提保留不变。修正“无 spike-000”：历史测量存在，适用性未核对。
- README「当前状态」补 M1/M2；PHE_SPEC 状态行不再写“尚未运行任何真实实验”。

## 2026-10-29 · Codex Target 最小接口

- `phe.py run` 按 `proposal.target.agent` 选择 `claude-code`（旧默认）或 `codex`；`config.target_agent` 只声明同一选择，冲突时启动前拒绝；Codex 拒绝 `config.target_argv` 覆盖，正式调用只用规范 argv。
- Codex 默认命令使用 `codex exec --json --ephemeral --ignore-user-config -s workspace-write -C <work> -m <冻结 model_id> -o <output>/last-message.txt <prompt>`，不添加权限绕过或 max-turns 参数。
- 新增 `tools/phe_target.py`：Codex JSONL 解析只取最后一次 `turn.completed` 的 thread 累计 usage，TOK 只算 input+output；坏行、缺 usage、负数和全 0 不补零，失败/致命 error/非零退出保留 error 语义；最后一个 `turn.started` 未被终态闭合时按不完整流处理，清空用量并记 `parse_error`；`final_message` 保留 agent_message 文本。
- Codex 费用保持 `None`，继续走 `pivot:telemetry_missing`；没有新增估算、价目表或协议字段，也没有 max-turns 预算控制。
- Codex 指纹只覆盖 `~/.codex/AGENTS.md` 和 `config.toml`，不读 auth；runner 模块 Hash 增加 `phe_target.py`；Hash 要求取自冻结 proposal 的原始 `target.agent`（不经过 Target 适配器），Codex 实验要求完整三项，旧 Claude sandbox 实验仍接受两项或三项形状（runner 字节改动后需重新冻结）。完整性判定和停止记账排在目标解析之前。
- 只用测试层替换 subprocess 边界的合成 JSONL 验证，没有启动真实 Codex 会话；`installation_id` 写入、实际会话隔离、加载链和费用仍未验证。

## 2026-10-28 · M2 代码阶段收尾

- M2 切片 A0/A/B 完成：历史格式说明、只读采集、结构分组；切片 C 代码部分完成，新增 `phe.py log/status` 和 Skill 续作步骤。两个真实 Host 的续作验证尚未执行，M2 整体仍进行中。
- `host_log.jsonl` 独立于冻结的 proposal，保留原有哈希与批准绑定；status 从文件推导阶段，不持久化阶段。
- 盘点收录 Pi 子代理定义目录；用户级和项目级 `.agents/skills` 改记为 `shared` 并附本机观察的 readers。旧登记表不自动迁移。
- 清理 `tools/m1` 既有 ruff 问题；组合版本 199 项测试通过，全仓 ruff 检查与格式检查通过。未运行消耗额度的真实 Agent 试验。

## 2026-10-28 · M1 决定修订

- 切片 D1 完成：沙箱内导入、发布、symlink 部署、回滚，经两组各 3 轮审查；temper 副本演练通过，真实环境 Hash 前后一致。
- 撤回“资产源是 temper”：temper 是项目的使用，不是项目的实现。资产源回到 PHE 自己的目录（默认 `~/.phe/`，可配置）。
- 新增“开发与使用分离”原则：开发期间对真实环境只读；发布、改软链、回滚只在沙箱验证；接管真实环境由用户另行批准。

## v0.6 — 2026-10-28

- 新增原则：只用用户已有的 Agent 环境和登录额度，不需要、也不申请单独的模型 API key 或计费账户。取代 v0.5 中“隔离配置目录下可能只能用 API key 按量计费”的说法。
- M-1 的 Target 隔离改为：在现有登录下用 `--setting-sources project`、`--strict-mcp-config`、`--disable-slash-commands`、`--settings` 收窄加载范围，具体组合由 spike-000 探测。排除不掉的用户级 `CLAUDE.md` 作为各组共有的常量记录 Hash；自动记忆必须关闭或确认不写入。只有另开账户才能隔离时，结果为 `pivot(isolation_unavailable)`。
- `total_cost_usd` 明确为用量代理值，不是额外付款；订阅额度用尽或限流按基础设施失败处理，占用运行位置。
- 模板新增 `auth_source`、`separate_api_key_allowed=false`、`isolation_flags`、`unexcludable_user_content_sha256`、`auto_memory_disabled` 等字段。

## v0.5 — 2026-10-28

文档结构：

- 拆成两份文档：`docs/PHE_SPEC.md` 只写长期设计和里程碑；`docs/M-1_PROTOCOL.md` 写会被冻结、按实验改版的执行细节。
- 新增仓库根目录 `README.md`（文档地图和阅读顺序）；删除交付说明 `docs/README.md` 和弃用指针文件。v0.4 原稿保存在初始提交里。
- 修正误置的目录：`docs/docs/PHE_SPEC.md` → `docs/PHE_SPEC.md`，`docs/examples/` → `examples/`。
- 合并 v0.4 中重复的生命周期描述；跨文件引用改用章节标题，不用编号。

M-1 协议：

- 新增 spike-000 用量测量：最多 2 次调用，只跑 A 配置，同时探测认证方式、加载链和用量字段。结果只用于设定 spike-001 的预算，不参与判定。
- Token 只记录和比较，不再作为停止条件，删除 100k / 1M 上限。原因：`cacheReadInputTokens` 随轮数累积，很可能让正常运行全部触发停止。
- 单次时间、回合数和单次费用改为由 spike-000 推导；总墙钟改为公式 `25 × (T_run + 33) + 1800`，默认 7,125 秒。v0.4 的 5,400 秒只比运行上限之和多 75 秒。
- A/A Token 噪声门槛改为冻结前由用户填写，0.25 / 0.30 只作为没有实测依据的建议值。通过次数的波动是主门槛。
- `stop_claim` 改名为 `invalid_run`，只表示正式运行中完整性被破坏；运行前就无法隔离或无法控制预算的情况归为 `pivot`。
- 明确隔离配置目录下可能只能用 API key 按量计费，须在预算审批时告知用户。

产品规范：

- 新增「放弃 L2 主张的出口」：累计 3 次 pivot，或正式实验累计估算费用达到 spike-001 获批上限的 3 倍，就退回只读审计和配置统一。
- 明确 M-1 需要一个最小 Runner，不是纯手工实验。

本版仍然没有执行任何真实 Agent 调用，没有实测数据。

## v0.4 — 2026-10-28

- 固定 M-1 Go/Pivot/Stop 的判据与优先级，区分探针通过和真实规则效果。
- 以人工回执规则和重复句子作为控制，不从未知真实规则中挑“已知必要/无用”。
- 5 组 × 5 次，合计最多 25 次 Target 启动；不隐藏补跑、预热或控制组。
- 单独定义成本、时间和 Token 的限制；不把客户端估算当作硬账单封顶。
- 固定 M-1 Claude Code 用量口径，保留 Host 无关架构。
- 统一 PHE_REPO、TARGET_REPO 和临时 worktree 的权限及路径。
- 删除实验由 Proposal 与 CandidateHarnessSnapshot 表达，不制造不存在的 Trial 资产。
- 旧文档只保留弃用指针，M3 拆为 M3a 经验与 M3b 想法入口。
- 移除未经此次核验的外部版本/研究断言作为开发依赖。

此包未执行真实 Agent 调用；没有验证任何规则的收益或填入任何真实实验结果。
