# 轻量评估闭环示例·第二轮：证据表述纠正（本轮结果）

本轮只做一件事：纠正 `examples/lightweight-loop/` 里 W3 的**证据过度表述**，再用同一份 README 复跑，
证明报告、变更包、账本、review 与原文一致。合成会话原文**没有改动**。

上一轮（`../report.md`、`../checks.md`）证明“15 个 bash 块能跑通、字节与账本对得上”，但没核对
“W3 的证据到底说了几件事”，于是把“两条叫停”写成了“两次因 Agent 顺手改相邻文件被叫停”。这是内容缺陷，
流程检查看不出来。

## 原文事实（唯一依据，不改）

`pi-root/sessions/…2026-10-28T09-00-00-000Z_7c1d0c4e-….jsonl`：

| 行 | 角色 | 内容 |
|---|---|---|
| L2 | 用户 | 让 Agent 整理 `config/AGENTS.md`，“顺便看看 README 的措辞” |
| L7–L8 | 助手 / 工具结果 | `edit` 改 `config/AGENTS.md`（目标文件本身，不是相邻文件） |
| L9 | 用户 | “停一下，我没让你改 README。先别做相邻改动。” |
| L10 | 助手 | `toolCall edit {"path": "README.md"}` |
| L11 | 工具结果 | “已写入 README.md”（成功） |
| L12 | 用户 | “不是让你顺手改别的文件，我说的是只动 config。停下来。” |

所以：**两条叫停消息，一次可见的相邻文件写入（L10 调用 + L11 结果），而且这次写入发生在 L9 已经明确
叫停之后。** 原文不支持“两次独立越界”。

## 本轮改动

1. `host-report.md`
   - W3「证据指针」补 L10 / L11，并写明“唯一可见的相邻文件写入，在 L9 明确叫停之后”。
   - W3「为什么改」改成事件计数：两条叫停 + 一次可见越界；L7/L8 改的是 config，不算相邻越界。
   - W3「凭什么判断」保留“收益待验证”，补一句“单条合成会话说不出真实环境里的越界频率”。
   - 新增「材料边界」三条：`config/AGENTS.md` 是独立准备的演练基线、不是会话编辑后的快照；未提供 Retired
     记录、没做退役匹配，不能声称没有历史冲突；同一会话只提供一条可见越界，两条叫停不是两个独立事件证据。
2. `render_inputs.py`
   - W3 `evidence` 增加 `:L10`、`:L11`；`prediction.why` 与报告同口径。
   - W3 的 review 记录 `reason` 补“只有一次可见越界写入”，`observations` 增加 L10、L11；`verdict=observe`、
     `prediction_met=unknown` 不变。
   - 三个 patch、W1/W2 的三行说明、批准范围（只 W1/W3）、`source=sandbox_rehearsal` 都没动。
   - `change.json` 的 `prediction.why` 是报告 W3「为什么改」的精简版：去掉 Markdown 代码标记，并把
     “L7、L8 改的是 `config/AGENTS.md`（L2 用户本来就要求整理它）”压成半句；`expected`、`verify` 与报告逐字相同。

W1 / W2 / W3 三行说明与实际 diff、原文指针的对照已由 `outputs/checks.txt` 逐条核过（见「检查结果」）。

## 复跑

`runner.sh` 由 `build_runner.py` 从 `examples/lightweight-loop/README.md` 机械拼出：README 第 1–14 个
bash 块原样 → 证据采集段（不属于 README，只读命令与复制 `$WORK` 里的文件）→ 第 15 个 bash 块原样。
`outputs/blocks-source.txt` 记录每个块在 README 与 `runner.sh` 中逐字一致（15/15 True）。

`runner.sh` 是**本轮实际执行的脚本**，开头的 `cd` 写死了本轮工作树的绝对路径；工作树清理后要重跑，先把那一行
改成仓库根再执行（其余内容不用改）。

| 项 | 结果 |
|---|---|
| 15 个 bash 块 | 全部 `exit=0`（`commands.log`） |
| 证据采集段 | `exit=0`，24 项检查全部通过（`outputs/checks.txt`） |
| 临时工作区 | `/private/var/folders/…/T/tmp.emlGbvnBWB`，第 15 块 `chmod -R u+w` + `rm -rf` 后 `WORK 已删除：是` |

### 本轮重新生成的 hash

