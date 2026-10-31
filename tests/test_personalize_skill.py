"""R3a 产品入口：目录型 Skill 的多文件预览、逐项批准、应用与逆序恢复（全在临时目录）。

合成 Skill 有两个引用文件；P1 一次改正文、改一个引用文件、加一个引用文件、删一个引用文件。
两个 Host 入口是 Pi 的两个 Skill 根（`.pi/agent/skills/demo` 与共享的 `.agents/skills/demo`），
都软链到同一来源目录。验收口径：预览逐文件列出增删改、未批准条目不生效、未受管入口（普通目录）
不被覆盖、预览后引用文件变了要重新预览、恢复后入口原链接字符串与文件集合/每个文件字节都与应用前一致。
"""

import json
import os
import shutil
import unittest
from pathlib import Path

from personalize_fixture import PersonalizeFixture, snapshot, write
from skill_asset import (
    APPLIED_NOTES,
    APPLIED_SKILL_MD,
    BASE_FILES,
    NOTES,
    PATCH_DELETE,
    PATCH_P1,
    PATCH_P2,
    files_of,
    link_tree,
    write_skill,
)

# 合成 Skill 有两个引用文件；P1 一次改正文、改一个引用文件、加一个引用文件、删一个引用文件。
# 两个 Host 入口是 Pi 的两个 Skill 根（`.pi/agent/skills/demo` 与共享的 `.agents/skills/demo`），
# 都软链到同一来源目录。验收口径：预览逐文件列出增删改、未批准条目不生效、未受管入口（普通目录）
# 不被覆盖、预览后引用文件变了要重新预览、恢复后入口原链接字符串与文件集合/每个文件字节都与应用前一致。


class SkillFixture(PersonalizeFixture):
    """把夹具的三个单文件指令入口换成两个指向同一合成 Skill 目录的软链入口。"""

    RELATIVE = False

    def setUp(self):
        super().setUp()
        for path in self.entries.values():
            path.unlink()
        self.skill = self.tmp / "shared" / "demo"
        write_skill(self.skill)
        self.entries = {
            "pi": self.home / ".pi/agent/skills/demo",
            "agents": self.home / ".agents/skills/demo",
        }
        for path in self.entries.values():
            link_tree(self.skill, path, relative=self.RELATIVE)
        self.links = {name: os.readlink(path) for name, path in self.entries.items()}

    def prepare(self) -> None:
        self.assertEqual(self.init().returncode, 0)
        result = self.cli(
            "adopt",
            "--inventory",
            str(self.inventory()),
            "--registry",
            str(self.phe / "registry/assets.json"),
            "--group",
            str(self.skill),
            tool=self.source / "tools/registry.py",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.asset_id = json.loads((self.phe / "registry/assets.json").read_text(encoding="utf-8"))["assets"][0][
            "asset_id"
        ]
        self.change = self.package(
            [self.item("P1", PATCH_P1, 8, "user_preference"), self.item("P2", PATCH_P2, 1, "hypothesis")]
        )

    def files(self) -> dict[str, str]:
        return files_of(self.skill)


class SkillPreviewTests(SkillFixture):
    def test_preview_lists_each_file_change_and_writes_nothing_outside_reports(self):
        before_home, before_skill = snapshot(self.home), snapshot(self.skill)
        self.prepare()
        result = self.preview("c1")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        self.assertEqual(plan["asset"]["kind"], "skill")
        entries = plan["asset"]["entries"]
        self.assertEqual([entry["agent"] for entry in entries], ["pi", "shared"])
        self.assertEqual(entries[0]["files"], entries[1]["files"])
        self.assertEqual(set(entries[0]["files"]), set(BASE_FILES))
        self.assertEqual(len(entries[0]["sha256"]), 64)
        item = plan["items"][0]
        self.assertEqual(
            {row["path"]: row["state"] for row in item["diffs"]},
            {
                "SKILL.md": "modified",
                "references/notes.md": "modified",
                "references/draft.md": "deleted",
                "references/extra.md": "added",
            },
        )
        self.assertEqual(
            set(item["expected_files"]),
            {"SKILL.md", "references/notes.md", "references/extra.md"},
        )
        preview = (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8")
        self.assertIn("`references/extra.md`（新增）", preview)
        self.assertIn("`references/draft.md`（删除）", preview)
        self.assertIn("+补充笔记：先复述问题。", preview)
        self.assertIn("-草稿：这条引用没有被 SKILL.md 用到。", preview)
        self.assertEqual(snapshot(self.home), before_home)
        self.assertEqual(snapshot(self.skill), before_skill)
        self.assertFalse((self.phe / "ledger.jsonl").exists())

    def test_reference_change_after_preview_requires_a_new_preview(self):
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        approval = self.approve(self.plan_path("c1"), ["P1"])
        write(self.skill / "references/notes.md", NOTES + "手改的一行。\n")
        edited = self.files()
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 1)
        self.assertIn("的内容在预览之后变了", result.stderr)
        self.assertEqual(self.files(), edited)
        self.assertEqual({name: os.readlink(path) for name, path in self.entries.items()}, self.links)
        self.assertFalse((self.phe / "ledger.jsonl").exists())

    def test_unmanaged_directory_entry_is_not_overwritten(self):
        self.prepare()
        self.entries["agents"].unlink()
        shutil.copytree(self.skill, self.entries["agents"])
        copied = files_of(self.entries["agents"])
        result = self.preview("c1")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        unwritable = [entry for entry in plan["asset"]["entries"] if not entry["writable"]]
        self.assertEqual(len(unwritable), 1)
        self.assertEqual(unwritable[0]["state"], "other")
        self.assertIn("PHE 不覆盖", unwritable[0]["note"])
        approval = self.approve(self.plan_path("c1"), ["P1"])
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 1)
        self.assertIn("入口是普通文件或目录", result.stderr)
        self.assertEqual(files_of(self.entries["agents"]), copied)
        self.assertEqual(os.readlink(self.entries["pi"]), self.links["pi"])
        self.assertFalse((self.phe / "ledger.jsonl").exists())


