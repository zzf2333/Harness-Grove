# M1 计划：首次导入与资产库

**状态：** 进行中（2026-10-28）。切片 A 完成（reviewer 第 3 轮 PASS）：本机盘点 56 条、7 个共享分组，其中 6 组指向 `~/AI/temper/config/`。M0 结果见 [M0_PLAN](M0_PLAN.md)。
**上位文档：** [PHE_SPEC](PHE_SPEC.md) 的「第一次运行：先盘点，再接管」「统一的 Harness 资产库」「里程碑与 Gate」。

M1 的 Gate 是：首次接管不改变配置含义；发布不覆盖带外修改。前三个切片只读用户配置、只写 PHE 仓库；只有切片 D 会改用户配置，必须另获批准。

## 完成条件

1. 能只读盘点 Claude Code、Codex、Pi 的用户级和项目级 Harness，列出文件、软链、目标和 Hash；无法确认的加载关系标 `unverified`，不写"已加载"。
2. 用户选择的条目能登记为 `Active(origin=imported, evidence_status=unverified)`，登记过程不改任何用户文件。
3. 基线之后的内容变化、软链目标变化、文件删除都能报为 `ExternalChange`。
4. 切片 D（发布、绑定、回滚）在用户批准并决定资产源之后完成。

## 切片

| 切片 | 内容 | 改用户配置 | 验收 |
|---|---|---|---|
| A | `tools/inventory.py`：静态盘点，产出 ObservedAsset JSON，全部 `unmanaged`、`load_chain: unverified` | 否 | fixture 单测；本机真实只读运行，抽查与 `ls -la`、`shasum` 一致 |
| B | `adopt`：登记进 PHE 仓库的 registry JSON | 否 | 单测；adopt 后再次盘点无差异 |
| C | 漂移检测：新盘点对比基线，产出 `ExternalChange` | 否 | fixture 中改内容、换软链目标、删文件三种都能报出 |
| D | Release、Binding、软链/渲染部署、回滚 | **是，需用户批准** | 待定；先决定资产源 |

## 盘点约束（切片 A）

- `--home`、`--project` 可指定，单测用临时目录；默认不写任何用户目录。输出写到被 Git 忽略的 `inventory/`。
- 候选路径写成数据表，注明"来自本机观察，未按官方文档逐项核实"。存在才记录。
- `settings.json`、`.mcp.json`、`config.toml` 等可能含密钥，只记 Hash 和顶层键名，不记值。
- 同一真实路径的多条记录归为一组，作为"可统一托管"的候选。
- 不存在的路径不记录；无权限的路径保留一条并写 `access_error`；`broken_link` 只在软链目标确实不存在时为 true。目录候选先记自身一条，再展开一层；断链、无权限或软链成环时只记自身。

## 待用户决定（切片 A 之后问）

- 本机已有事实上的统一资产源 `~/AI/temper/config/`：`~/.claude/CLAUDE.md`、`~/.pi/agent/AGENTS.md` 都软链到它的 `AGENTS.md`，`~/.claude/skills/*` 软链到它的 `skills/`。规范里的 `~/.phe/assets/` 会与它重复。PHE 是 adopt temper 作为资产源，还是另建？决定之前不设计 `~/.phe/assets/`。

## 不做

- 不安装 Skill、不推送、不写 `~/.phe/`、不调用模型。
- 实际加载链探测（需要启动 Agent 看日志）放到切片 C 之后或 M2。
