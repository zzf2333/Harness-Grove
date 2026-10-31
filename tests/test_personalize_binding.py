"""R2 产品入口执行前的绑定核对：PHE_HOME、每个入口当前读的内容、恢复目标的内容。

这些核对都在写入之前：在 A 预览并批准的计划不能拿 `--phe-home B` 执行；入口或恢复目标的内容在预览
之后变了要求重新预览。全部在临时目录里跑（合成 home、仓外 PHE_HOME、仓库副本、无关 cwd）。
"""

import json
import os
import unittest
from pathlib import Path

from personalize_fixture import BASE, PersonalizeFixture, sha256, snapshot, write


class PheHomeBindingTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)

    def test_apply_refuses_a_plan_from_another_phe_home(self):
        """在 A 预览并批准后换成 `--phe-home B` 执行：执行前核对规范化 PHE_HOME，B 一个字节都不写。"""
        approval = self.approve(self.plan_path("c1"), ["P1"])
        other = self.tmp / "phe-other"
        init = self.cli("init", "--phe-home", str(other), tool=self.source / "tools/workspace.py")
        self.assertEqual(init.returncode, 0, init.stderr)
        before = snapshot(other)
        result = self.cli(
            "apply",
            "--phe-home",
            str(other),
            "--registry",
            str(self.phe / "registry/assets.json"),
            "--plan",
            str(self.plan_path("c1")),
            "--item",
            "P1",
            "--approval",
            str(approval),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("PHE_HOME", result.stderr)
        self.assertEqual(snapshot(other), before)
        self.assertEqual(self.effective(), BASE.encode())


class EntryContentBindingTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()

    def test_apply_rechecks_every_entry_reading_the_planned_content(self):
        """两条入口读同字节的不同文件，预览后其中一个被改：应用前逐入口核对内容，不只看首入口。"""
        alt = self.tmp / "alt-AGENTS.md"
        write(alt, BASE)
        self.entries["codex"].unlink()
        os.symlink(alt, self.entries["codex"])
        self.assertEqual(self.preview("c1").returncode, 0)
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(entry["sha256"] for entry in plan["asset"]["entries"]),
            [plan["items"][0]["current_sha256"]] * 3,
        )
        approval = self.approve(self.plan_path("c1"), ["P1"])
        changed = BASE + "手改的一行。\n"
        write(alt, changed)
        refused = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("内容在预览之后变了", refused.stderr)
        self.assertEqual(alt.read_bytes(), changed.encode())
        self.assertEqual(os.readlink(self.entries["claude"]), str(self.shared))
        self.assertFalse((self.phe / "ledger.jsonl").exists())


class RestoreBindingTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        approval = self.approve(self.plan_path("c1"), ["P1"])
        applied = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(applied.returncode, 0, applied.stderr)

    def restore_plan(self, cycle: str = "c2") -> Path:
        result = self.restore_preview(cycle, "x0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(json.loads(result.stdout)["plan"])

    def test_restore_refuses_a_plan_from_another_phe_home(self):
        """恢复也在写入前核对 PHE_HOME：在 A 预览并批准后不能改成 B。"""
        plan_path = self.restore_plan()
        approval = self.approve_restore(plan_path, "x0001")
        other = self.tmp / "phe-other"
        init = self.cli("init", "--phe-home", str(other), tool=self.source / "tools/workspace.py")
        self.assertEqual(init.returncode, 0, init.stderr)
        before = snapshot(other)
        refused = self.cli(
            "restore",
            "--phe-home",
            str(other),
            "--execution",
            "x0001",
            "--plan",
            str(plan_path),
            "--approval",
            str(approval),
        )
        self.assertEqual(refused.returncode, 1)
        self.assertIn("PHE_HOME", refused.stderr)
        self.assertEqual(snapshot(other), before)
        self.assertIn("先给结论，再复述问题。", self.effective().decode())

    def test_restore_refuses_when_the_approved_target_content_changed(self):
        """恢复预览批准后用户改了自己的共享文件：入口链接没变，但恢复目标内容变了，要重新预览。"""
        plan_path = self.restore_plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(entry["restore"]["content_sha256"] for entry in plan["entries"]), [sha256(BASE.encode())] * 3
        )
        approval = self.approve_restore(plan_path, "x0001")
        changed = BASE + "批准之后手改的一行。\n"
        write(self.shared, changed)
        refused = self.restore_cli(plan_path, "x0001", approval)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("恢复目标的内容在恢复预览之后变了", refused.stderr)
        self.assertEqual(self.shared.read_bytes(), changed.encode())
        self.assertIn("先给结论，再复述问题。", self.effective().decode())


class RelativeLinkRestoreTests(PersonalizeFixture):
    """一个入口是相对软链：恢复预览按链接所在目录解析内容摘要，恢复时仍写回原始相对目标。"""

    RELATIVE = "../../shared/AGENTS.md"

    def setUp(self):
        super().setUp()
        self.entries["codex"].unlink()
        os.symlink(self.RELATIVE, self.entries["codex"])
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        approval = self.approve(self.plan_path("c1"), ["P1"])
        applied = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(applied.returncode, 0, applied.stderr)

    def test_restore_preview_and_restore_use_the_link_directory_not_the_cwd(self):
        result = self.restore_preview("c2", "x0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan_path = Path(json.loads(result.stdout)["plan"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        relative = [entry for entry in plan["entries"] if entry["path"] == str(self.entries["codex"])]
        self.assertEqual(len(relative), 1)
        self.assertEqual(relative[0]["restore"]["old_target"], self.RELATIVE)
        self.assertEqual(relative[0]["restore"]["content_sha256"], sha256(BASE.encode()))

        approval = self.approve_restore(plan_path, "x0001")
        restored = self.restore_cli(plan_path, "x0001", approval)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(os.readlink(self.entries["codex"]), self.RELATIVE)
        self.assertEqual(self.entries["codex"].read_bytes(), BASE.encode())


if __name__ == "__main__":
    unittest.main()
