# M1 计划：首次导入与资产库

**状态：** 进行中（2026-10-28）。切片 A、B、C 完成（A 经 reviewer 3 轮、B/C 经 2 轮 PASS）；切片 D1 完成（沙箱）：见下文「切片 D1 演练」。D2（MCP、hooks、Codex config.toml）未开始；接管真实环境待用户批准。切片 A 结果：本机盘点 56 条、7 个共享分组，其中 6 组指向 `~/AI/temper/config/`（2026-10-28 口径修订后同机复跑为 55 条，见「盘点口径修订」）。M0 结果见 [M0_PLAN](M0_PLAN.md)。
**上位文档：** [PHE_SPEC](PHE_SPEC.md) 的「第一次运行：先盘点，再接管」「统一的 Harness 资产库」「里程碑与 Gate」。

M1 的 Gate 是：首次接管不改变配置含义；发布不覆盖带外修改。

## 原则：开发与使用分离（2026-10-28，用户决定）

- **项目实现**是本仓库的代码、文档和测试；**项目使用**是用户真实在用的配置，目前是 `~/AI/temper/config/` 及三个 Agent 指向它的软链。
- 开发期间对真实环境只读：盘点和漂移检测可以读，登记表只写本仓库被忽略的 `registry/`。
- 所有写入类功能（发布、改软链、回滚）只在沙箱里开发和验证：临时 HOME、temper 的副本、假的 `~/.claude`、`~/.codex`、`~/.pi`。工具必须显式传入目标目录，默认不碰真实的 `~/`。
- 接管真实环境是单独的决定，由用户在项目成熟后另行批准；届时先给改动清单，并保证能退回 temper。

## 完成条件

1. 能只读盘点 Claude Code、Codex、Pi 的用户级和项目级 Harness，列出文件、软链、目标和 Hash；无法确认的加载关系标 `unverified`，不写"已加载"。
2. 用户选择的条目能登记为 `Active(origin=imported, evidence_status=unverified)`，登记过程不改任何用户文件。
3. 基线之后的内容变化、软链目标变化、文件删除都能报为 `ExternalChange`。
4. 切片 D（发布、绑定、回滚）在沙箱里完成：把 temper 副本迁入 PHE 资产源、切换软链、回滚，且全程不碰真实环境。

## 切片

| 切片 | 内容 | 改用户配置 | 验收 |
|---|---|---|---|
| A | `tools/inventory.py`：静态盘点，产出 ObservedAsset JSON，全部 `unmanaged`、`load_chain: unverified` | 否 | fixture 单测；本机真实只读运行，抽查与 `ls -la`、`shasum` 一致 |
| B | `adopt`：登记进 PHE 仓库的 registry JSON | 否 | 单测；adopt 后再次盘点无差异 |
| C | 漂移检测：新盘点对比基线，产出 `ExternalChange` | 否 | fixture 中改内容、换软链目标、删文件、变不可读四种都能报出 |
| D | Release、Binding、软链部署、回滚 | 否（只在沙箱） | 沙箱中迁移、发布、回滚、冲突中止都可复现 |

## 盘点约束（切片 A）

- `--home`、`--project` 可指定，单测用临时目录；默认不写任何用户目录。输出写到被 Git 忽略的 `inventory/`。
- 候选路径写成数据表，注明"来自本机观察，未按官方文档逐项核实"。存在才记录。
- `settings.json`、`.mcp.json`、`config.toml` 等可能含密钥，只记 Hash 和顶层键名，不记值。
- 同一真实路径的多条记录归为一组，作为"可统一托管"的候选。
- 不存在的路径不记录；无权限的路径保留一条并写 `access_error`；`broken_link` 只在软链目标确实不存在时为 true。目录候选先记自身一条，再展开一层；断链、无权限或软链成环时只记自身。

## 资产源（2026-10-28 修订）

- 资产源是 PHE 自己的目录，默认 `~/.phe/`，可通过参数或 `PHE_HOME` 指定。temper 是第一份被导入、待迁移的现有部署，开发期间只读。
- 替代同日早先的“资产源是 temper”决定：temper 是项目的使用而不是项目的实现，通用框架不能把某一份部署写成资产源。

## 登记与漂移（切片 B、C）

