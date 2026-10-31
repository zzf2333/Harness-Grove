"""发布部署后入口指向 release 文件：发现报告按 observed 入口认登记身份，当前目标单独比较。

走完整 CLI：adopt → import → release → deploy → discover，全部在临时沙箱里。
"""

import unittest

from workspace_fixture import INVENTORY, REGISTRY, RELEASE, WorkspaceFixture, run_cli


class DeployedEntryTests(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init().returncode, 0)
        self.inventory_json = self.tmp / "registry" / "inventory.json"
        result = run_cli(
            INVENTORY,
            "--home",
            str(self.host),
            "--project",
            str(self.project),
            "--out",
            str(self.inventory_json),
            cwd=self.elsewhere,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            REGISTRY,
            "adopt",
            "--inventory",
            str(self.inventory_json),
            "--registry",
            str(self.registry_path),
            "--group",
            str(self.shared),
            cwd=self.elsewhere,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def release_cli(self, *args: str):
        return run_cli(RELEASE, *args, "--phe-home", str(self.phe), "--home", str(self.host), cwd=self.elsewhere)

    def test_deployed_entry_keeps_registered_identity_with_new_target(self):
        self.assertEqual(self.discover().returncode, 0)
        for path in (self.host / ".pi/agent/AGENTS.md", self.host / ".codex/AGENTS.md"):
            record = self.records_by_path()[str(path)]
            self.assertEqual(record["registry_status"], "registered", path)
            self.assertEqual(record["registry_asset_id"], "asset-0001", path)
            self.assertTrue(record["registry_target_matches"], path)

        result = self.release_cli(
            "import", "--registry", str(self.registry_path), "--inventory", str(self.inventory_json)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.release_cli("release")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.release_cli(
            "deploy", "--agent", "codex", "--release", "r0001", "--registry", str(self.registry_path)
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self.discover().returncode, 0)
        records = self.records_by_path()
        deployed = records[str(self.host / ".codex/AGENTS.md")]
        self.assertEqual(
            deployed["resolved_path"],
            str(self.phe / "releases" / "r0001" / "files" / "asset-0001" / "AGENTS.md"),
        )
        self.assertEqual(deployed["registry_status"], "registered")
        self.assertEqual(deployed["registry_asset_id"], "asset-0001")
        self.assertEqual(deployed["registry_target"], str(self.shared))
        self.assertFalse(deployed["registry_target_matches"])
        untouched = records[str(self.host / ".pi/agent/AGENTS.md")]
        self.assertEqual(untouched["registry_status"], "registered")
        self.assertTrue(untouched["registry_target_matches"])
        self.assertEqual(records[str(self.host / ".claude/CLAUDE.md")]["registry_status"], "unregistered")
        md = self.report_md()
        self.assertIn("已登记 asset-0001", md)
        self.assertIn("不是登记源", md)


if __name__ == "__main__":
    unittest.main()
