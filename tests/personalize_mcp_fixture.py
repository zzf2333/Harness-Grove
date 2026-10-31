"""R3b 产品入口（MCP 条目）测试共用的合成夹具：两个合成 Host 配置、仓外 PHE_HOME、无关 cwd。

托管 `demo` 与未托管 `unmanaged` 同文件共存；未托管条目的值里有合成标记串，用来证明它不进预览、计划、
journal、发布或账本。所有文件都是手写合成内容，不读真实 Host 配置。
"""

import difflib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from personalize_fixture import ROOT, run_cli, sha256, write
from test_release import rmtree_force

# 按文件路径载入 tools/mcp_config.py：不把 tools/ 放进 sys.path，也不在源码树里写 __pycache__。
sys.dont_write_bytecode = True
_SPEC = importlib.util.spec_from_file_location("mcp_config", ROOT / "tools/mcp_config.py")
assert _SPEC is not None and _SPEC.loader is not None
mcp_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mcp_config)

MARKER = "SYNTHETIC-UNMANAGED-MARKER-7f3a"

# 4 空格缩进、`mcpServers` 前后都有外围键：托管跨度之外的字节必须原样保留。
PI_USER = (
    "{\n"
    '    "autoEnableCodemode": false,\n'
    '    "mcpServers": {\n'
    '        "demo": {\n'
    '            "command": "npx",\n'
    '            "args": ["-y", "demo-mcp"],\n'
    '            "env": {"DEMO_KEY": "${DEMO_KEY}"}\n'
    "        },\n"
    '        "unmanaged": {\n'
    '            "command": "legacy-server",\n'
    '            "env": {"LEGACY_TOKEN": "' + MARKER + '"}\n'
    "        }\n"
    "    },\n"
    '    "theme": "synthetic"\n'
    "}\n"
)

# 2 空格缩进、没有外围键：与上面不同的排版，验证渲染按文件自己的缩进步长对齐。
CLAUDE_PROJECT = (
    "{\n"
    '  "mcpServers": {\n'
    '    "demo": {\n'
    '      "command": "npx",\n'
    '      "args": ["-y", "demo-mcp"],\n'
    '      "env": {"DEMO_KEY": "${DEMO_KEY}"}\n'
    "    },\n"
    '    "unmanaged": {\n'
    '      "command": "legacy-server",\n'
    '      "env": {"LEGACY_TOKEN": "' + MARKER + '"}\n'
    "    }\n"
    "  }\n"
    "}\n"
)

# Pi 目标里 `demo` 显式禁用：启停不是本片管理的字段，普通更新要保留这个目标本地状态。
PI_USER_DISABLED = (
    "{\n"
    '    "autoEnableCodemode": false,\n'
    '    "mcpServers": {\n'
    '        "demo": {\n'
    '            "command": "npx",\n'
    '            "args": ["-y", "demo-mcp"],\n'
    '            "env": {"DEMO_KEY": "${DEMO_KEY}"},\n'
    '            "enabled": false\n'
    "        },\n"
    '        "unmanaged": {\n'
    '            "command": "legacy-server",\n'
    '            "env": {"LEGACY_TOKEN": "' + MARKER + '"}\n'
    "        }\n"
    "    },\n"
    '    "theme": "synthetic"\n'
    "}\n"
)

# Claude 官方示例会显式写 `"type": "stdio"`：登记、更新与恢复都要承认这个值。
CLAUDE_PROJECT_STDIO_TYPE = (
    "{\n"
    '  "mcpServers": {\n'
    '    "demo": {\n'
    '      "type": "stdio",\n'
    '      "command": "npx",\n'
    '      "args": ["-y", "demo-mcp"],\n'
    '      "env": {"DEMO_KEY": "${DEMO_KEY}"}\n'
    "    },\n"
    '    "unmanaged": {\n'
    '      "command": "legacy-server",\n'
    '      "env": {"LEGACY_TOKEN": "' + MARKER + '"}\n'
    "    }\n"
    "  }\n"
    "}\n"
)

REPORT_TEXT = "# 合成报告\n\n本报告是测试夹具里宿主写的示例，不是自动诊断。\n"


