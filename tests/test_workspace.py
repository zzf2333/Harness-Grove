"""R1 入口 tools/workspace.py 的契约验收：初始化、幂等、只读发现报告、登记状态与稳定输出。

全部在临时目录里跑：合成 Host 树 + 仓库外的 PHE_HOME + 无关 cwd，不读真实 home。
夹具在 workspace_fixture.py。
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from workspace_fixture import INVENTORY, REGISTRY, ROOT, TOOL, WorkspaceFixture, run_cli, snapshot, write


class InitTests(WorkspaceFixture):
    def test_init_creates_skeleton_and_format_marker(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(os.listdir(self.phe)), ["assets", "releases", "workspace.json"])
        self.assertEqual(json.loads((self.phe / "workspace.json").read_text(encoding="utf-8")), {"format_version": 1})
        # 登记表、账本与绑定按实际操作创建，init 不预建。
        self.assertFalse(self.registry_path.exists())

    def test_repeat_init_keeps_existing_data(self):
        self.assertEqual(self.init().returncode, 0)
        write(self.phe / "assets" / "asset-0001" / "asset.json", '{"asset_id": "asset-0001"}\n')
        write(self.phe / "reports" / "discovery.md", "上次的报告\n")
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("已初始化", result.stdout)
        self.assertTrue((self.phe / "assets" / "asset-0001" / "asset.json").is_file())
        self.assertEqual((self.phe / "reports" / "discovery.md").read_text(encoding="utf-8"), "上次的报告\n")
        self.assertEqual(json.loads((self.phe / "workspace.json").read_text(encoding="utf-8")), {"format_version": 1})

    def test_unknown_format_version_is_refused_without_writing(self):
        write(self.phe / "workspace.json", '{"format_version": 99}\n')
        before = snapshot(self.phe)
        result = self.init()
        self.assertEqual(result.returncode, 1)
        self.assertIn("99", result.stderr)
        self.assertEqual(snapshot(self.phe), before)

    def test_phe_home_from_environment_and_command_line_wins(self):
        env = {**os.environ, "PHE_HOME": str(self.tmp / "from-env")}
        result = run_cli(TOOL, "init", cwd=self.elsewhere, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.tmp / "from-env" / "workspace.json").is_file())
        result = run_cli(TOOL, "init", "--phe-home", str(self.phe), cwd=self.elsewhere, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.phe / "workspace.json").is_file())
        self.assertFalse((self.tmp / "from-env" / "reports").exists())

    def test_missing_phe_home_is_an_error(self):
        env = {key: value for key, value in os.environ.items() if key != "PHE_HOME"}
        result = run_cli(TOOL, "init", cwd=self.elsewhere, env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--phe-home", result.stderr)


class DiscoverTests(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init().returncode, 0)
        self.inputs_before = {name: snapshot(self.tmp / name) for name in ("host", "project", "shared")}
        self.tree_before = snapshot(self.tmp)
        self.result = self.discover()
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.tree_after = snapshot(self.tmp)
        self.data = self.report_json()
        self.md = self.report_md()

    def test_outputs_only_under_phe_home(self):
        added = set(self.tree_after) - set(self.tree_before)
        self.assertEqual(added, {"phe/reports", "phe/reports/discovery.md", "phe/reports/discovery.json"})
        self.assertEqual({key: self.tree_after[key] for key in self.tree_before}, self.tree_before)
        self.assertIn(str(self.phe / "reports" / "discovery.md"), self.result.stdout)
        self.assertIn(str(self.phe / "reports" / "discovery.json"), self.result.stdout)

    def test_inputs_unchanged(self):
        for name, before in self.inputs_before.items():
            self.assertEqual(snapshot(self.tmp / name), before, name)

    def test_records_cover_kinds_with_management_and_registration(self):
        records = self.data["records"]
        self.assertEqual({record["kind"] for record in records}, {"instructions", "skill", "mcp", "rules"})
        self.assertTrue(all(record["load_chain"] == "unverified" for record in records))
        self.assertTrue(all(record["registry_status"] == "unregistered" for record in records))
        self.assertTrue(all(record["registry_asset_id"] is None for record in records))
        self.assertEqual(self.data["registry"], {"path": str(self.registry_path), "present": False})
        mcp = [record for record in records if record["kind"] == "mcp"]
        self.assertEqual(len(mcp), 1)
        self.assertEqual(mcp[0]["top_level_keys"], ["servers"])
        self.assertIn("读不到 server 条目", mcp[0]["management_note"])

    def test_management_comes_from_the_entry_not_the_kind_alone(self):
        records = self.records_by_path()
        expected = {
            self.host / ".claude/CLAUDE.md": "sandbox_managed",
            self.host / ".claude/skills/demo": "sandbox_partial",
            self.host / ".pi/agent/mcp.json": "observe_only",
            self.host / ".claude/rules/style.md": "observe_only",
        }
        self.assertEqual({path: records[str(path)]["management"] for path in expected}, expected)

    def test_shared_instructions_keep_source_and_entries(self):
        shared = [record for record in self.data["records"] if record["resolved_path"] == str(self.shared)]
        self.assertEqual(
            sorted((record["agent"], record["scope"]) for record in shared), [("codex", "user"), ("pi", "user")]
        )
        self.assertTrue(all(record["is_symlink"] for record in shared))
        self.assertTrue(all(record["symlink_target"] == str(self.shared) for record in shared))
        self.assertEqual(
            [group["resolved_path"] for group in self.data["groups"]],
            [str(self.project / "AGENTS.md"), str(self.shared)],
        )
        self.assertIn("软链 → `" + str(self.shared) + "`", self.md)

    def test_coverage_lists_every_type_and_project_scope(self):
        coverage = {row["kind"]: row for row in self.data["coverage"]}
        self.assertEqual(
            list(coverage),
            ["instructions", "skill", "mcp", "hook", "extension", "prompt", "plugin", "settings", "rules", "memory"],
        )
        self.assertEqual(coverage["hook"]["status"], "unsupported")
        self.assertEqual(coverage["hook"]["discovered"], 0)
        self.assertEqual(coverage["skill"]["status"], "found")
        self.assertEqual(coverage["skill"]["discovered"], 4)
        self.assertEqual(coverage["settings"]["status"], "not_found")
        scopes = {row["scope"]: row for row in self.data["scopes"]}
        self.assertEqual(scopes["project"]["roots"], [str(self.project)])
        self.assertGreater(scopes["project"]["discovered"], 0)
        self.assertIn("未支持", self.md)
        self.assertIn("`hook`", self.md)
        self.assertIn("未发现", self.md)

    def test_report_is_human_readable_and_marks_unverified_load_chain(self):
        self.assertIn("# Harness 发现报告（只读）", self.md)
        self.assertIn("## 覆盖摘要", self.md)
        self.assertIn("## 发现条目", self.md)
        self.assertIn(str(self.host / ".claude" / "CLAUDE.md"), self.md)
        self.assertIn(f"`{self.host / '.claude/skills'}` · 入口 claude/user · 目录", self.md)
        self.assertIn("load_chain unverified", self.md)
        self.assertIn("未登记", self.md)
        self.assertTrue(
            all(
                record["path"].startswith(str(self.host)) or record["path"].startswith(str(self.project))
                for record in self.data["records"]
            )
        )
        # 报告里不出现运行时间：两次运行字节一致。
        first = (self.md, (self.phe / "reports" / "discovery.json").read_bytes())
        self.assertEqual(self.discover().returncode, 0)
        self.assertEqual(self.report_md(), first[0])
        self.assertEqual((self.phe / "reports" / "discovery.json").read_bytes(), first[1])


class DiscoverWithoutInitTests(WorkspaceFixture):
    def test_discover_requires_initialized_workspace(self):
        result = self.discover()
        self.assertEqual(result.returncode, 1)
        self.assertIn("init", result.stderr)
        self.assertFalse((self.phe / "reports").exists())

    def test_home_is_required(self):
        self.assertEqual(self.init().returncode, 0)
        result = run_cli(TOOL, "discover", "--phe-home", str(self.phe), cwd=self.elsewhere)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--home", result.stderr)
        self.assertFalse((self.phe / "reports").exists())


class ProjectScopeTests(WorkspaceFixture):
    def test_project_scope_is_reported_as_unscanned_without_project_flag(self):
        self.assertEqual(self.init().returncode, 0)
        result = run_cli(TOOL, "discover", "--phe-home", str(self.phe), "--home", str(self.host), cwd=self.elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        scopes = {row["scope"]: row for row in self.report_json()["scopes"]}
        self.assertIsNone(scopes["project"]["discovered"])
        self.assertEqual(scopes["project"]["roots"], [])
        self.assertIn("项目级范围未扫描", self.report_md())
        self.assertIn("没有传 --project", self.report_md())


class RegisteredAssetTests(WorkspaceFixture):
    def test_existing_registry_marks_only_registered_asset(self):
        self.assertEqual(self.init().returncode, 0)
        inventory_json = self.tmp / "registry" / "inventory.json"
        result = run_cli(
            INVENTORY,
            "--home",
            str(self.host),
            "--project",
            str(self.project),
            "--out",
            str(inventory_json),
            cwd=self.elsewhere,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            REGISTRY,
            "adopt",
            "--inventory",
            str(inventory_json),
            "--registry",
            str(self.registry_path),
            "--group",
            str(self.shared),
            cwd=self.elsewhere,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.discover().returncode, 0)
        data = self.report_json()
        self.assertEqual(data["registry"], {"path": str(self.registry_path), "present": True})
        registered = [record for record in data["records"] if record["registry_status"] == "registered"]
        self.assertEqual([record["registry_asset_id"] for record in registered], ["asset-0001", "asset-0001"])
        self.assertEqual({record["registry_state"] for record in registered}, {"Active"})
        others = [record for record in data["records"] if record["registry_status"] == "unregistered"]
        self.assertGreater(len(others), 0)
        self.assertIn("已登记 asset-0001", self.report_md())


class SourceCopyTests(unittest.TestCase):
    """从源码副本运行：导入本地模块不能在源码树里留下 __pycache__ 或其他文件。"""

    def test_source_copy_is_unchanged(self):
        tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="workspace-src-")))
        self.addCleanup(shutil.rmtree, tmp)
        source = tmp / "src"
        shutil.copytree(ROOT / "tools", source / "tools", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        write(tmp / "host" / ".claude/CLAUDE.md", "# 指令\n")
        phe = tmp / "phe"
        elsewhere = tmp / "elsewhere"
        elsewhere.mkdir()
        tool = source / "tools" / "workspace.py"
        before = snapshot(source)
        result = run_cli(tool, "init", "--phe-home", str(phe), cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(tool, "discover", "--phe-home", str(phe), "--home", str(tmp / "host"), cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(source), before)
        self.assertFalse((source / "tools" / "__pycache__").exists())


class SubagentDefinitionTests(WorkspaceFixture):
    """子代理定义：盘点归成 instructions，但逻辑类型未支持，报告只能标成只读观察。"""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.init().returncode, 0)
        self.assertEqual(self.discover().returncode, 0)
        self.records = self.records_by_path()

    def test_subagent_definitions_and_their_directories_are_observe_only(self):
        paths = (
            self.host / ".claude/agents",
            self.host / ".claude/agents/reviewer.md",
            self.host / ".pi/agent/agents",
            self.host / ".pi/agent/agents/planner.md",
        )
        for path in paths:
            record = self.records[str(path)]
            self.assertEqual(record["kind"], "instructions", path)
            self.assertEqual(record["management"], "observe_only", path)
            self.assertIn("子代理", record["management_note"], path)
            self.assertIn("未支持", record["management_note"], path)

    def test_report_shows_observe_only_for_subagent_entries(self):
        lines = self.report_md().splitlines()
        definition = next(line for line in lines if "reviewer.md" in line)
        directory = next(line for line in lines if line.startswith(f"- `{self.host / '.claude/agents'}` ·"))
        for line in (definition, directory):
            self.assertIn("能力 仅观察", line)
            self.assertNotIn("沙箱可托管", line)

    def test_single_file_instruction_keeps_sandbox_capability(self):
        self.assertEqual(self.records[str(self.host / ".claude/CLAUDE.md")]["management"], "sandbox_managed")
        coverage = {row["kind"]: row for row in self.report_json()["coverage"]}
        self.assertIn("子代理定义", coverage["instructions"]["note"])


if __name__ == "__main__":
    unittest.main()
