# R3b 第一片的 MCP 恢复计划夹具（合成）

`restore-x0001.json` 由 **base `5a7ba99`** 的 `tools/personalize.py` 真实生成：`mcp-adopt`（合成 Pi 用户级
`mcp.json` 与 Claude 项目级 `.mcp.json`）→ `preview` → `apply`（只改 `args`）→ `restore-preview`。

那次运行在临时目录里，输入是两个合成配置文件（合成标记串、未托管条目）、合成报告和一条 P1 patch；不来自用户
会话、真实配置或本机路径。为了让夹具能复现在任意临时目录，生成后只做了两处替换：

- 生成时的工作目录 → `@ROOT@`；
- 恢复计划里的 journal 文件名 → `@JOURNAL@`（journal 名带生成时间，无法预先固定）。

计划的**字段集合保持 `5a7ba99` 原样**：`entries[]` 只有 `current_*` / `restore_*` sha256、`before_fragment` 与
`diff`，没有后加的 `span`。`tests/test_personalize_mcp_legacy.py` 按当前临时目录还原 `@ROOT@`，按账本里的实际
journal 还原 `@JOURNAL@`，再按替换后的字节重新写一份合成恢复批准（路径变了，计划字节就变了，旧批准对不上；
这和 R2 旧计划夹具同一个口径）。字段、结构与生成顺序都来自旧版输出，不是从新版计划删字段模拟。

重新生成的方法：把仓库切到 `5a7ba99`，按上述顺序跑一遍 `mcp-adopt → preview → apply → restore-preview`，按同样的
两处替换保存。夹具不依赖运行时的 git 历史。
