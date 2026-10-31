"""发现报告的逻辑类型表：中文名、PHE 当前能力、限制（标准库，纯数据）。

从 `workspace.py` 拆出，让入口文件留在 20000 字节上限内；内容与顺序只在这里维护。
"""

DIRECTORY_NOTE = "只记一层条目；导入会按物理形状分类，逻辑类型与启用状态未支持。"
NO_IMPORT_NOTE = "只记一层条目；没有导入、发布或部署路径。"

# 逻辑类型 → 中文名、PHE 当前能力、限制。顺序就是报告里的顺序。
# hook 在盘点里没有候选路径（hooks 写在 settings.json 里），列在这里是为了让覆盖摘要不漏这一类。
KIND_ROWS = (
    (
        "instructions",
        "共享指令",
        "sandbox_managed",
        (
            "沙箱可用 import → release → deploy 与 change.py apply/restore；只覆盖单文件指令，"
            "Host 是否加载未验证；子代理定义（`.claude/agents`、`.pi/agent/agents`）的逻辑类型未支持，"
            "条目单独标成仅观察。"
        ),
    ),
    (
        "skill",
        "Skills",
        "sandbox_managed",
        (
            "沙箱与产品入口可用 import/release/deploy 与预览→批准→apply/review/restore；目录内容（含引用文件）整棵重建，"
            "只覆盖软链入口，普通目录入口不迁移、不覆盖；Pi 的发现与显式展开已在一次性隔离 Host 里验证，"
            "模型自主选用与任务执行未验证。"
        ),
    ),
    (
        "mcp",
        "MCP",
        "product_partial",
        (
            "只支持更新已存在的 server 条目：Pi 的用户级 `~/.pi/agent/mcp.json` 与项目级 `.pi/mcp.json`、"
            "Claude Code 项目级 `.mcp.json`；只替换 `mcpServers` 下托管成员的值跨度，未托管条目与外围字节不变；"
            "启停只改一层（Pi 条目 `enabled`、Claude 项目 `disabledMcpServers`）；"
            "新增 / 删除条目、Codex TOML、连接与 Host 加载未支持；盘点只记 Hash 与顶层键名，读不到 server 条目。"
        ),
    ),
    (
        "hook",
        "Hooks",
        "unsupported",
        "盘点没有独立候选路径（hooks 写在 settings.json 里），报告不列 hook 条目，也不猜路径。",
    ),
    ("extension", "扩展", "observe_only", DIRECTORY_NOTE),
    ("prompt", "prompts", "observe_only", DIRECTORY_NOTE),
    ("plugin", "插件", "observe_only", DIRECTORY_NOTE),
    ("settings", "工具配置 / 权限 / 模型与版本", "observe_only", "只记 Hash 与顶层键名，不记值；改值与还原未支持。"),
    ("rules", "规则目录", "observe_only", NO_IMPORT_NOTE),
    ("memory", "记忆目录", "observe_only", NO_IMPORT_NOTE),
)
