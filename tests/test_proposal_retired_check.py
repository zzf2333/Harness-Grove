"""`tools/proposal.py check` 在切片 B1 的契约：默认不回读外部来源、显式 `--retired` 复核来源。

夹具在 `proposal_fixture.py`，合成 Retired 输入全部手写。`draft --retired` 的契约在
`test_proposal_retired.py`。
"""

import hashlib
import json
import unittest

from proposal_fixture import EXPERIMENT_ID, RepoCopyCase, candidate_with_review, retired_document


class RetiredCheckTests(RepoCopyCase):
    def draft_with_retired(self, review=None, retired=None):
        candidate = self.write_candidate(candidate_with_review(review), name="check-review.json")
        result = self.draft(candidate=candidate, retired=retired or self.retired_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def proposal_bytes(self, experiment_id=EXPERIMENT_ID):
        return (self.experiments / experiment_id / "proposal.json").read_bytes()

    def test_default_check_does_not_read_retired_source(self):
        self.draft_with_retired()
        self.retired_path.unlink()
        before = self.snapshot()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["checked"])
        self.assertEqual(report["retired_source_check"]["status"], "not_performed")
        self.assertIn("无法检测外部变化", report["retired_source_check"]["reason"])
        self.assertFalse(report["freeze_ready"])
        self.assertEqual(self.snapshot(), before)

    def test_explicit_check_accepts_unchanged_source(self):
        self.draft_with_retired()
        before = self.snapshot()
        result = self.check(retired=self.retired_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        check = json.loads(result.stdout)["retired_source_check"]
        self.assertEqual(check["status"], "unchanged")
        self.assertFalse(check["proposal_updated"])
        self.assertEqual(self.snapshot(), before)

    def test_explicit_check_reports_changed_source_without_updating_proposal(self):
        self.draft_with_retired()
        before = self.snapshot()
        proposal_before = self.proposal_bytes()
        document = retired_document()
        document["entries"][0]["rejected_reason"] = "改过的理由"
        self.write_retired(document)
        result = self.check(retired=self.retired_path)
        self.assertEqual(result.returncode, 1, result.stderr)
        check = json.loads(result.stdout)["retired_source_check"]
        self.assertEqual(check["status"], "changed")
        self.assertEqual(check["action_required"], "re_search_required")
        self.assertFalse(check["proposal_updated"])
        self.assertEqual(self.proposal_bytes(), proposal_before)
        after = self.snapshot()
        self.assertEqual({path: after[path] for path in before}, before)
        self.assertEqual(set(after) - set(before), set())

    def test_group_times_come_from_editable_pointers_not_original_source(self):
        """时序只来自 proposal 里的可编辑指针：改草稿时间、不动外部文件即可消掉时序阻塞。"""
        document = retired_document()
        document["entries"][0]["retired_at"] = "2026-10-29T00:00:00Z"
        retired = self.write_retired(document, name="retired-after-group.json")
        groups_before = self.groups_path.read_bytes()
        draft = self.draft_with_retired(retired=retired)
        self.assertIn("retired_evidence_not_after_retirement", json.loads(draft.stdout)["freeze_ready_blockers"])

        def move_pointers_after_retirement(proposal):
            for group in proposal["candidate"]["evidence"]["groups"]:
                for pointer in group["pointers"]:
                    pointer["ts"] = "2026-10-30T00:00:00Z"

        self.edit_proposal(move_pointers_after_retirement)
        self.assertEqual(self.groups_path.read_bytes(), groups_before)
        result = self.check(retired=retired)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retired_source_check"]["status"], "unchanged")
        self.assertNotIn("retired_evidence_not_after_retirement", report["freeze_ready_blockers"])
        self.assertEqual(report["retired_findings"], [])
        self.assertFalse(report["freeze_ready"])
        self.assertIn("prediction_freeze_not_implemented", report["freeze_ready_blockers"])
        self.assertEqual(report["evidence"]["groups_file"]["sha256"], hashlib.sha256(groups_before).hexdigest())
        notes = "\n".join(report["notes"])
        self.assertIn("proposal 内可编辑的 pointers.ts", notes)
        self.assertIn("默认 check 与 check --retired 都不回读原始分组文件", notes)
        self.assertIn("retired source unchanged 只说明 Retired 来源文件字节未变", notes)
        default = self.check()
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn(
            "默认 check 与 check --retired 都不回读原始分组文件", "\n".join(json.loads(default.stdout)["notes"])
        )

    def test_explicit_check_reports_unavailable_source(self):
        self.draft_with_retired()
        self.retired_path.unlink()
        result = self.check(retired=self.tmp / "absent-retired.json")
        self.assertEqual(result.returncode, 1, result.stderr)
        check = json.loads(result.stdout)["retired_source_check"]
        self.assertEqual(check["status"], "unavailable")
        self.assertEqual(check["action_required"], "re_search_required")
        self.assertFalse(check["proposal_updated"])

    def test_check_rejects_tampered_retired_or_review(self):
        def review(proposal):
            return proposal["candidate"]["host_judgment"]["retired_review"]

        mutations = {
            "unknown-retired-id": lambda proposal: review(proposal)["entries"][0].update(retired_id="retired-forged"),
            "illegal-relation": lambda proposal: review(proposal).update(relation="similar"),
            "forged-matches": lambda proposal: review(proposal).update(matches=[]),
            "unknown-group": lambda proposal: review(proposal)["entries"][0].update(
                new_evidence_groups=["ffffffffffff"]
            ),
            "retired-status": lambda proposal: proposal["candidate"]["retired"].update(status="no_match"),
            "retired-entries-duplicate": lambda proposal: proposal["candidate"]["retired"]["entries"].append(
                dict(proposal["candidate"]["retired"]["entries"][0])
            ),
            "retired-missing-supplied-scope": lambda proposal: proposal["candidate"]["retired"].pop("supplied_scope"),
            "forged-retired-field": lambda proposal: proposal["candidate"]["retired"].update(matches=[]),
        }
        self.draft_with_retired()
        path = self.experiments / EXPERIMENT_ID / "proposal.json"
        original = path.read_bytes()
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                proposal = json.loads(original)
                mutate(proposal)
                path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
                result = self.check()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")
        path.write_bytes(original)

    def test_old_unavailable_draft_still_lists_blocker(self):
        self.assertEqual(self.draft().returncode, 0)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retired"]["status"], "unavailable")
        self.assertIn("retired_unavailable", report["freeze_ready_blockers"])
        self.assertEqual(report["retired_source_check"]["status"], "not_performed")
        explicit = self.check(retired=self.retired_path)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(json.loads(explicit.stdout)["retired_source_check"]["status"], "not_performed")


class RetiredNoBytecodeWriteTests(RepoCopyCase):
    def test_draft_and_explicit_check_leave_no_bytecode_cache(self):
        before = self.full_snapshot()
        candidate = self.write_candidate(candidate_with_review(), name="bytecode-review.json")
        self.assertEqual(self.draft(candidate=candidate, retired=self.retired_path).returncode, 0)
        self.assertEqual(self.check(retired=self.retired_path).returncode, 0)
        after = self.full_snapshot()
        artifacts = {"experiments", f"experiments/{EXPERIMENT_ID}", f"experiments/{EXPERIMENT_ID}/proposal.json"}
        self.assertEqual(set(after) - set(before), artifacts)
        self.assertEqual(
            [path for path in after if path.endswith(".pyc") or "__pycache__" in path.split("/")],
            [],
        )


if __name__ == "__main__":
    unittest.main()
