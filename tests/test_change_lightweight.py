"""PHE_SPEC §2.5 口径下的变更包闭环：三条写三行说明，不选实验也能批准、应用、记账、复盘、恢复。

复用 `tests/test_change.py` 的沙箱 fixture，只换合成内容：第 1、3 行是重复句，第 2 行诉求与“先给结论”
冲突，第 4 行是越界约束。逐条对应三种改动性质：偏好（W1）、清理重复（W2）、减少失误的假设（W3）。
"""

import json
import os
import unittest
from pathlib import Path

from test_change import ChangeFixture, sha256
from test_release import write

# 第 1、3 行重复；第 2 行诉求与“先给结论”冲突；第 4 行是越界约束。
LIGHT_BASE = "先给结论。\n先复述问题再回答。\n先给结论。\n用户说先别做时，不要越界执行。\n复盘时先读账本。\n"

# 偏好：把第 2 行改成“先给结论，再复述问题”。
PREFERENCE_PATCH = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -1,3 +1,3 @@
 先给结论。
-先复述问题再回答。
+先给结论，再复述问题。
 先给结论。
"""

# 清理：第 3 行与第 1 行重复，保留第 1 行原文，不改其他约束。
CLEANUP_PATCH = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -1,4 +1,3 @@
 先给结论。
 先复述问题再回答。
-先给结论。
 用户说先别做时，不要越界执行。
"""

# 假设：第 3、5 行在应用 PREFERENCE_PATCH 后仍未变，所以能接着打到当前生效内容上。
ASSUMPTION_PATCH = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -3,3 +3,3 @@
 先给结论。
-用户说先别做时，不要越界执行。
+用户说先别做时，不要越界执行，也不要顺手做相邻改动。
 复盘时先读账本。