class McpFixture(unittest.TestCase):
    """合成 Pi 用户级 mcp.json 与 Claude 项目级 .mcp.json，加上仓外 PHE_HOME。"""

    pi_text = PI_USER
    claude_text = CLAUDE_PROJECT

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="personalize-mcp-test-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.elsewhere = self.tmp / "elsewhere"
        self.out = self.tmp / "out"
        self.phe = self.tmp / "phe"
        self.elsewhere.mkdir()
        self.source = self.tmp / "src"
        shutil.copytree(ROOT / "tools", self.source / "tools")
        self.tool = self.source / "tools/personalize.py"
        self.pi_user = self.home / ".pi/agent/mcp.json"
        self.claude_project = self.project / ".mcp.json"
        write(self.pi_user, self.pi_text)
        write(self.claude_project, self.claude_text)
        self.pi_user.chmod(0o644)
        self.claude_project.chmod(0o600)
        self.original = {path: path.read_bytes() for path in (self.pi_user, self.claude_project)}
        self.report = self.out / "report.md"
        write(self.report, REPORT_TEXT)

    # 前置：初始化工作区、登记已存在的条目、准备报告与变更包。

    def cli(self, *args: str, tool: Path | None = None):
        return run_cli(tool or self.tool, *args, cwd=self.elsewhere)

    def init(self) -> None:
        result = self.cli("init", "--phe-home", str(self.phe), tool=self.source / "tools/workspace.py")
        self.assertEqual(result.returncode, 0, result.stderr)

    def adopt(self, *targets: str, expect: int = 0):
        result = self.cli(
            "mcp-adopt",
            "--phe-home",
            str(self.phe),
            "--server",
            "demo",
            *[item for target in targets for item in ("--target", target)],
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def prepare(self) -> None:
        self.init()
        result = self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}")
        self.asset_id = json.loads(result.stdout)["asset_id"]
        self.change = self.package()

    def definition(self) -> Path:
        return self.phe / "assets" / self.asset_id / "content/server.json"

    def definition_with_args(self, *extra: str) -> str:
        data = json.loads(self.definition().read_text(encoding="utf-8"))
        data["args"] = ["-y", "demo-mcp", *extra]
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def patched_definition(self) -> str:
        return self.definition_with_args("--verbose")

    def item(self, item_id: str, after: str) -> dict:
        before = self.definition().read_text(encoding="utf-8")
        patch = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/server.json",
                tofile="b/server.json",
            )
        )
        return {
            "item_id": item_id,
            "kind": "user_preference",
            "evidence": ["合成证据：托管条目要能加参数"],
            "prediction": {
                "why": "合成场景里 demo 需要 --verbose",
                "expected": f"{item_id} 生效；未托管的 legacy-server 条目字节不变",
                "verify": "读两个配置文件；Host 是否加载、能否连上都记 unknown",
            },
            "target": {"asset_id": self.asset_id},
            "patch": patch,
        }

    def package(self, items: list[dict] | None = None) -> Path:
        data = {
            "schema_version": 1,
            "report": {"path": str(self.report), "sha256": sha256(self.report.read_bytes())},
            "items": items or [self.item("P1", self.patched_definition())],
        }
        path = self.out / "change.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve(self, plan: Path, items: list[str], source: str = "user") -> Path:
        data = {
            "schema_version": 1,
            "approval_id": "approval-test",
            "source": source,
            "plan_sha256": sha256(plan.read_bytes()),
            "items": items,
            "note": "测试夹具：合成批准，不代表真实用户同意",
        }
        path = self.out / "approval.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve_restore(self, plan: Path, execution: str) -> Path:
        data = {
            "schema_version": 1,
            "approval_id": f"approval-restore-{execution}",
            "source": "user",
            "execution": execution,
            "plan_sha256": sha256(plan.read_bytes()),
            "note": "测试夹具：合成恢复批准，不代表真实用户同意",
        }
        path = self.out / "approval-restore.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def review(self, execution: str) -> Path:
        record = {
            "verdict": "observe",
            "prediction_met": "unknown",
            "reason": "合成复盘：只看两个文件的托管跨度变了；Host 是否加载未验证",
            "observations": ["托管条目已按定义渲染", "未托管条目字节不变"],
            "next": "按 prediction.verify 核对；没有行为证据前记 unknown",
        }
        path = self.out / "review.json"
        write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        result = self.cli("review", "--phe-home", str(self.phe), "--execution", execution, "--record", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        return path

    # CLI 包装：全部从无关 cwd 调用仓库副本里的产品入口。

    def preview(self, cycle: str = "c1"):
        return self.cli("preview", "--phe-home", str(self.phe), "--change", str(self.change), "--cycle", cycle)

    def plan(self, cycle: str = "c1") -> Path:
        return self.phe / "reports" / cycle / "plan.json"

    def apply(self, item: str = "P1", approval: Path | None = None, cycle: str = "c1"):
        return self.cli(
            "apply",
            "--phe-home",
            str(self.phe),
            "--plan",
            str(self.plan(cycle)),
            "--item",
            item,
            "--approval",
            str(approval or self.approve(self.plan(cycle), [item])),
        )

    def restore_plan(self, execution: str, cycle: str) -> Path:
        result = self.cli("restore-preview", "--phe-home", str(self.phe), "--execution", execution, "--cycle", cycle)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.phe / f"reports/{cycle}/restore-{execution}.json"

    def restore(self, execution: str, plan: Path, approval: Path):
        return self.cli(
            "restore",
            "--phe-home",
            str(self.phe),
            "--execution",
            execution,
            "--plan",
            str(plan),
            "--approval",
            str(approval),
        )

    def drift(self) -> dict:
        result = self.cli("mcp-drift", "--phe-home", str(self.phe))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def ledger(self) -> list[dict]:
        path = self.phe / "ledger.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def poke_files(self) -> dict:
        return {name: path.read_bytes() for name, path in (("pi", self.pi_user), ("claude", self.claude_project))}
