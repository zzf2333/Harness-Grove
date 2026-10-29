# 真实实验的执行控制：主动暂停与 Target 写隔离

**状态（2026-10-29）：** 本轮只给 `tools/phe.py run` 加两项执行控制，没有启动模型、没有真实 CLI 会话、没有新的实验冻结。第 2 轮审查后收窄了执行层隔离：沙箱程序固定、写目录只由 runner 创建、Git 采集与 oracle 同策略包裹、runner 模块 Hash 纳入冻结、完整性判定先于 sandbox 解析。第 3 轮审查后补两处执行层缺陷：oracle 准备阶段按真实路径核对目的文件、沙箱模块改从源码字节加载；oracle 越界写回归改用文件目标。`experiments/m2-real-001` 保持冻结字节不变；它的 `proposal.runner.runner_sha256` 记的是改动前的 `tools/phe.py`，所以要用新 runner 执行必须换新实验 ID 重新冻结批准。

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

## 3. 执行层写隔离：`config.json` 的 `sandbox`

```json
{
  "sandbox": {
    "mechanism": "macos-sandbox-exec",
    "network": "allow"
  }
}
```

`mechanism` 只支持 macOS 内置 `/usr/bin/sandbox-exec`，程序路径固定；`network` 取 `allow`（默认）或 `deny`。`sandbox` 段只接受这两个键：出现 `executable`、`write_roots` 或任何其他键时 `run` 明确拒绝启动，不静默忽略。

- **包裹范围：** 同一机制覆盖三类进程：Target 及其子进程；外置验收器（它可能执行 Target 改过的项目代码和测试，不能因为脚本可信就在外层运行）；Target 返回后的 Git 采集（`diff`、`status`）。Git 采集另外禁用 external diff、textconv、fsmonitor，并使用只含固定项的干净环境（不继承 `GIT_*`、不读用户/系统 Git 配置）。
- **可写范围（由 runner 创建，不能配置）：** 每个 slot 的 `repo/` 工作目录、`tmp/` 专用临时目录（Target 的 `TMPDIR` 指向它）、`output/` 输出子目录（默认 `--debug-file` 落在这里，视为不可信输出）。policy 文件、stdout/stderr、diff、oracle 输出和启动记录都写在 `artifacts/slot-NN/`，Target 不可写——仓库级 Git 配置和验收结果都不能被 Target 伪造。整个 `/tmp`、artifacts、实验目录和用户目录都不在允许清单里。
- **哨兵：** 每个 slot 启动 Target 之前，用本 slot 实际写下的 `artifacts/slot-NN/policy-target.sb` 和完整允许清单（三个目录）实测：三个允许目录可写、同一策略下子进程也只能写这些目录、`/tmp` 下自建的未允许目录不可写（子进程同规则）。哨兵只写这些 runner 自有临时目录；任一不符合预期就拒绝启动。
- **可信测试覆盖：** 验收前把 `oracle_tests` 的可信测试覆盖到验收副本（`cp -Rc` 会保留 Target 留下的 symlink）。目的路径先按真实路径核对，必须落在副本根内；外链文件、外链祖先目录、绝对路径和 `..` 逃逸一律拒绝，不跟随覆盖副本外的文件。拒绝时把原因写进 `artifacts/slot-NN/oracle-prep-error.txt`，该 slot 记 `pivot:runner_crashed_no_record`（与 runner 异常的既有处理一致），不静默中断。
- **启动记录：** `argv.json` 记包装前的 `argv`、实际执行的 `wrapped_argv`、policy 路径与 SHA-256；`runs.jsonl` 记 `wrapped_argv_sha256`、`policy_sha256`、`git_policy_sha256`、`oracle_wrapped_argv_sha256`、`oracle_policy_sha256`。stdout/stderr 用管道由 runner 收集，Target 不持有证据文件的 fd。
- **fail closed：** 声明了 `sandbox` 就必须可用。机制不支持、`/usr/bin/sandbox-exec` 不存在、出现不支持的键或哨兵不通过时，`run` 以退出码 1 拒绝启动，不写 `stop.json`、不启动任何 Target。
- **Hash 绑定：** `sandbox` 段在 `config.json` 里，SHA-256 冻结在 `proposal.runner.config_sha256`。新 sandbox 实验还必须把实际加载的 `tools/phe.py` 和 `tools/phe_sandbox.py` 的 SHA-256 冻结到 `proposal.runner.runner_files_sha256`（键为两个文件名，用 `phe.runner_file_hashes()` 生成：`python3 -c 'import sys; sys.path.insert(0, "tools"); import phe, json; print(json.dumps(phe.runner_file_hashes()))'`）；`run` 校验当前加载的模块副本，缺失或不一致记 `invalid_run:sandbox_runner_hash_missing` / `invalid_run:sandbox_runner_hash_changed`，在哨兵和 Target 之前停止。其中 `phe_sandbox.py` 的 Hash 取自已执行的源码字节：普通 `import` 可能执行 `__pycache__` 里 timestamp/size 匹配但内容不同的 pyc，此时 `.py` 的 Hash 不代表实际跑的代码；`phe.py` 以脚本方式执行（`python3 tools/phe.py`），Hash 取当前文件字节。没有 `sandbox` 的旧实验不要求这个字段。
- **完整性优先：** 续跑和每次启动 slot 前先核对 proposal/config/harness/oracle/runner 模块 Hash；配置被改写时先记 `invalid_run:config_hash_changed`，不会因为损坏的 `sandbox` 段提前抛错而漏掉 `stop.json`。

不保证：外置验收器和 Git 采集同样受写限制，但它们仍能读实验目录和用户文件，profile 不是保密措施；`/dev/null` 和 `/dev/fd/*` 的写入放行（普通重定向，不是状态写入）；读取范围不受限，网络按 `network` 透传；这是 macOS 专用机制，其他平台声明了就拒绝启动。验收器和 Git 的依赖缓存默认在允许清单外：真实测试套件或工具若需要写 HOME/系统缓存会失败，失败留在 artifacts 或 stderr 里，必须人工按协议处理，不自动放宽。真实 CLI 在写受限下的完整行为本次未测；Git helper 的禁用只在本机 Git 2.56 上实测。`phe.py` 的 Hash 按文件字节算：以 `import phe` 方式调用（而不是按脚本执行）时，本项不保证 pyc 与源码一致。

## 4. 本轮未验证

- 没有启动任何模型、真实 Claude Code/Codex 会话或 `claude -p`；隔离与暂停只在合成夹具和本地桩程序上验证。
- oracle 目的路径核对用本地 symlink 夹具实测（外链文件、外链祖先、绝对路径、`..`）；真实 Target 会不会构造这类外链未测。恶意 pyc 用临时副本实测（mtime/size 匹配、内容不同），未覆盖其他缓存失效或绕过路径。
- 没有对 `experiments/m2-real-001` 执行 `run`/`judge`，没有写 `approval.json`、`result.json`、`report.md`。
- 真实实验用新 runner 执行前需要：换新实验 ID 重新冻结（含 `sandbox` 段、`runner_files_sha256` 和具体 slot 目录），重新取得用户批准。
