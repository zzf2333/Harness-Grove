# 轻量评估闭环示例·首次使用者验收报告

> **后续轮次：当前结论与证据以新目录为准。** 本文件是第一轮复跑记录：证明 15 个 bash 块能跑通、字节与
> 账本对得上。但那一轮没有核对 W3 证据的**事件计数**，漏掉了“把两条叫停写成两次独立越界”的内容缺陷。
> 报告与生成字段已在第二轮纠正，报告 sha256 与 `change_sha256` 因此重新生成。现在的结论与证据指向
> [`round2-content-correction/report.md`](round2-content-correction/report.md) 与
> [`round2-content-correction/checks.md`](round2-content-correction/checks.md)。本文件的旧日志保留、不覆盖；
> 下文出现的 hash、临时目录路径与运行输出都属于上一轮。

验收对象：仓库内新增的 `examples/lightweight-loop/`（README、合成会话、最小配置、宿主报告样例、
`render_inputs.py`）。

方式：只看 `examples/lightweight-loop/README.md`，在全新临时目录里按顺序逐条执行，不参考 `tests/`。
执行的命令由 README 的 bash 代码块机械拼出（`runner-part1.sh`、`runner-part2.sh`），保证与文档逐字一致。

复跑两次：

- 第一次（`commands-run1.log`、`runner-run1-part1.sh`、`runner-run1-part2.sh`）：第 1–14 个块通过，
  第 15 个块（清理）失败，`rm -rf "$WORK"` 删不掉只读发布，退出 1。
- 修正 README 第 14 步后第二次（`commands.log`、`runner-part1.sh`、`runner-part2.sh`）：15 个块全部退出 0。
  本报告与 `checks.md` 的证据默认取自第二次运行。

## 场景记录

| 场景 | 谁做 | 结果 | 说明 |
|---|---|---|---|
| 建临时工作区、复制样例、三个入口软链 | 工具生成（`cp`/`mkdir`/`ln`，写 `$WORK`） | 通过 | `pwd -P` 必需，见「偏差 1」 |
| `history.py digest` 生成摘要 | 工具生成 | 通过 | 1/1 会话、1 块，正文 4 条用户发言（L2/L6/L9/L12） |
| 回读原文、读报告 | 宿主判断 | 通过 | `sed` 打印 L2/L6/L9/L12 与配置 L1–L4；报告里的原文与之一致（`outputs/pointer-consistency.txt`） |
| `inventory.py --home` + `registry.py adopt --group` | 工具生成 | 通过 | 三个入口聚成一组，`asset-0001`，3 条 observed path |
| `render_inputs.py` 生成变更包/批准/review 记录 | 工具生成 | 通过 | `change_sha256=9b7c43fc…`（第二次运行，报告文本已改，本轮新值 `df31a0b7…` 见新目录）；批准只含 W1、W3，`source=sandbox_rehearsal` |
| `change.py apply` W1、W3 | 工具生成 | 通过 | `x0001`/`r0001`、`x0002`/`r0002`，前后 sha256 与 `status` 一致 |
| W2 不批不生效 | 工具生成（`grep -c`） | 通过 | 三个入口仍有 2 行“先给结论。” |
| W1、W3 在三个入口实际生效 | 工具生成（`cat`/`cmp`）+ 人眼核对 | 通过 | 第 2 行改对、第 4 行加了“也不要顺手做相邻改动” |
| 账本：来源、三行说明、不冒充效果结论 | 工具生成 | 通过 | 7 条事实：approval/apply/review/restore；`source` 全 `sandbox_rehearsal`；无“已验证” |
| `review-plan` | 工具生成 | 通过 | 两个执行带原预测、证据、报告 sha256、观察窗 `from`、`to: null` |
| 写 review 记录 | 宿主判断 | 通过（样例预写） | `observe` / `unknown`；理由写明只在临时沙箱应用、Host 未加载、样例会话在应用之前 |
| `change.py review` | 工具生成 | 通过 | 追加一条 `kind=review`，带 `record_sha256`；不改文件与状态 |
| 逆序 `restore` x0002 → x0001 | 工具生成 | 通过 | x0002 回到 `r0001`，x0001 回到 `release: null` |
| 恢复后核对原字节 | 工具生成 | 通过 | 三条 `cmp` 无输出；三份 sha256 `33385c10…`；软链仍指向共享文件 |
| 清理临时目录 | 工具生成 | 第一次失败、修正后通过 | 见「偏差 2」 |
| 真实配置写入、Host 加载、观察窗取样、`source=user` 批准、L2 实验 | — | 未测 | 本示例明确只在临时目录，且不启动 Host |

## 绝对路径与仓库内原文的对应

证据里的绝对路径都指向本次运行的临时工作区（`/private/var/folders/…/T/tmp.VFWWIb6tAG/`），
第 15 步已把它删除。逐字节对应关系：

