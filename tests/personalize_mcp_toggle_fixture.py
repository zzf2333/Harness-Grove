"""R3b 启停切片测试共用的合成夹具：在 MCP 条目夹具之上加一份合成的 `~/.claude.json`。

`~/.claude.json` 里同时放：声明项目键（托管 `demo`、未托管 `other`、信任与审批字段），另一个项目桶（同名的
`demo` 也出现在它的启停列表里，用来证明只动声明项目键），以及 user 作用域的 `mcpServers`。所有文件都是手写
合成内容，不读真实 Host 配置。
"""

import json
import os
import stat

from personalize_fixture import write
from personalize_mcp_fixture import MARKER, McpFixture

# 项目键是临时目录里的合成路径，运行时替换；`@TOGGLE@` 放声明项目桶里已有的启停列表（缺省表示没有这个列表）。
CLAUDE_HOME_TEMPLATE = """{
  "numStartups": 2,
  "mcpServers": {
    "global-demo": {
      "command": "npx",
      "args": ["-y", "demo-mcp"]
    }
  },
  "projects": {
    "@PROJECT@": {
      "allowedTools": [],
      @TOGGLE@"hasTrustDialogAccepted": true,
      "disabledMcpjsonServers": ["approval-unmanaged"],
      "mcpServers": {
        "demo": {
          "command": "npx",
          "args": ["-y", "demo-mcp"],
          "env": {"DEMO_KEY": "${DEMO_KEY}"}
        },
        "other": {
          "command": "other-server",
          "args": ["--other"],
          "env": {"OTHER_TOKEN": "@MARKER@"}
        }
      }
    },
    "@OTHER@": {
      "disabledMcpServers": ["demo", "legacy-disabled"]
    }
  }
}
"""

# 只出现在 Host 配置里的合成串：它们不进预览、计划、journal、发布或账本。
UNMANAGED_STRINGS = (MARKER, "legacy-disabled", "approval-unmanaged", "other-server", "global-demo")


class McpToggleFixture(McpFixture):
    """Pi 用户级条目 + Claude 项目条目 + Claude 项目启停（`~/.claude.json`）的合成夹具。"""

    # 声明项目桶里已有的启停数组（JSON 片段）；None 表示这个列表不存在。
    project_toggle = None

    def setUp(self):
        super().setUp()
        self.claude_home = self.home / ".claude.json"
        self.project_key = str(self.project)
        self.other_key = str(self.tmp / "other-project")
        write(self.claude_home, self.claude_home_text())
        self.claude_home.chmod(0o600)
        self.home_original = self.claude_home.read_bytes()

    def claude_home_text(self) -> str:
        toggle = "" if self.project_toggle is None else f'"disabledMcpServers": {self.project_toggle},\n      '
        return (
            CLAUDE_HOME_TEMPLATE.replace("@PROJECT@", self.project_key)
            .replace("@OTHER@", self.other_key)
            .replace("@MARKER@", MARKER)
            .replace("@TOGGLE@", toggle)
        )

    # 前置与包装：登记三个目标（启停目标带项目键），再准备报告与变更包。

    def adopt_with_toggle(self, project_key: str | None = None, expect: int = 0):
        result = self.cli(
            "mcp-adopt",
            "--phe-home",
            str(self.phe),
            "--server",
            "demo",
            "--target",
            f"pi={self.pi_user}",
            "--target",
            f"claude={self.claude_project}",
            "--target",
            f"claude-toggle={self.claude_home}",
            "--project-key",
            self.project_key if project_key is None else project_key,
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def set_up_project(self, *, toggle: bool = True):
        self.init()
        if toggle:
            result = self.adopt_with_toggle()
        else:
            result = self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}")
        self.asset_id = json.loads(result.stdout)["asset_id"]
        self.change = self.package()
        return result

    def definition_with(self, **fields) -> str:
        data = json.loads(self.definition().read_text(encoding="utf-8"))
        data.update(fields)
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    # 断言辅助：合成 home 的内容、权限位与未托管串。

    def poke_home(self) -> dict:
        return json.loads(self.claude_home.read_text(encoding="utf-8"))

    def home_mode(self) -> int:
        return stat.S_IMODE(os.stat(self.claude_home).st_mode)

    def project_bucket(self) -> dict:
        return self.poke_home()["projects"][self.project_key]

    def unmanaged_strings_in_phe(self) -> list[str]:
        leaked = []
        for path in sorted(self.phe.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            leaked.extend(
                f"{path.relative_to(self.phe)}:{needle}" for needle in UNMANAGED_STRINGS if needle.encode() in data
            )
        return leaked