| 工件 | 值 |
|---|---|
| `host-report.md` sha256 | `53699c1b52e7a67743489b00d62312576f9e857992565a94b198d406930551a4` |
| `change.json` sha256（= 批准绑定的 `change_sha256`） | `df31a0b71e01f9d2184c5d51bc241e8fc3c1db5f146cf73222cae40e2e9231a2` |
| 批准 | `source=sandbox_rehearsal`，`items=["W1","W3"]`，W2 未批准 |
| 原配置 / 恢复后共享文件 sha256 | `33385c1002dbddc188f311a998d428b7bbf324397ec83a497925cd86654d1f66` |
| W1 之后 / W1+W3 之后 sha256 | `4d5f4396…d5880` / `a9821367…47421` |

### 实际字节

- 三个入口（`.pi/agent/AGENTS.md`、`.codex/AGENTS.md`、`.claude/CLAUDE.md`，都是软链）第 2 行变成
  “先给结论，再复述问题。”，第 4 行末尾多了“也不要顺手做相邻改动” —— 见 `commands.log` block 9。
- 未批准的 W2 没生效：`grep -c '^先给结论。$'` 输出 `2`，重复句仍在两处。
- 逆序 `restore` x0002 → x0001 后，三个入口与共享文件回到原字节（`cmp` 无输出、四个 sha256 相同、
  软链仍在）；`outputs/checks.txt` 另外核对账本 `restored_sha256` 与原文件一致。

### 账本与 review

- 7 条事实：`approval`/`apply`（W1、W3）、`review`（x0002）、`restore`（x0002、x0001）；`source` 全部
  `sandbox_rehearsal`，W2 从未入账。
- `prediction.why`/`expected`/`verify`/`baseline` 原样入账，与 `change.json` 逐字一致（`outputs/checks.txt`）。
- 账本里没有“已验证”、没有 `verified`/`effect` 字段、非 review 事实没有 `prediction_met`。
- review 只有一条，`verdict=observe`、`prediction_met=unknown`，`observations` 含 L9/L10/L11/L12 与
  `change.json`；`review-plan-final.json` 显示原预测与观察窗 `to: null`。

## 检查结果

`outputs/checks.txt` 的 24 项（全部通过）与本轮内容检查 15 条见 `checks.md`。项目检查
（`checks-project.log`，逐条记录退出码）：

| 命令 | 退出码 |
|---|---|
| `python3 -m unittest discover -s tests -t tests` | 0（`Ran 358 tests`、`OK`） |
| `ruff check .` | 0（`No issues found`） |
| `ruff format --check .` | 0（`83 files already formatted`） |

检查在提交前的工作树上运行；提交只加本次文件，内容与检查时相同。

## 边界与未验证

- 临时工作区已在第 15 块删除，`commands.log` 里的绝对路径不再存在；逐字节对应关系见
  `../report.md` 的对应表（`$WORK/lightweight-loop/*` ↔ `examples/lightweight-loop/*`，
  `$WORK/temper/config/AGENTS.md` ↔ `examples/lightweight-loop/config/AGENTS.md`）。
- 本轮不提供、也不能声称的东西：
  - 没有 Retired / 退役记录，**未做退役匹配**，所以不能证明这三条与历史退役记录没有冲突。
  - 期望值仍是“待验证”：没有真实应用、Host 未加载、观察窗内没有会话，W3 的收益结论只能是
    `observe` / `unknown`。本次复跑只说明流程与文本一致，不说明改动有效。
  - 两条叫停不是两个独立事件，也没有第二次越界写入可供计数。
- 未测：真实 `~/.pi`、`~/.codex`、`~/.claude` 写入，`source=user` 批准路径，L2 对照实验，观察窗统计。

## 工件清单

| 文件 | 内容 |
|---|---|
| `build_runner.py` | 从 README 拼出 `runner.sh` 并校验 15 个块逐字一致 |
| `runner.sh` | 本轮实际执行的脚本（15 块 + 证据采集段） |
| `checks_inline.py` | 证据采集段内嵌的核对脚本（24 项断言） |
| `commands.log` | 复跑全过程：每块命令、输出、`exit=` |
| `checks-project.log` | 项目三项检查的命令、版本与退出码 |
| `outputs/blocks-source.txt` | 15 个块在 README / `runner.sh` 中的逐字一致性 |
| `outputs/host-report.sha256`、`outputs/change.sha256` | 本轮报告与变更包 hash |
| `outputs/change.json`、`outputs/approval-rehearsal.json`、`outputs/review-W3.json` | 生成的输入工件 |
| `outputs/ledger.jsonl`、`outputs/status-final.json`、`outputs/review-plan-final.json` | 账本快照与只读状态/复盘计划 |
| `outputs/registry-assets.json`、`outputs/pi-index.json`、`outputs/pi-1.md` | 盘点、摘要产物 |
| `outputs/checks.txt` | 事件计数、三行说明、批准来源、账本、字节、hash 的逐条核对 |