| 证据里的路径 | 仓库内原文 |
|---|---|
| `$WORK/lightweight-loop/pi-root/sessions/--home-demo-proj--/2026-10-28T09-00-00-000Z_7c1d0c4e-2b5a-4f36-9d2e-6a1b0c3d5e7f.jsonl` | `examples/lightweight-loop/pi-root/sessions/…`（`cp -R` 副本） |
| `$WORK/lightweight-loop/host-report.md` | `examples/lightweight-loop/host-report.md` |
| `$WORK/temper/config/AGENTS.md` | `examples/lightweight-loop/config/AGENTS.md`（`cp` 副本，L1–L4 相同） |
| `$WORK/inputs/*.json` | 由 `examples/lightweight-loop/render_inputs.py` 生成，不含仓库文件 |

所以 `outputs/evidence-pointer-readback.txt`、`outputs/pointer-consistency.txt` 里能对上原文；临时目录删掉后，
按上表回仓库文件可复现同样的核对。

## README 偏差与修正

1. **第 0 步 `pwd -P` 必需。** 首次运行按 README 的写法带了它，所以没出问题；但原因是实测过的：
   `mktemp -d` 给 `/var/folders/…`，而 `registry adopt --group` 要与盘点里的 `/private/var/folders/…`
   逐字比对，少了 `pwd -P` 会报 `盘点里没有该 path 或分组`、退出 1。README 已写明这个原因。
2. **第 14 步清理失败并已修（首次运行的实际阻塞）。** 原文只写 `rm -rf "$WORK"`：

   ```
   rm: …/phe/releases/r0001/manifest.json: Permission denied
   ----- block 15 exit=1
   WORK 已删除：否
   ```

   原因是 PHE 的发布目录按设计只读（`releases/rNNNN` 不可写），`rm -rf` 删不掉目录里的文件，留下半个
   临时目录。README 改为 `chmod -R u+w "$WORK"` 再 `rm -rf "$WORK"`，并在步骤里写清为什么。修正后
   第二次运行 `block 15 exit=0`、`WORK 已删除：是`。

没有其他偏差：摘要计数、`adopt` 输出、执行 ID/发布号、`grep -c` 的 `2`、`cmp` 的静默、恢复后的
sha256 都与 README 写的预期一致。

## 流程结论与边界

- 闭环在临时配置上完整跑通：会话 → 摘要 → 宿主回读 → 报告 → 盘点/登记 → 变更包 → 演练批准 →
  应用 → 账本 → 复盘记录 → 逆序恢复回原字节。
- 这与“改动有效”无关：批准与 `apply` 成功只证明写进了临时配置；W3 的复盘结论是 `observe` / `unknown`；
  样例会话在应用之前，不能当观察证据；`sandbox_rehearsal` 不是用户批准。
- 只有一处正常流程阻塞（清理步骤），已按最小改法修 README；`tools/` 未改动。
- 相邻但不阻塞、本次未动的观察：
  - `history.py digest` 的 `--since` 只有日期粒度下界，README 用固定日期 `2026-10-01` 才不依赖运行时间；
    真实复盘仍要按源记录 `ts` 核对观察窗（Skill §13 已写明）。
  - review 与 apply 可能落在同一秒（本次都是 `15:30:58`），观察窗 `[from, to)` 在恢复后是零宽；这属于
    正常语义，不影响记录，但真实复盘统计时要注意。
  - 清理只读发布需要先 `chmod`，如果以后想把“删临时 PHE_HOME”写进工具，需要在写入层统一处理；本轮只改
    文档。

## 工件清单

| 文件 | 内容 |
|---|---|
| `commands.log` | 第二次运行：15 个 bash 块的命令、输出与退出码 |
| `commands-run1.log` | 第一次运行：第 15 块失败的现场 |
| `runner-part1.sh` / `runner-part2.sh` | 由 README 代码块拼出的执行脚本（命令逐字一致） |
| `outputs/digest-index.json`、`outputs/pi-1.md`、`outputs/pi-index.json` | 摘要产物 |
| `outputs/pointer-consistency.txt`、`outputs/evidence-pointer-readback.txt` | 证据指针回读结果 |
| `outputs/registry-assets.json` | 登记表（三个入口一个资产） |
| `outputs/change.json`、`outputs/approval-rehearsal.json`、`outputs/render-inputs.json` | 生成的输入工件 |
| `outputs/apply-W1.json`、`outputs/apply-W3.json`、`outputs/status-after-apply.json` | 应用输出与状态 |
| `outputs/review-plan.json`、`outputs/review-W3.json` | 复盘计划与 review 记录 |
| `outputs/restore-x0002.json`、`outputs/restore-x0001.json`、`outputs/status-final.json` | 恢复输出与最终状态 |
| `outputs/ledger.jsonl`、`outputs/ledger-check.txt` | 账本快照与逐项核对 |
| `outputs/cleanup-fix.log` | 修正后的清理步骤复跑 |