class SkillPreparedFixture(SkillFixture):
    """前置：准备并预览成功，拿到可执行计划与入口的原始文件集合。"""

    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        self.plan = self.plan_path("c1")
        self.before_files = self.files()

    def apply_p1(self) -> dict:
        approval = self.approve(self.plan, ["P1"], name="approval-p1.json")
        result = self.apply_cli(self.plan, "P1", approval)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class SkillApplyTests(SkillPreparedFixture):
    def test_only_approved_item_takes_effect_and_both_entries_share_one_release(self):
        applied = self.apply_p1()
        self.assertEqual((applied["asset_id"], applied["release"]), (self.asset_id, "r0001"))
        links = {name: os.readlink(path) for name, path in self.entries.items()}
        self.assertEqual(len(set(links.values())), 1)
        link = links["pi"]
        self.assertTrue(link.startswith(str(self.phe / "releases")))
        self.assertEqual(files_of(Path(link)), applied["after_files"])
        self.assertEqual(
            set(applied["after_files"]),
            {"SKILL.md", "references/notes.md", "references/extra.md"},
        )
        self.assertEqual(applied["before_files"], self.before_files)
        self.assertEqual((Path(link) / "SKILL.md").read_text(encoding="utf-8"), APPLIED_SKILL_MD)
        self.assertEqual((Path(link) / "references/notes.md").read_text(encoding="utf-8"), APPLIED_NOTES)
        self.assertFalse((Path(link) / "references/draft.md").exists())
        row = next(fact for fact in self.ledger() if fact["kind"] == "apply")
        self.assertEqual(row["asset_kind"], "skill")
        self.assertEqual(row["after_sha256"], applied["after_sha256"])
        self.assertEqual(row["after_files"], applied["after_files"])
        self.assertEqual(row["before_files"], self.before_files)
        self.assertNotIn("并给出依据", (Path(link) / "references/notes.md").read_text(encoding="utf-8"))
        self.assertEqual(self.files(), self.before_files)


