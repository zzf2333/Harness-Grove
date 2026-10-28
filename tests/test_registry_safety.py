"""登记表的安全契约：drift 报告不覆盖受保护文件；编号删除后不复用；写入失败保留旧表。"""

import importlib.util
import json
import os
import unittest
from unittest.mock import patch

from test_registry import REGISTRY, RegistryFixture

SPEC = importlib.util.spec_from_file_location("registry_tool", REGISTRY)
assert SPEC is not None and SPEC.loader is not None
registry_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry_tool)


class SafetyTests(RegistryFixture):
    def test_drift_out_refuses_protected_files(self):
        self.assertEqual(self.adopt("--group", str(self.shared)).returncode, 0)
        hardlink = self.tmp / "hardlink-to-shared.md"
        os.link(self.shared, hardlink)
        for target in (self.reg, self.inv, self.shared, self.claude_link, hardlink):
            with self.subTest(out=str(target)):
                before = {p: p.read_bytes() for p in (self.reg, self.inv, self.shared)}
                result = self.drift("--out", str(target))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("受保护", result.stderr)
                self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_drift_out_to_free_path_still_works(self):
        self.assertEqual(self.adopt("--group", str(self.shared)).returncode, 0)
        out = self.out_dir / "report.json"
        result = self.drift("--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["changes"], 0)

    def test_deleted_highest_asset_id_is_not_reused(self):
        self.assertEqual(self.adopt("--group", str(self.shared)).returncode, 0)
        self.assertEqual(self.adopt("--path", str(self.settings)).returncode, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        data["assets"] = [a for a in data["assets"] if a["asset_id"] != "asset-0002"]
        self.reg.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.adopt("--path", str(self.project_agents)).returncode, 0)
        self.assertEqual([a["asset_id"] for a in self.assets()], ["asset-0001", "asset-0003"])
        # 清空资产列表后仍按高水位继续。
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        data["assets"] = []
        self.reg.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.adopt("--path", str(self.settings)).returncode, 0)
        self.assertEqual([a["asset_id"] for a in self.assets()], ["asset-0004"])

    def test_failed_write_keeps_old_registry(self):
        self.assertEqual(self.adopt("--group", str(self.shared)).returncode, 0)
        before = self.reg.read_bytes()
        with (
            patch.object(registry_tool.os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            registry_tool.write_atomic(self.reg, "{}")
        self.assertEqual(self.reg.read_bytes(), before)
        self.assertEqual([p.name for p in self.reg.parent.iterdir() if p.name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