"""


class LightweightEvaluationTests(ChangeFixture):
    """三条三行说明；只批准偏好与假设，清理条不批不生效。"""

    BASE_TEXT = LIGHT_BASE

    def light_item(self, item_id: str, patch: str, line: int, prediction: dict) -> dict:
        return {
            "item_id": item_id,
            "evidence": [f"{self.shared}:L{line}"],
            "prediction": prediction,
            "target": {"asset_id": self.asset_id},
            "patch": patch,
        }

    def user_approval(self, items: list[str]) -> Path:
        data = {
            "schema_version": 1,
            "approval_id": "approval-user",
            "source": "user",
            "change_sha256": sha256(self.change.read_bytes()),
            "items": items,
            "note": "合成用户批准：只批准偏好与假设两条，清理条暂不处理",
        }
        path = self.out / "approval-user.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def test_lightweight_items_close_the_loop_without_an_experiment(self):
        """写三行说明 → 只批准偏好与假设 → 应用 → 复盘 → 逆序恢复回原字节。"""
        preference = {
            "why": "用户明确要求回答先给结论；第 2 行要求先复述问题，与新偏好冲突",
            "expected": "先给结论，再复述问题；不丢“用户说先别做时不越界”",
            "verify": "读一遍：措辞是否准确表达偏好、是否与其余行冲突；偏好不需要证明更优",
            "baseline": "待观察",
        }
        cleanup = {
            "why": "第 1 行与第 3 行是重复句",
            "expected": "同一约束只保留一处；保留第 1 行原文，不扩大授权",
            "verify": "对照改前改后含义：仍有一条先给结论；收益待验证",
        }
        assumption = {
            "why": "合成证据指针显示两次越界执行相邻任务",
            "expected": "越界后需要用户叫停的情况减少；不丢原有“先别做时不越界”约束",
            "verify": "收益待验证：本轮不做行为实验，批准真实应用后按观察窗统计叫停次数",
        }
        self.change = self.package(
            [
                self.light_item("W1", PREFERENCE_PATCH, 2, preference),
                self.light_item("W2", CLEANUP_PATCH, 3, cleanup),
                self.light_item("W3", ASSUMPTION_PATCH, 4, assumption),
            ]
        )

        # 1. 只批准偏好与假设，两条分别应用；清理条不批准、不生效。
        approval = self.user_approval(["W1", "W3"])
        first = self.apply("W1", approval)
        second = self.apply("W3", approval)
        self.assertEqual((first["execution_id"], first["proposal_id"]), ("x0001", "W1"))
        self.assertEqual((second["execution_id"], second["proposal_id"]), ("x0002", "W3"))
        lines = self.effective().decode().splitlines()
        self.assertEqual(lines[1], "先给结论，再复述问题。")
        self.assertEqual(lines[3], "用户说先别做时，不要越界执行，也不要顺手做相邻改动。")
        self.assertEqual(lines.count("先给结论。"), 2, "清理条未批准，重复句应当还在")
        for path in self.entries.values():
            self.assertEqual(path.read_bytes(), self.effective())

        # 2. 账本记批准来源与三行说明原文；没有任何字段把批准或应用写成效果已验证。
        facts = self.ledger()
        self.assertEqual([fact["kind"] for fact in facts], ["approval", "apply", "approval", "apply"])
        self.assertEqual(facts[0]["source"], "user")
        self.assertEqual(facts[0]["approved_items"], ["W1", "W3"])
        self.assertEqual(facts[1]["prediction"], preference)
        self.assertEqual(facts[3]["prediction"], assumption)
        self.assertNotIn("W2", [fact.get("proposal_id") for fact in facts])
        for fact in facts:
            self.assertNotIn("已验证", json.dumps(fact, ensure_ascii=False), fact)
            self.assertFalse([key for key in fact if "effect" in key or "verified" in key], fact)

        # 3. 对假设条目写一条 review：收益待验证，只能 observe / unknown。
        record_data = {
            "verdict": "observe",
            "prediction_met": "unknown",
            "reason": "收益待验证：只在临时沙箱应用，没做行为实验，真实 Host 从未加载",
            "observations": [f"{self.shared}:L4", str(self.change)],
            "next": "用户批准真实应用后，在观察窗内统计越界后需要叫停的次数；在此之前保持未验证采纳",
        }
        record = self.out / "review-assumption.json"
        write(record, json.dumps(record_data, ensure_ascii=False, indent=2) + "\n")
        review = self.review("x0002", record)
        self.assertEqual(review["record"], record_data)
        self.assertEqual(review["record_sha256"], sha256(record.read_bytes()))

        plan = self.review_plan()
        self.assertEqual([entry["proposal_id"] for entry in plan["executions"]], ["W1", "W3"])
        entry = plan["executions"][1]
        self.assertEqual(entry["prediction"], assumption)
        self.assertEqual(entry["state"], "applied")
        self.assertEqual(entry["observation_window"], {"from": facts[3]["at"], "to": None})
        self.assertEqual(entry["latest_review"]["record"]["verdict"], "observe")
        status = self.status()
        self.assertEqual([item["latest_review"] for item in status["executions"]], [None, review])
        self.assertEqual([item["state"] for item in status["executions"]], ["applied", "applied"])

        # 4. 逆序恢复：先回偏好版本，再回到原字节；复盘记录仍可读。
        restored = self.restore("x0002")
        self.assertEqual(restored["restored_sha256"], first["after_sha256"])
        self.assertIn("先给结论，再复述问题。", self.effective().decode())
        self.assertNotIn("也不要顺手做相邻改动", self.effective().decode())
        self.restore("x0001")
        for path in self.entries.values():
            self.assertEqual(os.readlink(path), str(self.shared))
            self.assertEqual(path.read_bytes(), LIGHT_BASE.encode())
        self.assertEqual(self.shared.read_bytes(), LIGHT_BASE.encode())
        self.assertEqual(self.bindings(), {})
        entry = self.review_plan()["executions"][1]
        self.assertEqual(entry["state"], "restored")
        self.assertEqual(entry["observation_window"]["to"], restored["at"])
        self.assertEqual(entry["latest_review"]["record"]["verdict"], "observe")


if __name__ == "__main__":
    unittest.main()
