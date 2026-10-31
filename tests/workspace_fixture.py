"""R1 入口测试共用的合成夹具：合成 Host 根、项目根、仓库外 PHE_HOME 与无关 cwd。

全部在临时目录里跑，不读真实 home。测试模块按需从这里导入。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "workspace.py"
INVENTORY = ROOT / "tools" / "inventory.py"
REGISTRY = ROOT / "tools" / "registry.py"
RELEASE = ROOT / "tools" / "release.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_cli(tool: Path, *args: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(tool), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
        env=env,
    )


def snapshot(root: Path) -> dict[str, str]:
    """路径 → 类型与内容指纹；软链只记指向，不跟随。"""
    data = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            data[key] = "link:" + os.readlink(path)
        elif path.is_file():
            data[key] = "file:" + hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            data[key] = "dir"
    return data


def release_tree(path: Path) -> None:
    """发布目录是只读的；清理临时目录前先放开权限，软链不动。"""
    for item in [path, *path.rglob("*")]:
        if not item.is_symlink():
            item.chmod(0o755 if item.is_dir() else 0o644)
    shutil.rmtree(path, ignore_errors=True)


class WorkspaceFixture(unittest.TestCase):
    """合成 Host 根（共享指令软链 + Skill + MCP 配置 + 未托管文件）、项目根、仓外 PHE_HOME。"""

    def setUp(self):
        # 先取真实路径，macOS 的 /var 软链会让路径断言变复杂。
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="workspace-test-")))
        self.addCleanup(release_tree, self.tmp)
        self.shared = self.tmp / "shared" / "AGENTS.md"
        self.host = self.tmp / "host"
        self.project = self.tmp / "project"
        self.phe = self.tmp / "phe"
        self.elsewhere = self.tmp / "elsewhere"
        self._build_fixture()
        self.elsewhere.mkdir()
        self.registry_path = self.phe / "registry" / "assets.json"

    def _build_fixture(self):
        write(self.shared, "共享指令：回答先给结论。\n")
        write(self.host / ".claude/CLAUDE.md", "# Claude 指令\n")
        skill = self.host / ".claude/skills/demo"
        write(skill / "SKILL.md", "---\nname: demo\n---\n演示 skill\n")
        write(skill / "notes.md", "说明\n")
        write(self.host / ".pi/agent/mcp.json", '{"mcpServers": {"demo": {"command": "demo"}}}\n')
        # 未托管、盘点不支持的托管路径：rules 目录里的普通文件。
        write(self.host / ".claude/rules/style.md", "风格规则\n")
        write(self.host / ".claude/agents/reviewer.md", "复核者定义\n")
        write(self.host / ".pi/agent/agents/planner.md", "规划者定义\n")
        for entry in (self.host / ".pi/agent/AGENTS.md", self.host / ".codex/AGENTS.md"):
            entry.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self.shared, entry)
        write(self.project / "AGENTS.md", "项目指令\n")
        write(self.project / ".claude/skills/proj-skill/SKILL.md", "---\nname: proj-skill\n---\n项目 skill\n")

    def init(self, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return run_cli(TOOL, "init", "--phe-home", str(self.phe), *extra, cwd=self.elsewhere, env=env)

    def discover(self, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return run_cli(
            TOOL,
            "discover",
            "--phe-home",
            str(self.phe),
            "--home",
            str(self.host),
            "--project",
            str(self.project),
            *extra,
            cwd=self.elsewhere,
            env=env,
        )

    def report_md(self) -> str:
        return (self.phe / "reports" / "discovery.md").read_text(encoding="utf-8")

    def report_json(self) -> dict:
        return json.loads((self.phe / "reports" / "discovery.json").read_text(encoding="utf-8"))

    def records_by_path(self) -> dict[str, dict]:
        return {record["path"]: record for record in self.report_json()["records"]}
