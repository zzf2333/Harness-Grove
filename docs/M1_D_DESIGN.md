# M1 切片 D1 设计：沙箱里的导入、发布、部署与回滚

**状态：** 设计（2026-10-28）。只在沙箱实现和验证，不碰真实环境。上位文档：[M1_PLAN](M1_PLAN.md)「原则：开发与使用分离」。

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
3. 每次写入（建文件、建/换 symlink、mkdir）都经过 `guarded_write_path()`：链接本身所在目录的 realpath 必须在 `--home` 或 `--phe-home` 之下，否则抛异常。
4. 单测：对每个子命令传真实 home，断言退出 2、沙箱前后不变、写入函数未被调用。

## 目录与对象

```text
PHE_HOME/
  assets/<asset_id>/...      # import 从 registry 选中的资产复制内容
  releases/rNNNN/            # 不可变：manifest.json（每个文件 sha256），建完后 chmod -R a-w
  bindings.json              # {agent: {link_path: release_id}}
  journal/<ts>.json          # 每次 deploy 前记下每条链接的旧状态
```

release 编号用和 registry 一样的高水位，不复用。

## 命令

| 命令 | 行为 |
|---|---|
| `import` | 从 registry 选中的资产复制内容到 `assets/` |
| `release` | 由 `assets/` 构建 `releases/rNNNN/` 与 manifest |
| `deploy --agent X --release rNNNN` | 先对沙箱 home 跑 inventory + drift，任何 `ExternalChange` 都中止，一条链接都不动；全部目标检查完才写；写前记 journal，再逐条替换链接，最后更新 bindings |
| `rollback <journal>` | 按 journal 倒序恢复旧状态 |

“记录式事务”就是 journal + rollback，不另设状态机。保证：任一链接替换失败可按 journal 恢复；不保证并发 deploy 的互斥。

## 验收

- 合成 fixture 单测：import → release → deploy → 再盘点 drift 为 0 → 改一个文件后 deploy 中止 → rollback 后与 deploy 前逐字节一致；四层守卫各有用例。
- 主控手动演练：`cp -RL ~/AI/temper/config` 到临时目录（只读 temper），在临时 HOME 里走完整流程。
- reviewer 重点：写入路径能否逃出沙箱；deploy 是否真的在写之前完成全部检查。
