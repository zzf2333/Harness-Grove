# 检查结果（第二轮：内容纠正）

证据全部来自 `round2-content-correction/`：`commands.log`（复跑全过程）、`outputs/checks.txt`
（证据采集段 24 项断言，全部通过）、`checks-project.log`（项目三项检查退出码）。

## 内容检查（本轮纠正的对象）

| 编号 | 检查 | 结果 | 证据 |
|---|---|---|---|
| K1 | 会话原文未改：仍是 13 行，L9/L12 是用户叫停 | 通过 | `git diff` 不含该 JSONL；`outputs/checks.txt`「带“停”的用户叫停行：['9','12']」 |
| K2 | 相邻文件写入只有一次：L10 `edit README.md` + L11 “已写入 README.md” | 通过 | `outputs/checks.txt`「非 config 的助手写入调用：(行号, 路径) = [('10','README.md')]」 |
| K3 | 报告 W3 不再写“两次因 Agent 顺手改相邻文件被叫停” | 通过 | `outputs/checks.txt`「报告不再写“两次因…被叫停”」；`grep -n 两次因 examples/lightweight-loop/host-report.md` 无输出 |
| K4 | 报告 W3 证据指针含 L10/L11，并写明发生在 L9 叫停之后 | 通过 | `examples/lightweight-loop/host-report.md` W3 行；`outputs/checks.txt` 会话原文段落 |
| K5 | `prediction.why` 与报告同口径（两条叫停、一次可见越界、L7/L8 不算越界） | 通过 | `outputs/change.json`、`outputs/checks.txt`「prediction.why = …」 |
| K6 | W3 `evidence` 含 `:L10`、`:L11` | 通过 | `outputs/change.json`；`outputs/checks.txt`「W3 证据含 L10、L11」 |
| K7 | 报告新增材料边界三条（独立基线 / 未提供 Retired / 两条叫停不是两个独立事件） | 通过 | `host-report.md`「材料边界」段；`outputs/checks.txt` 对应断言 |
| K8 | W1、W2、W3 三行说明与实际 diff、原文指针一致（W1 改第 2 行、W2 删重复第 3 行、W3 改第 4 行） | 通过 | `outputs/checks.txt` 配置 L1–L5 原文 + `outputs/change.json` 三条 `prediction`；`commands.log` block 9 实际字节 |
| K9 | review 记录保持 `observe` / `unknown`，`observations` 含 L10/L11 | 通过 | `outputs/review-W3.json`、`outputs/checks.txt`「review 只有一条、记在 x0002、保持 observe/unknown」「review 的 observations 含 L10/L11」 |
| K10 | 账本把 `prediction` 原文照原样记入，未改写、未升级 | 通过 | `outputs/checks.txt`「入账的三行说明与 change.json 原文一致」 |

## 复跑与字节检查

| 编号 | 检查 | 结果 | 证据 |
|---|---|---|---|
| R1 | README 15 个 bash 块逐字进入 `runner.sh` | 通过 | `outputs/blocks-source.txt`（15 行全部 True） |
| R2 | 15 个块真实复跑，逐块退出码 0 | 通过 | `commands.log`（`block 1`–`block 15` 均 `exit=0`） |
| R3 | 报告 hash 重新生成，沙箱副本、仓库原文、`change.json` 记录三处一致 | 通过 | `outputs/checks.txt`（`53699c1b…51a4`）、`outputs/host-report.sha256` |
| R4 | 变更包 hash 重新生成，批准绑定同一 hash | 通过 | `outputs/checks.txt`（`df31a0b7…31a2`）、`outputs/change.sha256`、`outputs/approval-rehearsal.json` |
| R5 | 批准来源 `sandbox_rehearsal`，只批 W1/W3 | 通过 | `outputs/approval-rehearsal.json`、`outputs/checks.txt` |
| R6 | 三入口实际字节里 W1、W3 生效（第 2 行、第 4 行） | 通过 | `commands.log` block 9（三个入口 `cat` 内容一致） |
| R7 | W2 重复句仍在，未被批准生效 | 通过 | `commands.log` block 9（`grep -c` 输出 `2`） |
| R8 | 账本 `source` 全 `sandbox_rehearsal`，W2 从未入账 | 通过 | `outputs/ledger.jsonl`、`outputs/checks.txt` |
| R9 | 账本没有把批准/应用写成“效果已验证” | 通过 | `outputs/checks.txt`（无“已验证”、无 `verified`/`effect`、非 review 事实无 `prediction_met`） |
| R10 | 逆序恢复 x0002 → x0001，三个入口与共享文件回原字节 | 通过 | `outputs/checks.txt`（`restored_sha256` 链、四个 sha256 相同）；`commands.log` block 14（三条 `cmp` 无输出） |
| R11 | 恢复后三个入口仍是软链 | 通过 | `commands.log` block 14（`ls -l` 三行 `->`）；`outputs/checks.txt` |
| R12 | 临时目录清理成功 | 通过 | `commands.log` 末尾（`block 15 exit=0`、`WORK 已删除：是`） |

## 项目检查

| 命令 | 退出码 | 输出要点 |
|---|---|---|
| `python3 -m unittest discover -s tests -t tests` | 0 | `Ran 358 tests`、`OK` |
| `ruff check .` | 0 | `No issues found` |
| `ruff format --check .` | 0 | `83 files already formatted` |

三条命令都不经管道，退出码逐条写在 `checks-project.log`。

## 未测

- 真实 `~/.pi`、`~/.codex`、`~/.claude` 写入；真实 Host 是否加载改后配置；观察窗内行为统计。
- `source=user` 的真实批准路径；L2 沙盒对照实验与任何效果结论。
- 退役匹配：本轮与上一轮都没有提供 Retired 记录，没有做过匹配，不能声称没有历史冲突。
- 临时工作区已删除，`commands.log` 里的绝对路径不可再访问；按 `../report.md` 的对应表回仓库文件核对。
