# Harness Grove · PHE 开发基线 v0.4

这是一份规格与预注册模板交付包，不是已实现的框架，也未执行真实 Agent 实验。

## 放入仓库

将 `docs/PHE_SPEC.md` 放入 Harness Grove 仓库同路径。用 `docs/Personal Harness Evolution.md` 中的弃用指针替代旧版活动文档；先确认没有尚需保留的本地修改。更新 README 和开发任务中的旧链接。

`examples/m-1/proposal.template.json` 是 draft。数字门槛、5 组各 5 次及预算已经设定；填入实际仓库、Agent 版本、模型 ID、文件 Hash 后，经用户批准才可形成 `experiments/spike-001/proposal.json` 并冻结。

M-1 的规范、预算语义与判定公式以 `docs/PHE_SPEC.md` §5–7 为准。所有运行产物在 Harness Grove 自己的 `experiments/` 中；不写入被测源仓库。

M-1 固定 Claude Code 为 Target，但任何有必要工具权限的现有 Agent 都能作为 Host。v0.4 不扩大长期产品的平台限制。
