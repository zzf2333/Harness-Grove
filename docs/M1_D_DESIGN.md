# M1 切片 D1 设计：沙箱里的导入、发布、部署与回滚

**状态：** D1 已实现（2026-10-28），单测覆盖沙箱流程与四层守卫；尚未经 reviewer 审查，未在真实 temper 副本上演练。只在沙箱实现和验证，不碰真实环境。上位文档：[M1_PLAN](M1_PLAN.md)「原则：开发与使用分离」。

## 范围

- **做：** instructions（AGENTS.md / CLAUDE.md）和 skills 两类资产；部署策略只有 symlink。盘点里有证据的共享资产正是这两类。
- **不做（留给 D2）：** MCP 渲染、hooks、Codex `config.toml`。各平台格式未核实，现在设计只能猜。
- 加载链仍标 `unverified`：沙箱里没有 Agent 真的读这些文件。

## 沙箱

- 临时 HOME：假的 `.claude/`、`.codex/`、`.pi/agent/`，软链指向沙箱里的 temper 副本。
- temper 副本用 `cp -RL` 复制（跟随链接），避免沙箱里的链接指回真实 temper。
- 临时 `PHE_HOME`：PHE 资产源。

## 防止碰到真实环境（四层，全在代码里）

1. `--home` 和 `--phe-home` 必填，没有默认值。
2. 真实 home 用 `pwd.getpwuid(os.getuid()).pw_dir` 取，不跟 `$HOME`。两个参数 realpath 后，等于真实 home、是它的祖先，或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之下，一律在任何写入前退出 2。没有逃生参数。
3. 每次写入（建文件、建/换 symlink、mkdir、chmod、写 journal 和 bindings）都经过 `guarded_write_path()`：被写对象所在目录的 realpath 必须在 `--home` 或 `--phe-home` 之下，否则抛异常。检查看目录、不解析叶子，所以写链接时查的是链接自身所在目录，不是它指向的目标；沙箱根本身允许创建。
4. 单测：对每个子命令传真实 home，断言退出 2、沙箱前后不变、写入函数未被调用。

## 目录与对象

```text
PHE_HOME/
  assets/<asset_id>/asset.json  # {asset_id, kind: instructions|skill, name, source, imported_at}
  assets/<asset_id>/content/... # 资产内容：instructions 是 content/<文件名>，skill 是 content/ 下的整个目录
  releases/high_water.json      # 下一个可用发布编号（高水位，目录名只在文件丢失时兜底）
  releases/rNNNN/               # 不可变：manifest.json（每个文件 sha256 和 asset_id），建完后 chmod -R a-w
  bindings.json                 # {agent: {link_path: release_id}}
  journal/<ts>.json             # 每次 deploy 前记下每条链接的旧状态（path、state、old_target、new_target）
```

release 编号用和 registry 一样的高水位，不复用。deploy 的目标链接取自 registry 里该 agent 的
`observed` 路径：instructions 指向 `releases/rNNNN/files/<asset_id>/<文件名>`，skill 指向
`releases/rNNNN/files/<asset_id>` 整个目录。

## 命令

| 命令 | 行为 |
|---|---|
| `import --registry R --inventory I [--asset-id ...]` | 把选中资产复制到 `assets/<asset_id>/`；来源 realpath 必须在 `--home`、`--phe-home` 或两者的共同祖先（沙箱根）之下；`--inventory` 用来确认资产确实出现在这次 `--home` 的盘点里；已导入的编号拒绝覆盖；资产内容含软链时拒绝 |
| `release` | 由 `assets/` 构建 `releases/rNNNN/` 与 manifest |
| `deploy --agent X --release rNNNN --registry R` | 先对 `--home` 跑 inventory + drift，任何 `ExternalChange` 都中止，一条链接都不动；全部目标检查完（父目录存在、守卫通过、不是普通文件）才写；写前记 journal，再逐条原子替换链接（临时链接 + `os.replace`），最后更新 bindings |
| `rollback --journal J` | 按 journal 倒序恢复旧状态，再撤掉这些链接的 binding |

“记录式事务”就是 journal + rollback，不另设状态机。保证：任一链接替换失败（含 bindings 写入失败）按 journal 恢复已改过的链接；不保证并发 deploy 的互斥。

deploy 不改登记表：部署后同一份登记表会把这些链接报成 `symlink_target_changed`，所以验收里的“再盘点 drift 为 0”要先把新盘点 adopt 成新基线。deploy 只盘点 `--home`（没有 `--project`），登记表里的项目级条目会被判为 `missing` 并中止；该 agent 的 `observed` 路径不在 `--home` 之下的跳过并打印。

## 实现

`tools/release.py` 是 CLI、`import` 和 `release`；写入层、`deploy` 和 `rollback` 在 `tools/release_deploy.py`。两者只用标准库，盘点与漂移检测复用 `tools/inventory.py`、`tools/registry.py` 的函数。

## 验收

- 合成 fixture 单测：import → release → deploy → 再盘点并 adopt 后 drift 为 0 → 改一个文件后 deploy 中止 → rollback 后与 deploy 前逐字节一致（含 `symlink_target`）；四层守卫各有用例。
- 主控手动演练：`cp -RL ~/AI/temper/config` 到临时目录（只读 temper），在临时 HOME 里走完整流程。
- reviewer 重点：写入路径能否逃出沙箱；deploy 是否真的在写之前完成全部检查。
- 尚未验证：加载链（沙箱里没有 Agent 真的读这些文件）；真实 temper 副本上的演练。