- 登记表写在被 Git 忽略的 `registry/`（默认 `registry/assets.json`），只记路径、Hash、软链目标和状态，不记文件内容。
- 登记表用“同目录临时文件 + 原子替换”写入，写到一半失败时旧表不变；保存编号高水位 `next_asset_number`，删除条目不会让编号回退（手改或删掉整份登记表时只保证不与剩余条目重号）。不防两个 adopt 同时运行造成的丢更新，列为后续事项。
- `drift --out` 拒绝指向登记表、盘点或任何已登记文件（含软链和硬链接别名）。
- `ExternalChange` 只针对已登记的条目：内容变化、软链目标变化、文件删除或变得不可读。未登记的新文件不算漂移。

## 切片 D1 演练（2026-10-28，提交 40405e3）

- 实现经三组审查：主体 3 轮、多 Agent 部署放行规则 3 轮，均 PASS。测试 114 项 OK。
- 演练脚本 `tools/m1/rehearse_d1.sh`，可复跑；输出日志写到被 Git 忽略的 `experiments/m1-d1-rehearsal/run-*.log`（含本机路径，不入库）。真实 temper 只用 `cp -RL` 读取；沙箱 15 条链接，没有一条指回真实 temper。
- 结果：8 个资产登记、导入、发布为 `r0001`；claude、codex、pi、shared 依次部署都成功（6 + 1 + 3 + 5 条链接），15/15 条链接指向发布；改写发布文件后部署中止（退出 1），链接不变；四次 rollback 后对基线 drift 为 0，bindings 为空；`--home` 指向真实 home 时退出 2。shared 的 5 条链接替换的是沙箱里原本指向 temper 的 `~/.agents/skills/*`，所以链接总数仍是 15（2026-10-28 口径修订前是三个部署目标）。
- 真实环境：演练前后 temper 文件与四个 Agent 目录软链的 Hash 一致。
- 不保证：加载链未探测（沙箱里没有 Agent 读这些文件）；检查与写入之间的并发替换（TOCTOU）；同时改写发布文件和 manifest 的 sha256。

## 盘点口径修订：Pi 子代理目录与共享 skills（2026-10-28）

- 新增目录候选 `~/.pi/agent/agents/`（Pi 子代理定义）：按目录候选规则先记目录自身、再展开一层，kind 归 `instructions`（kind 枚举没有 agent，与 `.claude/agents` 一致）。
- `~/.agents/skills`（用户级和项目级）原来记在 `codex` 名下，实际是跨工具共享目录：Codex 和 Pi 都读，Claude Code 不读。新增 agent 值 `shared`，这类记录另带 `readers: ["codex", "pi"]`；单一 Agent 的目录不记 `readers`（读取者就是 `agent` 本身）。不沿用相近的现有项，先扩 agent 取值集合。
- 连锁改动：`deploy --agent` 因此接受 `shared`。实现本来没有 agent 枚举校验，按盘点记录里的 `agent` 值取路径，`bindings.json` 以 agent 名为键，所以这两处不需要改结构；只改 `tools/m1/rehearse_d1.sh`，给 claude、codex、pi、shared 各部署一次并逐次回滚。
- 本机复跑（2026-10-28，`--home ~`，输出在被忽略的 `inventory/`）：55 条记录、7 个分组；其中 `~/.pi/agent/agents` 7 条（自身加 6 个 `.md`），`agent: shared` 7 条。此前 56 条还包含项目级扫描，与本次仅 `--home` 的范围不同，不能直接作为增减对比；本次新增 7 条来自 Pi 子代理目录。
- 保证：`agent` 取值集合是 `claude`、`codex`、`pi`、`shared`；`shared` 记录一定带 `readers`，其他记录一定不带；deploy 只部署登记表 `observed` 里该 agent 值的路径。
- 旧登记表的 `observed.agent=codex` 不会因重新盘点自动变成 `shared`；`adopt` 对已登记资产幂等，也不承担迁移。部署仍按旧 observed 执行，旧表迁移须另行设计和批准，本次不改已有登记表。
- 不保证：`readers` 来自本机观察，未按官方文档核实，也不代表加载顺序或优先级；`shared` 只表示同一条路径被多个工具读到，不表示它们读到的语义一致。

## 不做

- 不安装 Skill、不推送、不调用模型；不写真实的 `~/.phe/`、temper 或任何 Agent 配置目录。
- 实际加载链探测（需要启动 Agent 看日志）放到切片 C 之后或 M2。