class RestoreFlow(SkillPreparedFixture):
    def assert_restored(self, applied: dict) -> None:
        result = self.restore_preview("c1", applied["execution_id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        restore_plan_path = self.phe / "reports/c1" / f"restore-{applied['execution_id']}.json"
        restore_plan = json.loads(restore_plan_path.read_text(encoding="utf-8"))
        self.assertEqual(restore_plan["entries"][0]["restore"]["files"], self.before_files)
        preview = (self.phe / "reports/c1" / f"restore-{applied['execution_id']}.md").read_text(encoding="utf-8")
        self.assertIn("恢复后文件", preview)
        restore_approval = self.approve_restore(restore_plan_path, applied["execution_id"])
        result = self.restore_cli(restore_plan_path, applied["execution_id"], restore_approval)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = json.loads(result.stdout)
        self.assertEqual(restored["restored_files"], self.before_files)
        self.assertEqual(restored["restored_sha256"], applied["before_sha256"])
        self.assertEqual({name: os.readlink(path) for name, path in self.entries.items()}, self.links)
        self.assertEqual(self.files(), self.before_files)


class SkillRestoreTests(RestoreFlow):
    def test_restore_returns_original_links_files_and_bytes(self):
        self.assert_restored(self.apply_p1())


class RelativeSkillRestoreTests(RestoreFlow):
    """入口是相对软链：恢复要按入口父目录解析，并保留原链接字符串。"""

    RELATIVE = True

    def test_restore_keeps_the_original_relative_link_string(self):
        for name in self.links:
            self.assertFalse(os.path.isabs(self.links[name]))
        self.assert_restored(self.apply_p1())


class SkillDeleteOnlyTests(SkillFixture):
    """只删除一个引用文件的 patch：成对 diff 头里没有新增目标，`+++` 是 /dev/null。"""

    def setUp(self):
        super().setUp()
        self.prepare()
        self.change = self.package([self.item("D1", PATCH_DELETE, 8, "hypothesis")])
        self.before_files = self.files()
        result = self.preview("c1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_delete_only_patch_previews_applies_and_restores(self):
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        item = plan["items"][0]
        self.assertEqual(item["patch_targets"], ["references/draft.md"])
        self.assertEqual({row["path"]: row["state"] for row in item["diffs"]}, {"references/draft.md": "deleted"})
        self.assertEqual(set(item["expected_files"]), {"SKILL.md", "references/notes.md"})
        preview = (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8")
        self.assertIn("`references/draft.md`（删除）", preview)

        approval = self.approve(self.plan_path("c1"), ["D1"], name="approval-d1.json")
        result = self.apply_cli(self.plan_path("c1"), "D1", approval)
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = json.loads(result.stdout)
        self.assertEqual(set(applied["after_files"]), {"SKILL.md", "references/notes.md"})
        link = Path(os.readlink(self.entries["pi"]))
        self.assertFalse((link / "references/draft.md").exists())
        self.assertEqual(self.files(), self.before_files)

        result = self.restore_preview("c2", applied["execution_id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        restore_plan = self.phe / "reports/c2" / f"restore-{applied['execution_id']}.json"
        result = self.restore_cli(
            restore_plan, applied["execution_id"], self.approve_restore(restore_plan, applied["execution_id"])
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.files(), self.before_files)
        self.assertEqual(files_of(Path(os.readlink(self.entries["pi"]))), self.before_files)


class SkillResidualStagingTests(SkillFixture):
    """一次失败尝试留下的暂存树不能混进下一次批准的应用。

    产品入口在入口内容被改过后会先拒绝，走不到暂存；这里直接按“失败发生在打 patch 之后”留下的形态放入
    `staging/x0002/tree`，验证共用的暂存规则会先重建本次执行的暂存根。
    """

    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)

    def test_failed_attempt_residue_does_not_leak_into_the_next_apply(self):
        approval = self.approve(self.plan_path("c1"), ["P1"], name="approval-p1.json")
        applied = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        # 失败不追加 apply 事实，下一次仍用 x0002：按失败尝试的形态放一份从 P1 后入口复制出的暂存树。
        stale = self.phe / "staging/x0002/tree"
        entry = Path(os.path.realpath(self.entries["pi"]))
        for path in sorted(entry.rglob("*")):
            if path.is_file():
                target = stale / path.relative_to(entry)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
        self.assertTrue((stale / "references/extra.md").is_file())

        result = self.restore_preview("c2", "x0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        restore_plan = self.phe / "reports/c2/restore-x0001.json"
        result = self.restore_cli(restore_plan, "x0001", self.approve_restore(restore_plan, "x0001"))
        self.assertEqual(result.returncode, 0, result.stderr)

        # 恢复后重新预览：只批准 P2。暂存树里上一次留下的 references/extra.md 不属于这次批准。
        self.assertEqual(self.preview("c3").returncode, 0)
        approval = self.approve(self.plan_path("c3"), ["P2"], name="approval-p2.json")
        result = self.apply_cli(self.plan_path("c3"), "P2", approval)
        self.assertEqual(result.returncode, 0, result.stderr)
        applied_second = json.loads(result.stdout)
        self.assertEqual(set(applied_second["after_files"]), {"SKILL.md", "references/notes.md", "references/draft.md"})
        link = Path(os.readlink(self.entries["pi"]))
        self.assertFalse((link / "references/extra.md").exists())
        self.assertFalse((stale / "references/extra.md").exists())


if __name__ == "__main__":
    unittest.main()
