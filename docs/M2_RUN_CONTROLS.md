# 真实实验的执行控制：主动暂停与 Target 写隔离

**状态（2026-10-29）：** 本轮只给 `tools/phe.py run` 加两项执行控制，没有启动模型、没有真实 CLI 会话、没有新的实验冻结。`experiments/m2-real-001` 保持冻结字节不变；它的 `proposal.runner.runner_sha256` 记的是改动前的 `tools/phe.py`，所以要用新 runner 执行必须换新实验 ID 重新冻结批准。

上位来源：[M-1_PROTOCOL](M-1_PROTOCOL.md)「预算」「判定」、[M2_HOST_ACCEPTANCE](M2_HOST_ACCEPTANCE.md)「执行层隔离核对」、[SKILL](../skill/harness-evolve/SKILL.md) §5/§8。本文只描述 runner 的实际行为与边界，不改协议里的预算公式和门槛。

## 1. 主动暂停：`--pause-after-new-slots N`

```bash
python3 -I tools/phe.py run <实验目录> --pause-after-new-slots 1   # 完成 1 个新 slot 后停下
python3 -I tools/phe.py run <实验目录>                              # 续跑，行为与之前完全一致
```

- **停在哪里：** 某个 slot 的 `runs.jsonl` 记录、`artifacts/slot-NN/` 下的 stdout/stderr/diff/oracle 输出都落盘之后，下一个 Target 启动之前。暂停检查排在完整性、墙钟、费用、半成品目录这些准入检查之后，所以暂停不会掩盖任何停止条件。
- **不是失败：** 退出码 0，stdout 打印一行 `paused: …`；不写 `stop.json`、不改 `runs.jsonl`、不追加 `host_log.jsonl`、不伪造 `stop_reason`。已有的 `stop.json` 保持原样。
- **续跑：** 仍按冻结的 `schedule` 跳过已完成 slot，不补跑、不追加。失败、超时和崩溃照旧占位。
- **没有新 slot 不暂停：** 所有 slot 都已完成时，带不带这个参数结果相同（正常结束路径，会写 `stop.json`）。
- **不扩预算：** 暂停不延长墙钟和费用额度，见下一节。

保证：暂停点之前的工作不会丢，暂停点之后不会自行继续。不保证：暂停在实验目录里没有标记文件——`stop.json` 不存在、`runs.jsonl` 少于 schedule，和"上次 `run` 中断"在文件上完全一样，区别只在本次调用的退出码和 stdout。续作方式与中断相同（[SKILL](../skill/harness-evolve/SKILL.md) §8 `running` 且无 `stop.json` 的分支）。

## 2. 墙钟累计口径

每次启动前的准入仍是"剩余额度要能容纳最坏一个 slot"：

```text
已记录执行时长 + 本次调用已用时间 + (T_run + 宽限 + 单次验收上限) <= max_experiment_wall_seconds
```

- **已记录执行时长**取 `runs.jsonl` 现有字段之和：`wall_time_v1 + oracle_seconds`。续跑（含暂停后续跑）不再从 0 重新计时，所以暂停不能把已经用掉的时间抹掉。
- **两次调用之间的人工等待不计入**：只有记录里的执行时长进入累计。这是对协议"整个实验墙钟"的收窄解释，用来说明暂停等待不算实验消耗。
- **没有新状态文件**：累计值只在读取时从现有记录投影出来。

不保证：`wall_time_v1` 和 `oracle_seconds` 之外的 runner 自身开销（复制工作区、Git 初始化）没有单独记录，续跑时这部分不会重复计入。首次调用（已完成记录为 0）的口径与改动前完全一致。

## 3. Target 写隔离：`config.json` 的 `sandbox`

```json
{
  "sandbox": {
    "mechanism": "macos-sandbox-exec",
    "executable": "/usr/bin/sandbox-exec",
    "network": "allow",
    "write_roots": ["/tmp/phe-m2-002-tmp"]
  }
}
```

`mechanism` 只支持 macOS 内置 `/usr/bin/sandbox-exec`；`executable` 可省（默认就是它）；`network` 取 `allow`（默认）或 `deny`；`write_roots` 是额外显式允许写入的绝对目录，必须已存在、不能是真实 home 或它的祖先/子目录。

- **执行层包裹：** Target 命令前面加上 `sandbox-exec -f <profile>`，profile 由 `config.json` 和本 slot 的路径生成，存放在 `artifacts/slot-NN/sandbox.sb`，作为该 slot 实际生效规则的原样证据。子进程继承同一 profile（实测：沙箱子进程写未允许目录同样 `Operation not permitted`）。
- **可写范围：** 本 slot 的工作目录、本 slot 的 artifacts 目录、`write_roots`。其他位置一律拒绝写入，包括 `~/.claude`、`~/.claude.json`、`~/.codex`、`~/.pi`、`~/.agents`、temper 和系统临时目录。
- **读与网络：** 读不限制（登录凭据仍可读，便于复用现有登录），网络按 `network` 透传。没有换 HOME、没有 `--bare`、没有新 key。
- **fail closed：** 声明了 `sandbox` 就必须可用。机制不支持、程序不存在、写根不合法时 `run` 直接以退出码 1 拒绝启动，不写 `stop.json`、不启动任何 Target——与"批准未绑定"同一类前置拒绝，不会静默按无隔离方式执行。
- **哨兵：** 每次 `run` 在启动第一个 Target 之前，在 `/tmp` 的独立目录里用同一套 profile 实测四件事：允许目录可写、未允许目录不可写、子进程写允许目录可写、子进程写未允许目录不可写。任一不符合预期就拒绝启动。
- **Hash 绑定：** 整个 `sandbox` 段在 `config.json` 里，`config.json` 的 SHA-256 冻结在 `proposal.runner.config_sha256`。改动隔离参数就是改 config Hash，`run` 会记 `invalid_run:config_hash_changed`，只能换新实验 ID 重冻。
- **必须显式给出的临时目录：** Target 或它的测试工具若需要写系统临时目录（`$TMPDIR`、包管理器缓存），必须把该目录列进 `write_roots`，否则写入被拒、该 slot 按实际失败记录。不做自动放宽。

不保证：外置验收器不在沙箱里（它是可信代码，在工作区副本上跑）；`/dev/null` 和 `/dev/fd/*` 的写入放行（普通重定向，不是状态写入）；读取范围不受限，profile 不是保密措施；这是 macOS 专用机制，其他平台没有实现，声明了就拒绝启动。真实 CLI 在写受限下的完整行为本次未测——如果 CLI 自身的 cache/state 写入被拒，后果是该 slot 失败或报错留在 artifacts 里，必须人工按协议停止，不自动放宽、不补跑。

## 4. 本轮未验证

- 没有启动任何模型、真实 Claude Code/Codex 会话或 `claude -p`；隔离与暂停只在合成夹具和本地桩程序上验证。
- 没有对 `experiments/m2-real-001` 执行 `run`/`judge`，没有写 `approval.json`、`result.json`、`report.md`。
- 真实实验用新 runner 执行前需要：换新实验 ID 重新冻结（含 `sandbox` 段与 `write_roots` 的具体目录），重新取得用户批准。
