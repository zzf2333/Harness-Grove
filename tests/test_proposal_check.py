"""`tools/proposal.py check` 的契约：只读检查、白名单来自仓库模板、证据计数与组 ID 一致性。

夹具在 `proposal_fixture.py`。这里覆盖 `check` 对编辑后草稿的拒绝，以及在临时仓库副本里
不留任何字节码缓存的只读性；`draft` 的契约在 `test_proposal.py`。
"""

import json
import unittest

from proposal_fixture import EXPERIMENT_ID, GROUP_A, GROUP_B, RepoCopyCase


class CheckTests(RepoCopyCase):
    def test_check_reports_same_missing_items_and_never_allows_freeze(self):
        self.assertEqual(self.draft().returncode, 0)
        before = self.snapshot()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["action"], "check")
        self.assertTrue(report["checked"])
        self.assertIs(report["freeze_ready"], False)
        self.assertIn("retired_unavailable", report["freeze_ready_blockers"])
        self.assertEqual(report["missing"], [])
        self.assertIn("paths.phe_repo_abs", report["null_fields"])
        self.assertEqual(self.snapshot(), before)

    def test_check_does_not_reread_groups_file(self):
        self.assertEqual(self.draft().returncode, 0)
        self.groups_path.unlink()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["checked"])

    def test_check_without_candidate_is_out_of_scope(self):
        exp = self.experiments / "legacy-001"
        exp.mkdir(parents=True)
        (exp / "proposal.json").write_text(
            json.dumps({"experiment_id": "legacy-001", "status": "draft"}), encoding="utf-8"
        )
        result = self.check("legacy-001")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["scope"], "legacy_proposal_out_of_scope")
        self.assertIs(report["candidate_present"], False)
        self.assertIsNone(report["freeze_ready"])
        self.assertIs(report["freeze_ready_evaluated"], False)

    def test_check_rejects_corrupted_proposal(self):
        exp = self.experiments / EXPERIMENT_ID
        exp.mkdir(parents=True)
        (exp / "proposal.json").write_text("{broken", encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_check_rejects_symlinked_proposal(self):
        exp = self.experiments / EXPERIMENT_ID
        exp.mkdir(parents=True)
        outside = self.tmp / "outside-proposal.json"
        outside.write_text("{}", encoding="utf-8")
        (exp / "proposal.json").symlink_to(outside)
        result = self.check()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "{}")

    def test_check_rejects_missing_proposal(self):
        (self.experiments / EXPERIMENT_ID).mkdir(parents=True)
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_check_rejects_edited_candidate_evidence(self):
        self.assertEqual(self.draft().returncode, 0)

        def reorder(proposal):
            proposal["candidate"]["evidence"]["selected_group_ids"] = [GROUP_B, GROUP_A]

        self.edit_proposal(reorder)
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_check_rejects_metric_absent_from_repository_template(self):
        # 把 made_up_v1 加进草稿自己的 metrics 不能放行预测指标：白名单只来自仓库模板。
        self.assertEqual(self.draft().returncode, 0)

        def widen_whitelist(proposal):
            proposal["metrics"]["made_up_v1"] = {"calculator": "made_up_calculator_v1", "type": "binary"}
            proposal["candidate"]["host_judgment"]["predictions"][0]["metric"] = "made_up_v1"

        self.edit_proposal(widen_whitelist)
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("made_up_v1", result.stderr)

    def test_check_rejects_group_count_not_matching_pointers(self):
        self.assertEqual(self.draft().returncode, 0)

        def inflate_count(proposal):
            group = proposal["candidate"]["evidence"]["groups"][0]
            group["count"] = len(group["pointers"]) + 1

        self.edit_proposal(inflate_count)
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("count", result.stderr)

    def test_check_rejects_duplicate_group_ids_even_with_matching_selectors(self):
        self.assertEqual(self.draft().returncode, 0)

        def duplicate_group(proposal):
            evidence = proposal["candidate"]["evidence"]
            twin = json.loads(json.dumps(evidence["groups"][0]))
            evidence["groups"] = [evidence["groups"][0], twin]
            evidence["selected_group_ids"] = [GROUP_A, GROUP_A]

        self.edit_proposal(duplicate_group)
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("重复", result.stderr)


class NoBytecodeWriteTests(RepoCopyCase):
    def test_draft_and_check_leave_no_bytecode_cache_in_repository_copy(self):
        before = self.full_snapshot()
        self.assertEqual(self.draft().returncode, 0)
        self.assertEqual(self.check().returncode, 0)
        after = self.full_snapshot()
        artifacts = {"experiments", f"experiments/{EXPERIMENT_ID}", f"experiments/{EXPERIMENT_ID}/proposal.json"}
        self.assertEqual(set(after) - set(before), artifacts)
        self.assertEqual({path: after[path] for path in before}, before)
        self.assertEqual(
            [path for path in after if path.endswith(".pyc") or "__pycache__" in path.split("/")],
            [],
        )


if __name__ == "__main__":
    unittest.main()
