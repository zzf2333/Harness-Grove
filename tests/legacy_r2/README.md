# R2 旧版计划夹具（合成）

这里的 JSON 由 **base `5000e23`** 的 `tools/personalize.py` 真实生成：

- `preview` 得到 `plan.json`；
- 用 `plan_sha256` 绑定计划的 `approval.json`；
- 应用 P1 后用 `restore-preview` 得到 `restore-x0001.json`；
- 用 `plan_sha256` 绑定恢复计划的 `restore-approval.json`；
- 生成用的变更包是 `change.json`。

那次运行在临时目录里，输入是合成指令（四行文本）、合成报告和一条 P1 patch；不来自用户会话、真实配置或本机路径。

为了让夹具能复现在任意临时目录，生成后只做了两处替换：

- 生成时的工作目录 → `@ROOT@`；
- `restore-x0001.json` 里的 journal 文件名 → `@JOURNAL@`（journal 名带生成时间，无法预先固定）。

计划与批准的**字段集合保持旧版原样**：没有 `expected_files`、`current_files`、`asset.kind`、`restore.files`。
`tests/test_personalize_legacy.py` 按当前临时目录还原 `@ROOT@`，按实际 journal 还原 `@JOURNAL@`，再按替换后的
字节重新计算 `plan.change.sha256` / `approval.plan_sha256`（计划/批准绑定的是原始字节）。字段、结构与生成顺序都
来自旧版输出，不是从新版计划删字段模拟。

重新生成的方法：把仓库切到 `5000e23`，按上述顺序跑一遍 `preview → apply → restore-preview`，按同样的两处替换
保存。夹具不依赖运行时的 git 历史。
