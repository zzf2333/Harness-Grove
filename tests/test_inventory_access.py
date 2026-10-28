"""盘点对“能 stat、读不了内容”和“父路径是普通文件”的处理。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from test_inventory import record_at, run_cli, write


class ContentAccessTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行无法用 chmod 构造权限错误")
        temp = tempfile.TemporaryDirectory(prefix="phe-inv-access-")
        self.addCleanup(temp.cleanup)
        # 取真实路径：macOS 的 /var 是 /private/var 的软链。
        self.tmp = Path(os.path.realpath(temp.name))
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        # 文件自身不可读，父目录可访问。
        self.unreadable = self.home / ".codex/AGENTS.md"
        write(self.unreadable, "# codex\n")
        # 有效软链指向不可读文件。
        self.target = self.tmp / "shared/AGENTS.md"
        write(self.target, "# shared\n")
        (self.home / ".pi/agent").mkdir(parents=True)
        os.symlink(self.target, self.home / ".pi/agent/AGENTS.md")
        # skill 目录可读，SKILL.md 不可读。
        self.skill_md = self.home / ".claude/skills/demo/SKILL.md"
        write(self.skill_md, "# demo\n")
        # 一个没有 SKILL.md 的 skill：不算访问错误。
        (self.home / ".claude/skills/empty").mkdir()
        # 父路径是普通文件：其下候选确定不存在。
        write(self.project / ".claude", "not a directory")
        for path in (self.unreadable, self.target, self.skill_md):
            self.addCleanup(os.chmod, path, 0o600)
            os.chmod(path, 0o000)
        out = self.tmp / "out.json"
        result = run_cli("--home", str(self.home), "--project", str(self.project), "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.data = json.loads(out.read_text(encoding="utf-8"))

    def test_unreadable_file_records_access_error(self):
        record = record_at(self.data, self.unreadable, "codex")
        self.assertEqual(record["access_error"], "PermissionError")
        self.assertIsNone(record["sha256"])

    def test_valid_symlink_to_unreadable_file_records_access_error(self):
        record = record_at(self.data, self.home / ".pi/agent/AGENTS.md", "pi")
        self.assertTrue(record["is_symlink"])
        self.assertFalse(record["broken_link"])
        self.assertEqual(record["access_error"], "PermissionError")

    def test_unreadable_skill_md_records_access_error_but_missing_does_not(self):
        demo = record_at(self.data, self.home / ".claude/skills/demo", "claude")
        self.assertEqual(demo["access_error"], "PermissionError")
        empty = record_at(self.data, self.home / ".claude/skills/empty", "claude")
        self.assertNotIn("access_error", empty)
        self.assertIsNone(empty["sha256"])

    def test_descendants_of_plain_file_are_not_recorded(self):
        prefix = str(self.project / ".claude") + os.sep
        self.assertEqual([r["path"] for r in self.data["records"] if r["path"].startswith(prefix)], [])


if __name__ == "__main__":
    unittest.main()
