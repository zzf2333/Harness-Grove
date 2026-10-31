"""登记身份与入口现状分开：按来源路径认已登记资产，发布目标再按 observed 入口认。

走完整 CLI：adopt（共享组）→ 后续操作 → discover，全部在临时沙箱里。
两个正常契约：`release deploy` 换掉入口目标后仍认出资产身份；入口改指另一个真实文件并
重新登记后，报告认新资产，不被旧资产的历史 observed 入口遮住。
"""

import os
import unittest

from workspace_fixture import INVENTORY, REGISTRY, RELEASE, WorkspaceFixture, run_cli, write


class AdoptedSharedGroup(WorkspaceFixture):
    """共享指令的两个入口登记成 asset-0001，盘点结果落在仓库外的 PHE_HOME 里。"""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.init().returncode, 0)
        self.inventory_json = self.tmp / "registry" / "inventory.json"
        self.inventory()

    def inventory(self):
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
        return result

    def adopt_shared_group(self):
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
        return result


class DeployedEntryTests(AdoptedSharedGroup):
    def setUp(self):
        super().setUp()
        self.adopt_shared_group()

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


class RelinkedEntryTests(AdoptedSharedGroup):
    """入口从共享源改指另一个真实文件并重新登记：新资产不能被旧资产的 observed 入口遮住。"""

    def test_relinked_entry_reports_the_newly_adopted_asset(self):
        self.adopt_shared_group()
        other = self.tmp / "shared" / "AGENTS-next.md"
        write(other, "共享指令 v2：先给结论，再给理由。\n")
        entry = self.host / ".codex/AGENTS.md"
        entry.unlink()
        os.symlink(other, entry)
        self.inventory()
        result = run_cli(
            REGISTRY,
            "adopt",
            "--inventory",
            str(self.inventory_json),
            "--registry",
            str(self.registry_path),
            "--path",
            str(entry),
            cwd=self.elsewhere,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("adopted asset-0002", result.stdout)

        self.assertEqual(self.discover().returncode, 0)
        records = self.records_by_path()
        relinked = records[str(entry)]
        self.assertEqual(relinked["registry_status"], "registered")
        self.assertEqual(relinked["registry_asset_id"], "asset-0002")
        self.assertEqual(relinked["registry_target"], str(other))
        self.assertTrue(relinked["registry_target_matches"])
        untouched = records[str(self.host / ".pi/agent/AGENTS.md")]
        self.assertEqual(untouched["registry_asset_id"], "asset-0001")
        self.assertEqual(untouched["registry_target"], str(self.shared))
        self.assertTrue(untouched["registry_target_matches"])


if __name__ == "__main__":
    unittest.main()
