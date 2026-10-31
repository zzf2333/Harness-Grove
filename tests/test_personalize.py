"""R2 产品批准入口 `tools/personalize.py`：预览、批准、应用、账本视图与恢复。

全部在临时目录里跑：合成 home（三个共享入口）+ 仓外 PHE_HOME + 仓库副本 + 无关 cwd。
批准记录一律标为测试夹具；`source=user` 与 `source=sandbox_rehearsal` 两条分支都跑，都不代表真实用户同意。
两周期延续与旧沙箱政策对照在 `tests/test_personalize_cycles.py`。
"""

import json
import os
import unittest
from pathlib import Path

from personalize_fixture import BASE, PersonalizeFixture, evidence_at, sha256, snapshot, write

FIXTURE_NOTE = "测试夹具"


class PreviewTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()

    def test_preview_writes_only_phe_and_shows_diff_and_write_scope(self):
        """预览只写 reports/<cycle>/：不改 Host、不改共享来源，diff、sha256 与写入范围都在计划里。"""
        host_before = snapshot(self.home)
        shared_before = self.shared.read_bytes()
        result = self.preview("c1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(self.home), host_before)
        self.assertEqual(self.shared.read_bytes(), shared_before)

        summary = json.loads(result.stdout)
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        self.assertEqual(summary["plan_sha256"], sha256(self.plan_path("c1").read_bytes()))
        self.assertEqual(summary["plan_sha256"], (self.phe / "reports/c1/plan.sha256").read_text().strip())
        self.assertEqual(plan["kind"], "phe_product_plan")
        self.assertEqual(plan["report"]["sha256"], sha256(self.report.read_bytes()))
        self.assertEqual(plan["change"]["sha256"], sha256(self.change.read_bytes()))
        self.assertTrue(plan["asset"]["shared"])
        self.assertEqual([entry["agent"] for entry in plan["asset"]["entries"]], ["claude", "codex", "pi"])
        self.assertTrue(all(entry["state"] == "symlink" for entry in plan["asset"]["entries"]))
        self.assertEqual(plan["host_writes"], [entry["path"] for entry in plan["asset"]["entries"]])
        self.assertEqual(plan["restore"]["method"], "journal")
        self.assertEqual(plan["missing"], [])
        first = plan["items"][0]
        self.assertEqual((first["item_id"], first["current_sha256"]), ("P1", sha256(BASE.encode())))
        self.assertIn("-先复述问题再回答。", first["diff"])
        self.assertIn("+先给结论，再复述问题。", first["diff"])
        self.assertNotEqual(first["expected_sha256"], first["current_sha256"])

        preview_text = (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8")
        self.assertIn("不是自动诊断", preview_text)
        self.assertIn("为什么改", preview_text)
        self.assertIn(plan["report"]["sha256"], preview_text)
        self.assertIn("Host 入口（只这些）", preview_text)
        self.assertIn("恢复也要单独预览并再次批准", preview_text)

    def test_preview_stops_when_shared_entries_read_different_content(self):
        """共享入口已经分叉：只按第一份内容算 diff 会丢掉别的入口的附加规则，所以不生成可执行计划。"""
        alt = self.tmp / "alt-AGENTS.md"
        write(alt, BASE + "本地附加规则：只在这台机器上生效。\n")
        self.entries["codex"].unlink()
        os.symlink(alt, self.entries["codex"])
        result = self.preview("c1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("读的内容与计划要改的那份不同", result.stderr)
        self.assertIn("不支持多来源合并", result.stderr)
        self.assertFalse(self.plan_path("c1").exists())
        self.assertEqual(alt.read_bytes(), (BASE + "本地附加规则：只在这台机器上生效。\n").encode())


class ApplyTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)

    def apply(self, item: str, items: list[str], source: str, name: str = "approval.json") -> dict:
        result = self.apply_cli(self.plan_path("c1"), item, self.approve(self.plan_path("c1"), items, source, name))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_only_approved_items_take_effect_and_the_ledger_records_both_branches(self):
        """只批准 P1 时 P2 不生效；两条分支（user / sandbox_rehearsal）都按计划写、按账本记。"""
        approval = self.approve(self.plan_path("c1"), ["P1"], source="user")
        first_plan_sha = (self.phe / "reports/c1/plan.sha256").read_text().strip()
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = json.loads(result.stdout)
        self.assertEqual((applied["execution_id"], applied["proposal_id"]), ("x0001", "P1"))
        self.assertEqual(applied["source"], "user")
        self.assertIn("先给结论，再复述问题。", self.effective().decode())
        for path in self.entries.values():
            self.assertTrue(os.path.islink(path))
            self.assertEqual(Path(os.readlink(path)).relative_to(self.phe).parts[0], "releases")
            self.assertEqual(path.read_bytes(), self.effective())
        # 共享来源文件本身没被改：应用只写 PHE 发布与入口链接。
        self.assertEqual(self.shared.read_bytes(), BASE.encode())

        # P2 没被批准：不能生效，也不追加账本事实。
        facts_before = len(self.ledger())
        refused = self.apply_cli(self.plan_path("c1"), "P2", approval)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("批准记录没有覆盖条目", refused.stderr)
        self.assertEqual(len(self.ledger()), facts_before)
        self.assertNotIn("并对照原预测", self.effective().decode())

        # 第二个条目重新预览：从当前内容起算，批准写 sandbox_rehearsal。
        self.assertEqual(self.preview("c1").returncode, 0)
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        self.assertEqual(plan["items"][1]["current_sha256"], sha256(self.effective()))
        rehearsal = self.approve(self.plan_path("c1"), ["P2"], source="sandbox_rehearsal", name="rehearsal.json")
        result = self.apply_cli(self.plan_path("c1"), "P2", rehearsal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["source"], "sandbox_rehearsal")
        content = self.effective().decode()
        self.assertIn("先给结论，再复述问题。", content)
        self.assertIn("复盘时先读账本，并对照原预测。", content)

        facts = self.ledger()
        self.assertEqual([fact["kind"] for fact in facts], ["approval", "apply", "approval", "apply"])
        self.assertEqual(
            [fact["source"] for fact in facts if fact["kind"] == "approval"], ["user", "sandbox_rehearsal"]
        )
        for fact in facts:
            if "note" in fact:
                self.assertIn(FIXTURE_NOTE, fact["note"])
            self.assertNotIn("verified", json.dumps(fact, ensure_ascii=False))
        self.assertEqual(facts[1]["plan_sha256"], first_plan_sha)
        self.assertEqual(facts[3]["plan_sha256"], (self.phe / "reports/c1/plan.sha256").read_text().strip())
        self.assertEqual(facts[1]["cycle"], "c1")
        self.assertEqual(facts[3]["prediction"]["why"], "合成证据 L3 指向的偏好或观察")
        self.assertEqual(len(facts[1]["target"]["paths"]), 3)
        # 证据指针指向原文：P2 指的那一行是用户消息，不是助手回复。
        pointer = facts[3]["evidence"][0].rsplit(":L", 1)
        self.assertEqual(evidence_at(Path(pointer[0]), int(pointer[1]))["role"], "user")
        self.assertEqual(evidence_at(Path(pointer[0]), int(pointer[1]))["text"], "不对，复盘时也要对照原预测。")

        # 只有计划列明的入口被写：home 里未登记的入口一个字节都没变。
        extra = self.home / ".pi/agent/extra.md"
        write(extra, "未登记的额外文件\n")
        before = sha256(extra.read_bytes())
        self.assertEqual(self.preview("c1").returncode, 0)
        plan = json.loads(self.plan_path("c1").read_text(encoding="utf-8"))
        self.assertEqual([item["state"] for item in plan["items"]], ["not_applicable", "not_applicable"])
        self.assertIn("当前不适用", (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8"))
        again = self.apply_cli(
            self.plan_path("c1"),
            "P1",
            self.approve(self.plan_path("c1"), ["P1"], "user", "approval-again.json"),
        )
        self.assertEqual(again.returncode, 1)
        self.assertIn("当前不适用", again.stderr)
        self.assertEqual(sha256(extra.read_bytes()), before)

    def test_preview_plan_is_invalidated_when_the_change_package_changes(self):
        approval = self.approve(self.plan_path("c1"), ["P1"])
        write(self.change, json.dumps({**json.loads(self.change.read_text()), "note": "手改"}, ensure_ascii=False))
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 1)
        self.assertIn("变更包在预览之后变了", result.stderr)
        self.assertEqual(self.effective(), BASE.encode())

    def test_preview_plan_is_invalidated_when_an_entry_changes(self):
        approval = self.approve(self.plan_path("c1"), ["P1"])
        elsewhere = self.tmp / "another-AGENTS.md"
        write(elsewhere, "别的内容\n")
        self.entries["codex"].unlink()
        os.symlink(elsewhere, self.entries["codex"])
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 1)
        self.assertIn("与预览时不同", result.stderr)
        self.assertEqual(self.effective(), BASE.encode())
        self.assertFalse((self.phe / "ledger.jsonl").exists())

    def test_preview_plan_is_invalidated_when_the_target_content_changes(self):
        approval = self.approve(self.plan_path("c1"), ["P1"])
        changed = BASE + "手改的一行。\n"
        write(self.shared, changed)
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 1)
        self.assertIn("的内容在预览之后变了", result.stderr)
        self.assertEqual(self.shared.read_bytes(), changed.encode())

    def test_approval_is_bound_to_the_current_plan_bytes(self):
        approval = self.approve(self.plan_path("c1"), ["P1"])
        data = json.loads(approval.read_text(encoding="utf-8"))
        data["plan_sha256"] = "0" * 64
        write(approval, json.dumps(data, ensure_ascii=False))
        result = self.apply_cli(self.plan_path("c1"), "P1", approval)
        self.assertEqual(result.returncode, 1)
        self.assertIn("绑定的计划 sha256", result.stderr)


class RestoreTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        self.first = self.apply("P1", ["P1"], source="user")
        self.assertEqual(self.preview("c1").returncode, 0)
        self.second = self.apply("P2", ["P2"], source="sandbox_rehearsal", name="rehearsal.json")

    def apply(self, item: str, items: list[str], source: str, name: str = "approval.json") -> dict:
        result = self.apply_cli(self.plan_path("c1"), item, self.approve(self.plan_path("c1"), items, source, name))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_restore_previews_then_needs_its_own_approval_and_returns_original_bytes(self):
        """逆序限制、恢复预览绑定的内容、错误的批准被拒，批准后回到原字节。"""
        out_of_order = self.restore_preview("c2", "x0001")
        self.assertEqual(out_of_order.returncode, 1)
        self.assertIn("逆序", out_of_order.stderr)

        result = self.restore_preview("c2", "x0002")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        plan_path = Path(summary["plan"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["kind"], "phe_restore_plan")
        self.assertEqual(plan["execution_id"], "x0002")
        self.assertEqual(summary["plan_sha256"], (self.phe / "reports/c2/restore-x0002.sha256").read_text().strip())
        self.assertEqual(
            sorted(entry["restore"]["content_sha256"] for entry in plan["entries"]),
            [self.first["after_sha256"]] * 3,
        )
        self.assertIn("恢复预览", (self.phe / "reports/c2/restore-x0002.md").read_text(encoding="utf-8"))

        # 没有绑定恢复计划字节的批准不能恢复。
        wrong = self.approve_restore(plan_path, "x0002")
        data = json.loads(wrong.read_text(encoding="utf-8"))
        data["plan_sha256"] = "0" * 64
        write(wrong, json.dumps(data, ensure_ascii=False))
        refused = self.restore_cli(plan_path, "x0002", wrong)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("恢复计划 sha256", refused.stderr)
        self.assertIn("并对照原预测", self.effective().decode())

        approval = self.approve_restore(plan_path, "x0002")
        restored = self.restore_cli(plan_path, "x0002", approval)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        row = json.loads(restored.stdout)
        self.assertEqual((row["kind"], row["source"]), ("restore", "user"))
        self.assertEqual(row["restored_sha256"], self.first["after_sha256"])
        content = self.effective().decode()
        self.assertIn("先给结论，再复述问题。", content)
        self.assertNotIn("并对照原预测", content)

        # 恢复 P1：回到共享来源，字节与应用前一致。
        result = self.restore_preview("c2", "x0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        final_plan = Path(json.loads(result.stdout)["plan"])
        approval = self.approve_restore(final_plan, "x0001")
        restored = self.restore_cli(final_plan, "x0001", approval)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["restored_sha256"], sha256(BASE.encode()))
        for path in self.entries.values():
            self.assertEqual(os.readlink(path), str(self.shared))
            self.assertEqual(path.read_bytes(), BASE.encode())
        self.assertEqual(json.loads((self.phe / "bindings.json").read_text()), {})

    def test_status_and_review_work_in_the_phe_home(self):
        status = self.cli("status", "--phe-home", str(self.phe))
        self.assertEqual(status.returncode, 0, status.stderr)
        data = json.loads(status.stdout)
        self.assertEqual([item["execution_id"] for item in data["executions"]], ["x0001", "x0002"])
        self.assertEqual([item["source"] for item in data["executions"]], ["user", "sandbox_rehearsal"])
        plan = self.cli("review-plan", "--phe-home", str(self.phe))
        self.assertEqual(plan.returncode, 0, plan.stderr)
        entries = json.loads(plan.stdout)["executions"]
        self.assertEqual(entries[0]["prediction"]["why"], "合成证据 L2 指向的偏好或观察")
        self.assertEqual(entries[0]["observation_window"], {"from": entries[0]["applied_at"], "to": None})
        record = self.review_record("review-x1.json", "observe", "unknown", [f"{self.session_c1}:L2"])
        reviewed = self.cli("review", "--phe-home", str(self.phe), "--execution", "x0001", "--record", str(record))
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        row = json.loads(reviewed.stdout)
        self.assertEqual((row["kind"], row["execution_id"]), ("review", "x0001"))
        self.assertEqual(row["record_sha256"], sha256(record.read_bytes()))
        entries = json.loads(self.cli("review-plan", "--phe-home", str(self.phe)).stdout)["executions"]
        self.assertEqual(entries[0]["latest_review"]["record"]["prediction_met"], "unknown")


if __name__ == "__main__":
    unittest.main()
