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
2. 真实 home 用 `pwd.getpwuid(os.getuid()).pw_dir` 取，不跟 `$HOME`。两个参数 realpath 后，等于真实 home、是它的祖先，或落在 `~/.claude`、`~/.codex`、`~/.pi`、`~/.phe`、`~/.agents`、`~/AI/temper` 之下，一律在任何写入前退出 2。没有逃生参数。字符串比较按 casefold，并用文件身份（`st_dev`、`st_ino`）逐级向上再判一遍，所以大小写不敏感的文件系统上 `/Users/…` 和 `/users/…` 这类别名同样被拒绝。
3. 每次写入（建文件、建/换 symlink、mkdir、chmod、写 journal 和 bindings）都经过 `guarded_write_path()`：被写对象所在目录的 realpath 必须在 `--home` 或 `--phe-home` 之下，否则抛异常。检查看目录、不解析叶子，所以写链接时查的是链接自身所在目录，不是它指向的目标；沙箱根本身允许创建。“在根之下”同样按文件身份逐级向上比，避免大小写别名误判。
4. 单测：对每个子命令传真实 home，断言退出 2、沙箱前后不变、写入函数未被调用。

边界：不保证防住检查与写入之间沙箱目录被并发替换的竞态（需要有人在沙箱内主动换目录）；单用户手动运行可以接受。

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
| `release` | 由 `assets/` 构建 `releases/rNNNN/` 与 manifest；来源目录不可用或遍历失败（含子目录读不到）、发布内容含软链时失败，并删掉本次 `rNNNN`，不留可部署的发布；编号照常推进，不复用 |
| `deploy --agent X --release rNNNN --registry R` | 先对 `--home` 跑 inventory + drift，任何 `ExternalChange` 都中止，一条链接都不动；例外是 `symlink_target_changed` 且路径在 `bindings.json` 里（任意 agent）、链接精确指向该绑定 release 对应文件的，视为 PHE 自己的部署，不算漂移（推导目标复用 `link_target()`，与写链接时同一个函数）；放行前按 manifest 校验绑定 release：编号必须是 `rNNNN`、目录链上没有软链、该资产的文件集合与每个文件的 sha256 与 manifest 一致，任一项不符或读不出来就不放行（`tools/release_verify.py`，同一 release+asset 每次预检只校验一次）；全部目标检查完（父目录存在、守卫通过、不是普通文件）才写；写前记 journal，再逐条原子替换链接（临时链接 + `os.replace`），最后更新 bindings |
| `rollback --journal J` | 按 journal 倒序恢复旧状态，再撤掉这些链接的 binding |

“记录式事务”就是 journal + rollback，不另设状态机。保证：任一链接替换失败（含 bindings 写入失败）按 journal 恢复已改过的链接；临时链接名带随机后缀，撞名就换名字重试，不删不是本次创建的对象；恢复时单条被守卫拒绝或 IO 失败不中断其余条目，最终报错同时带上原始的部署失败和恢复失败；不保证并发 deploy 的互斥。

deploy 不改登记表，基线不变。PHE 自己部署的链接由 deploy 预检按 `bindings.json` 识别，所以多个 Agent 共享同一资产时可以逐个部署。保证：已绑定、精确指向绑定 release 对应文件、且该发布按 manifest 校验通过（编号形如 `rNNNN`、目录链上没有软链、文件集合与每个文件的 sha256 一致）的链接不算漂移；路径只在 bindings 里但链接被改指别处、绑定的 release 不存在、release 里没有对应资产、发布内容被改写或被换成软链、编号不是 `rNNNN` 时仍报 `symlink_target_changed` 并中止；`content_changed`、`missing`、`unreadable` 一概不放行。不保证：识别 `bindings.json` 之外的任何 PHE 写入；也不保证发现连同 manifest 一起改得自洽的发布（校验以 manifest 为准）。单独运行 `registry drift` 仍会把这些链接报成 `symlink_target_changed`，所以验收里的“再盘点 drift 为 0”要先把新盘点 adopt 成新基线。deploy 只盘点 `--home`（没有 `--project`），登记表里的项目级条目会被判为 `missing` 并中止；该 agent 的 `observed` 路径不在 `--home` 之下的跳过并打印。

## 实现

`tools/release.py` 是 CLI、`import` 和 `release`；写入层、`deploy` 和 `rollback` 在 `tools/release_deploy.py`，deploy 预检里的绑定发布校验在 `tools/release_verify.py`。三个模块只用标准库，盘点与漂移检测复用 `tools/inventory.py`、`tools/registry.py` 的函数。

## 验收

- 合成 fixture 单测：import → release → deploy → 再盘点并 adopt 后 drift 为 0 → 改一个文件后 deploy 中止 → rollback 后与 deploy 前逐字节一致（含 `symlink_target`）；四层守卫各有用例。
- 多 Agent 共享资产（`tests/test_release_multiagent.py`）：claude → codex → pi 逐个部署都成功；已绑定的链接被改指别处、bindings 指向不存在的 release 时 deploy 中止且链接不变；绑定发布里的文件被改写、资产目录或发布目录或 manifest 被换成软链、资产目录多出文件、bindings 的编号不是 `rNNNN` 时 deploy 中止且链接、bindings、journal 都不变；deploy → rollback 后再部署其他 Agent 仍然成功；单独跑 `registry drift` 仍把 PHE 自己的部署报成 `symlink_target_changed`。
- 主控手动演练：`cp -RL ~/AI/temper/config` 到临时目录（只读 temper），在临时 HOME 里走完整流程。
- reviewer 重点：写入路径能否逃出沙箱；deploy 是否真的在写之前完成全部检查。
- 尚未验证：加载链（沙箱里没有 Agent 真的读这些文件）；真实 temper 副本上的演练。
