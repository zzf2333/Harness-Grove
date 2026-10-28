# CHANGELOG

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
