"""R3a 旧沙箱闭环：目录型 Skill 的多文件 patch 走 `change.py apply/restore`（全在临时目录）。

来源目录在合成沙箱根内，两个 Host 入口（Pi 的两个 Skill 根）软链到它。apply 走既有 release + deploy，
restore 按 journal 逆序把入口指回原目录。验收口径：多文件 patch（改正文、改引用文件、加引用文件、
删引用文件）落到发布里；两个入口指向同一发布；打不上当前内容时失败且不写入口/不记账；恢复后文件集合、
每个文件字节与入口原链接字符串都与应用前一致。
"""

import json
import os
import unittest
from pathlib import Path

from skill_asset import APPLIED_NOTES, APPLIED_SKILL_MD, PATCH_P1, files_of, link_tree, write_skill
from test_change import ChangeFixture
from test_release import REGISTRY, run_cli


class SkillChangeFixture(ChangeFixture):
    """把三个单文件指令入口换成两个指向同一合成 Skill 目录的软链入口。"""

    def setUp(self):
        super().setUp()
        for path in self.entries.values():
            path.unlink()
        self.skill = self.temper / "skills" / "demo"
        write_skill(self.skill)
        self.before_files = files_of(self.skill)
        self.entries = {
            "pi": self.home / ".pi/agent/skills/demo",
            "agents": self.home / ".agents/skills/demo",
        }
        for path in self.entries.values():
            link_tree(self.skill, path)
        self.links = {name: os.readlink(path) for name, path in self.entries.items()}
        self.shared = self.skill / "SKILL.md"
        self.reg = self.out / "registry-skill.json"
        result = run_cli(
            REGISTRY,
            "adopt",
            "--inventory",
            str(self.inventory("inventory-skill.json")),
            "--registry",
            str(self.reg),
            "--group",
            str(self.skill),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.asset_id = json.loads(self.reg.read_text(encoding="utf-8"))["assets"][0]["asset_id"]
        self.change = self.package([self.item("P1", PATCH_P1, 8)])


class SkillChangeTests(SkillChangeFixture):
    def test_multi_file_patch_applies_to_both_entries_and_restores_original_files(self):
        approval = self.approve(["P1"])
        applied = self.apply("P1", approval)
        self.assertEqual((applied["before_files"], applied["release"]), (self.before_files, "r0001"))
        links = {name: os.readlink(path) for name, path in self.entries.items()}
        self.assertEqual(len(set(links.values())), 1)
        link = links["pi"]
        self.assertTrue(link.startswith(str(self.phe / "releases")))
        self.assertEqual(files_of(Path(link)), applied["after_files"])
        self.assertEqual(set(applied["after_files"]), {"SKILL.md", "references/notes.md", "references/extra.md"})
        self.assertEqual((Path(link) / "SKILL.md").read_text(encoding="utf-8"), APPLIED_SKILL_MD)
        self.assertEqual((Path(link) / "references/notes.md").read_text(encoding="utf-8"), APPLIED_NOTES)
        self.assertFalse((Path(link) / "references/draft.md").exists())
        # 来源目录不在写入范围内：apply 只写 PHE 资产与发布。
        self.assertEqual(files_of(self.skill), self.before_files)

        status = self.status()
        self.assertEqual(status["targets"][0]["files"], applied["after_files"])
        self.assertEqual(status["targets"][0]["sha256"], applied["after_sha256"])

        restored = self.restore("x0001")
        self.assertEqual(restored["restored_files"], self.before_files)
        self.assertEqual(restored["restored_sha256"], applied["before_sha256"])
        self.assertEqual({name: os.readlink(path) for name, path in self.entries.items()}, self.links)
        self.assertEqual(files_of(Path(os.readlink(self.entries["pi"]))), self.before_files)

    def test_stale_patch_fails_without_writing_entries_or_ledger(self):
        self.apply("P1", self.approve(["P1"]))
        link = os.readlink(self.entries["pi"])
        result = self.apply_cli("P1", self.approve(["P1"], name="approval-again.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("patch 打不上当前生效内容", result.stderr)
        self.assertEqual(os.readlink(self.entries["pi"]), link)
        self.assertEqual(len([fact for fact in self.ledger() if fact["kind"] == "apply"]), 1)


if __name__ == "__main__":
    unittest.main()
