# 恢复预览 · 周期 c2

- 生成时间：2026-10-31T05:23:10+00:00
- 执行：`x0001`（提案 `P1`，发布 `r0001`）
- 恢复计划 sha256：`3af325e35459d6ec3fa6acec6f2a4bed449525025f8ef513c5862dc51672c8a4`（恢复批准绑定这份计划的原始字节）
- journal：`journal/20261031T052309Z.json`、`journal/20261031T052309Z-1.json`

| Agent | 入口 | 当前形态 | 当前文件 | 当前 sha256 | 恢复后 | 恢复后文件 | 恢复后内容 sha256 |
|---|---|---|---|---|---|---|---|
| shared | `/private/tmp/phe-r3a-SEMx9r/home/.agents/skills/r3a-demo` | 软链 → `/private/tmp/phe-r3a-SEMx9r/phe/releases/r0001/files/asset-0001` | 3 个 | `1ccb9472a9b079e3d2ea8e54fed65d6452075f7a18ec1a4ede0b5dccef5078f2` | 指向 `/private/tmp/phe-r3a-SEMx9r/skill/demo` | 3 个 | `9b7d5b4a6ef218500a46e24cac5452694bc589ed3c8197d5fc71f7565eb1e985` |
| pi | `/private/tmp/phe-r3a-SEMx9r/home/.pi/agent/skills/r3a-demo` | 软链 → `/private/tmp/phe-r3a-SEMx9r/phe/releases/r0001/files/asset-0001` | 3 个 | `1ccb9472a9b079e3d2ea8e54fed65d6452075f7a18ec1a4ede0b5dccef5078f2` | 指向 `/private/tmp/phe-r3a-SEMx9r/skill/demo` | 3 个 | `9b7d5b4a6ef218500a46e24cac5452694bc589ed3c8197d5fc71f7565eb1e985` |

## 写入范围

Host 入口（只这些）：

- `/private/tmp/phe-r3a-SEMx9r/home/.agents/skills/r3a-demo`
- `/private/tmp/phe-r3a-SEMx9r/home/.pi/agent/skills/r3a-demo`

PHE_HOME 产物：`bindings.json`、`ledger.jsonl`（相对 `<PHE_HOME>`）

说明：恢复按 journal 逆序把入口链接指回应用前的目标；恢复后要核对文件集合与每个文件的字节；应用前的共享来源文件本身从未被改过。恢复只追加账本事实，不表示提案无效或 retired。

