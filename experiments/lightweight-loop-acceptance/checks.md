# 检查结果（逐条）

> **后续轮次：当前结论与证据以新目录为准。** 下表是第一轮的 19 条检查，只覆盖“流程能否跑通、字节与账本
> 对不对”，**没有核对 W3 证据里的事件计数**（“两条叫停”被写成“两次越界”）。这条内容缺陷已在第二轮纠正，
> 检查项见 [`round2-content-correction/checks.md`](round2-content-correction/checks.md)；本文件保留为旧轮次
> 记录，不覆盖、不重写。

按 `examples/lightweight-loop/README.md` 在全新临时目录里复跑两次后得到的检查项。第一次运行在第 15 个
bash 块（清理）失败，修正 README 后第二次复跑全部通过；失败现场保留在 `commands-run1.log` 与
`outputs/cleanup-fix.log`。下面除 C13 注明外，证据都来自第二次（修正后）运行。

| 编号 | 检查 | 结果 | 证据 |
|---|---|---|---|
| C1 | 摘要能生成本地 input：`sessions_total=1`、`sessions_included=1`、1 块 | 通过 | `outputs/digest-index.json`、`outputs/pi-1.md` |
| C2 | 摘要列出 4 条用户发言，行号 L2、L6、L9、L12 | 通过 | `outputs/pi-1.md`（`- L2 用户：…` 到 `- L12 用户：…`） |
| C3 | 报告证据指针回读到样例会话原文一致（L2/L6/L9/L12 逐字命中） | 通过 | `outputs/pointer-consistency.txt`、`outputs/evidence-pointer-readback.txt` |
| C4 | 报告证据指针回读到样例配置原文一致（L1–L4 逐字命中） | 通过 | 同上 |
| C5 | 盘点把三个入口聚成一个 3 条 observed 的组，登记出 `asset-0001` | 通过 | `outputs/registry-assets.json`（`observed` 三条 claude/codex/pi） |
| C6 | 应用只批 W1、W3：执行 ID `x0001`、`x0002`，发布 `r0001`、`r0002` | 通过 | `outputs/apply-W1.json`、`outputs/apply-W3.json`、`commands.log` block 7 |
| C7 | 未批准的 W2 没生效：三个入口仍有 2 行“先给结论。”，重复句还在 | 通过 | `commands.log` block 9（`grep -c` 输出 `2`，三份文件内容一致） |
| C8 | W1、W3 在三个入口实际字节里生效 | 通过 | `commands.log` block 9（第 2 行“先给结论，再复述问题。”、第 4 行多“也不要顺手做相邻改动”）；`outputs/status-after-apply.json` 三个 target 的 `sha256` 都是 `a9821367…` |
| C9 | 账本 `source` 都是 `sandbox_rehearsal`，没有一处写成用户批准 | 通过 | `outputs/ledger-check.txt`、`outputs/ledger.jsonl` |
| C10 | 三行说明原文入账（`prediction.why` / `expected` / `verify` / `baseline`） | 通过 | `outputs/ledger-check.txt`（两条 `prediction` 原文）、`outputs/ledger.jsonl` |
| C11 | 账本里没有任何字段把批准/应用写成“效果已验证” | 通过 | `outputs/ledger-check.txt`（无“已验证”、无 `verified`/`effect` 字段、非 review 事实无 `prediction_met`） |
| C12 | review 记录 `observe` / `unknown`，保留未知，且不改执行状态与文件 | 通过 | `outputs/review-plan.json`（review 前 `reviews: []`）、`outputs/review-W3.json`、`outputs/status-final.json`（`latest_review.record.verdict=observe`） |
| C13 | 第一次运行：清理步骤 `rm -rf "$WORK"` 失败，留下半个临时目录 | **失败（已修）** | `commands-run1.log`（`block 15 exit=1`，`rm: …/phe/releases/r0001/manifest.json: Permission denied`）；修正后 `outputs/cleanup-fix.log` 通过 |
| C14 | 修正后清理步骤：`chmod -R u+w` + `rm -rf` 成功，临时目录被删净 | 通过 | `commands.log` 末尾（`block 15 exit=0`、`WORK 已删除：是`） |
| C15 | 逆序恢复：先 `x0002`（回到 `r0001`）再 `x0001`（回到原文件） | 通过 | `outputs/restore-x0002.json`（`release: r0001`）、`outputs/restore-x0001.json`（`release: null`） |
| C16 | 恢复后三个入口与共享文件回到原字节（`cmp` 与 sha256） | 通过 | `commands.log` block 14（三条 `cmp` 无输出；三份 sha256 都是 `33385c10…`）；`outputs/status-final.json`（三 target `release: null`、同 sha256） |
| C17 | 三个入口恢复后仍是软链，指回 `$WORK/temper/config/AGENTS.md` | 通过 | `commands.log` block 14（`ls -l` 三行 `-> …/temper/config/AGENTS.md`） |
| C18 | README 的 15 个 bash 块按顺序可复制执行，修正后退出码全 0 | 通过 | `commands.log`（block 1–15 均 `exit=0`）；`runner-part1.sh`、`runner-part2.sh` 由 README 代码块原样拼出 |
| C19 | 样例目录不含真实内容，且 `config/AGENTS.md` 与 `LIGHT_BASE` 逐字节相同 | 通过 | `outputs/evidence-pointer-readback.txt`（配置 L1–L4）；`config/AGENTS.md` 5 行 |

## 与 README 的偏差

只有一处，已修：

1. **第 0 步的 `pwd -P` 不是可选的。** 实测 macOS 上 `mktemp -d` 给的是 `/var/folders/...`，`registry.py adopt
   --group` 要与盘点里的 resolved path（`/private/var/folders/...`）逐字比对，少了 `pwd -P` 会报
   `盘点里没有该 path 或分组`、退出 1。README 第 0 步已写明这个原因。
2. **第 14 步清理改成 `chmod -R u+w` 再 `rm -rf`。** 发布目录按设计只读，`rm -rf` 删不掉里面的文件，
   第一次运行因此留下半个目录并退出 1；README 已改，第二次运行通过。

## 未测

- 真实 `~/.pi`、`~/.codex`、`~/.claude` 或 temper 的写入（本示例的守卫会拒绝）。
- 真实 Host 是否加载改后的配置、观察窗内是否出现可统计的行为差异（需要真实应用与时间窗）。
- `source=user` 的真实用户批准路径（本示例只有 `sandbox_rehearsal`）。
- L2 沙盒对照实验与任何效果结论。
