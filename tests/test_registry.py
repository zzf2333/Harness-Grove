"""tools/registry.py 的 adopt/drift 契约验收：选择、幂等、拒绝、四类 ExternalChange、不改被登记文件。"""

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
INVENTORY = ROOT / "tools/inventory.py"
REGISTRY = ROOT / "tools/registry.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def link(target: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(tool: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(tool), *args], capture_output=True, text=True, check=False)


class RegistryFixture(unittest.TestCase):
    """临时 HOME 和项目，用真实盘点工具产出 ObservedAsset 后再 adopt / drift。"""

    def setUp(self):
        # 先取真实路径，macOS 的 /var 软链会让路径断言变复杂。
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="registry-test-")))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.shared = self.tmp / "shared/AGENTS.md"
        self.shared_other = self.tmp / "shared/other.md"
        self.claude_link = self.home / ".claude/CLAUDE.md"
        self.codex_link = self.home / ".codex/AGENTS.md"
        self.broken_link = self.home / ".pi/agent/AGENTS.md"
        self.settings = self.project / ".claude/settings.json"
        self.project_agents = self.project / "AGENTS.md"
        self._build_fixture()
        self.out_dir = self.tmp / "out"
        self.reg = self.out_dir / "registry.json"
        self.inv = self.inventory("inventory-1.json")

    def _build_fixture(self):
        write(self.shared, "# shared\n")
        write(self.shared_other, "# other\n")
        write(self.settings, json.dumps({"permissions": {"allow": ["Bash"]}}))
        write(self.project_agents, "# project\n")
        link(self.shared, self.claude_link)
        link(self.shared, self.codex_link)
        link(self.tmp / "shared/missing.md", self.broken_link)

    def inventory(self, name: str) -> Path:
        out = self.out_dir / name
        result = run_cli(INVENTORY, "--home", str(self.home), "--project", str(self.project), "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def adopt(self, *selectors: str, registry: Path | None = None, inventory: Path | None = None):
        return run_cli(
            REGISTRY,
            "adopt",
            "--inventory",
            str(inventory or self.inv),
            "--registry",
            str(registry or self.reg),
            *selectors,
        )

    def drift(self, *extra: str, registry: Path | None = None, inventory: Path | None = None):
        return run_cli(
            REGISTRY,
            "drift",
            "--inventory",
            str(inventory or self.inv),
            "--registry",
            str(registry or self.reg),
            *extra,
        )

    def assets(self, registry: Path | None = None) -> list[dict]:
        data = json.loads((registry or self.reg).read_text(encoding="utf-8"))
        return data["assets"]

    def asset_id(self, resolved_path: Path) -> str:
        for asset in self.assets():
            if asset["resolved_path"] == str(resolved_path):
                return asset["asset_id"]
        raise AssertionError(f"登记表里没有 {resolved_path}")

    def drift_report(self, *extra: str, inventory: Path | None = None) -> dict:
        result = self.drift(*extra, inventory=inventory)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class AdoptTests(RegistryFixture):
    def test_adopt_group_records_every_reference(self):
        result = self.adopt("--group", str(self.shared))
        self.assertEqual(result.returncode, 0, result.stderr)
        assets = self.assets()
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset["asset_id"], "asset-0001")
        self.assertEqual(asset["resolved_path"], str(self.shared))
        self.assertEqual(
            [(o["agent"], o["path"]) for o in asset["observed"]],
            [("claude", str(self.claude_link)), ("codex", str(self.codex_link))],
        )
        for observed in asset["observed"]:
            self.assertTrue(observed["is_symlink"])
            self.assertEqual(observed["symlink_target"], str(self.shared))
        self.assertEqual(
            asset["baseline"]["paths"],
            {str(self.claude_link): str(self.shared), str(self.codex_link): str(self.shared)},
        )
        self.assertEqual(asset["baseline"]["sha256"], hashlib.sha256(b"# shared\n").hexdigest())
        self.assertEqual(asset["baseline"]["size"], len(b"# shared\n"))
        self.assertTrue(asset["baseline"]["mtime"].endswith("+00:00"))
        self.assertEqual(asset["state"], "Active")
        self.assertEqual(asset["origin"], "imported")
        self.assertEqual(asset["evidence_status"], "unverified")
        self.assertTrue(asset["adopted_at"].endswith("+00:00"))
        self.assertEqual(asset["inventory_sha256"], sha256(self.inv))

    def test_adopt_path_matches_group_asset(self):
        result = self.adopt("--path", str(self.claude_link))
        self.assertEqual(result.returncode, 0, result.stderr)
        assets = self.assets()
        self.assertEqual(len(assets), 1)
        # 资产按真实路径认定：按单条 path 登记也记录同组的另一条引用。
        self.assertEqual(assets[0]["resolved_path"], str(self.shared))
        self.assertEqual(len(assets[0]["observed"]), 2)
        # 普通文件：同一 path 被三个 agent 各记一条，observed 全部保留。
        result = self.adopt("--path", str(self.project_agents))
        self.assertEqual(result.returncode, 0, result.stderr)
        project_asset = self.assets()[1]
        self.assertEqual(project_asset["asset_id"], "asset-0002")
        self.assertEqual(project_asset["resolved_path"], str(self.project_agents))
        self.assertEqual(sorted(o["agent"] for o in project_asset["observed"]), ["claude", "codex", "pi"])
        self.assertEqual(project_asset["baseline"]["paths"], {str(self.project_agents): None})

    def test_asset_ids_increase_and_are_never_reused(self):
        self.assertEqual(self.adopt("--group", str(self.shared)).returncode, 0)
        self.assertEqual(self.adopt("--path", str(self.settings)).returncode, 0)
        self.assertEqual([a["asset_id"] for a in self.assets()], ["asset-0001", "asset-0002"])
        # 再登记一个已登记条目之后的新资产，编号接着最大编号走。
        self.assertEqual(self.adopt("--path", str(self.project_agents)).returncode, 0)
        self.assertEqual([a["asset_id"] for a in self.assets()], ["asset-0001", "asset-0002", "asset-0003"])

    def test_duplicate_adopt_does_not_register_twice(self):
        self.assertEqual(self.adopt("--group", str(self.shared)).returncode, 0)
        before = self.reg.read_text(encoding="utf-8")
        result = self.adopt("--path", str(self.codex_link))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("已登记为 asset-0001", result.stdout)
        self.assertEqual(len(self.assets()), 1)
        self.assertEqual(self.reg.read_text(encoding="utf-8"), before)

    def test_refuses_broken_link(self):
        result = self.adopt("--path", str(self.broken_link))
        self.assertEqual(result.returncode, 1)
        self.assertIn("broken_link", result.stderr)
        self.assertFalse(self.reg.exists())

    def test_refuses_access_error(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行无法用 chmod 构造权限错误")
        self.addCleanup(os.chmod, self.settings, 0o600)
        os.chmod(self.settings, 0o000)
        self.inv = self.inventory("inventory-locked.json")
        result = self.adopt("--path", str(self.settings))
        self.assertEqual(result.returncode, 1)
        self.assertIn("access_error=PermissionError", result.stderr)
        self.assertFalse(self.reg.exists())

    def test_adopt_changes_no_adopted_file(self):
        before = {
            path: (sha256(path), os.lstat(path).st_mtime_ns)
            for path in (self.shared, self.claude_link, self.project_agents)
        }
        tree_before = tree(self.home) | tree(self.project)
        result = self.adopt("--group", str(self.shared), "--path", str(self.project_agents))
        self.assertEqual(result.returncode, 0, result.stderr)
        for path, (digest, mtime) in before.items():
            self.assertEqual(sha256(path), digest, path)
            self.assertEqual(os.lstat(path).st_mtime_ns, mtime, path)
        self.assertEqual(tree(self.home) | tree(self.project), tree_before)

    def test_adopt_then_drift_reports_nothing(self):
        self.assertEqual(self.adopt("--group", str(self.shared), "--path", str(self.settings)).returncode, 0)
        report = self.drift_report(inventory=self.inventory("inventory-2.json"))
        self.assertEqual(report["changes"], 0)
        self.assertEqual(report["external_changes"], [])


class DriftTests(RegistryFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.adopt("--group", str(self.shared), "--path", str(self.settings)).returncode, 0)

    def test_content_change_reported(self):
        write(self.shared, "# shared v2\n")
        report = self.drift_report(inventory=self.inventory("inventory-2.json"))
        self.assertEqual(report["changes"], 1)
        change = report["external_changes"][0]
        self.assertEqual(change["asset_id"], self.asset_id(self.shared))
        self.assertEqual(change["type"], "content_changed")
        self.assertEqual(change["path"], str(self.shared))
        self.assertEqual(change["before"]["sha256"], hashlib.sha256(b"# shared\n").hexdigest())
        self.assertEqual(change["after"]["sha256"], hashlib.sha256(b"# shared v2\n").hexdigest())
        self.assertTrue(change["detected_at"].endswith("+00:00"))

    def test_symlink_retarget_reported(self):
        self.claude_link.unlink()
        link(self.shared_other, self.claude_link)
        report = self.drift_report(inventory=self.inventory("inventory-2.json"))
        self.assertEqual(report["changes"], 1)
        change = report["external_changes"][0]
        self.assertEqual(change["asset_id"], self.asset_id(self.shared))
        self.assertEqual(change["type"], "symlink_target_changed")
        self.assertEqual(change["path"], str(self.claude_link))
        self.assertEqual(change["before"], {"symlink_target": str(self.shared), "resolved_path": str(self.shared)})
        self.assertEqual(
            change["after"], {"symlink_target": str(self.shared_other), "resolved_path": str(self.shared_other)}
        )

    def test_deleted_symlink_reported(self):
        self.codex_link.unlink()
        report = self.drift_report(inventory=self.inventory("inventory-2.json"))
        self.assertEqual(report["changes"], 1)
        change = report["external_changes"][0]
        self.assertEqual(change["type"], "missing")
        self.assertEqual(change["asset_id"], self.asset_id(self.shared))
        self.assertEqual(change["path"], str(self.codex_link))
        self.assertIsNone(change["after"])

    def test_unreadable_asset_reported(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行无法用 chmod 构造权限错误")
        self.addCleanup(os.chmod, self.settings, 0o600)
        os.chmod(self.settings, 0o000)
        report = self.drift_report(inventory=self.inventory("inventory-2.json"))
        self.assertEqual(report["changes"], 1)
        change = report["external_changes"][0]
        self.assertEqual(change["type"], "unreadable")
        self.assertEqual(change["asset_id"], self.asset_id(self.settings))
        self.assertEqual(change["path"], str(self.settings))
        self.assertEqual(change["after"]["access_error"], "PermissionError")
        self.assertFalse(change["after"]["broken_link"])

    def test_unregistered_changes_are_not_drift(self):
        write(self.shared_other, "# other v2\n")
        write(self.project / ".claude/rules/new.md", "# new rule\n")
        write(self.project / ".mcp.json", json.dumps({"mcpServers": {}}))
        report = self.drift_report(inventory=self.inventory("inventory-2.json"))
        self.assertEqual(report["changes"], 0)
        self.assertEqual(report["external_changes"], [])

    def test_drift_writes_report_without_touching_registry(self):
        before = self.reg.read_text(encoding="utf-8")
        write(self.shared, "# shared v2\n")
        self.inv = self.inventory("inventory-2.json")
        out = self.out_dir / "drift.json"
        result = self.drift("--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["changes"], 1)
        self.assertEqual(report["inventory_sha256"], sha256(self.inv))
        self.assertEqual(self.reg.read_text(encoding="utf-8"), before)
        # 同一份新盘点再跑一次，结果一致：drift 不消费也不更新基线。
        self.assertEqual(self.drift_report()["changes"], 1)

    def test_drift_without_registry_fails(self):
        missing = self.out_dir / "no-such-registry.json"
        result = self.drift(registry=missing, inventory=self.inventory("inventory-2.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(missing), result.stderr)


if __name__ == "__main__":
    unittest.main()
