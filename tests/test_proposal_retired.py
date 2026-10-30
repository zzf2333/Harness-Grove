"""`tools/proposal.py draft --retired` 的契约：显式 Retired 检索输入的保存、拒绝与关系核对。

夹具在 `proposal_fixture.py`。合成 Retired 输入全部手写，不含真实退役记录；这里只走真实 CLI。
`check` 的契约在 `test_proposal_retired_check.py`。
"""

import hashlib
import json
import unittest

from proposal_fixture import (
    EXPERIMENT_ID,
    GROUP_A,
    GROUP_B,
    OLD_GROUP,
    RETIRED_ID_A,
    RETIRED_ID_B,
    RepoCopyCase,
    candidate_with_review,
    groups_document,
    retired_document,
)

REVIEW_PREFIX = "candidate.host_judgment.retired_review"


class RetiredDraftTests(RepoCopyCase):
    def assert_rejected(self, result):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_no_artifact()

    def review(self, relation="alternative", entries=None, **overrides):
        review = {"relation": relation, "reason": overrides.pop("reason", "合成理由"), "entries": entries or []}
        review.update(overrides)
        return review

    def draft_with_review(self, review, experiment_id=EXPERIMENT_ID, retired=None, groups=None):
        candidate = self.write_candidate(candidate_with_review(review), name="review.json")
        return self.draft(
            experiment_id=experiment_id, groups=groups, candidate=candidate, retired=retired or self.retired_path
        )

    def test_missing_or_unreadable_source_saves_unavailable_not_no_match(self):
        directory = self.tmp / "retired-dir.json"
        directory.mkdir()
        for name, path in (("missing", self.tmp / "absent-retired.json"), ("directory", directory)):
            with self.subTest(case=name):
                experiment_id = f"{EXPERIMENT_ID}-{name}"
                result = self.draft(experiment_id=experiment_id, retired=path)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertTrue(report["saved"])
                retired = report["retired"]
                self.assertEqual(retired["status"], "unavailable")
                self.assertTrue(retired["reason"].strip())
                self.assertNotIn("entries", retired)
                self.assertNotIn("matches", retired)
                self.assertEqual(self.proposal(experiment_id)["candidate"]["retired"], retired)
                self.assertIn("retired_unavailable", report["freeze_ready_blockers"])
                self.assertFalse(report["freeze_ready"])

    def test_unreadable_source_with_host_review_is_rejected(self):
        candidate = self.write_candidate(candidate_with_review(), name="review-unreadable.json")
        result = self.draft(candidate=candidate, retired=self.tmp / "absent-retired.json")
        self.assert_rejected(result)
        self.assertIn("retired_review", result.stderr)

    def test_valid_source_is_listed_verbatim_with_byte_hash_and_scope(self):
        raw = self.retired_path.read_bytes()
        source = retired_document()
        result = self.draft_with_review(candidate_with_review()["retired_review"])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        retired = report["retired"]
        self.assertEqual(retired["status"], "completed")
        self.assertEqual(retired["scope"], "all_entries_in_supplied_file")
        self.assertEqual(retired["supplied_scope"], source["scope"])
        self.assertEqual(retired["source"]["path"], str(self.retired_path))
        self.assertEqual(retired["source"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(retired["entries"], source["entries"])
        self.assertEqual(report["retired_findings"], [])
        self.assertEqual(report["missing"], [])
        self.assertNotIn("retired_review_incomplete", report["freeze_ready_blockers"])
        self.assertFalse(report["freeze_ready"])
        self.assertIn("prediction_freeze_not_implemented", report["freeze_ready_blockers"])
        self.assertEqual(self.proposal()["candidate"]["retired"], retired)

    def test_empty_source_is_completed_but_requires_explicit_unrelated_review(self):
        empty = self.write_retired(
            {"schema_version": 1, "scope": "合成示例：提供者给出的文件里没有退役记录", "entries": []},
            name="empty.json",
        )
        candidate = self.write_candidate({"groups": [GROUP_A]}, name="no-review.json")
        result = self.draft(candidate=candidate, retired=empty)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retired"]["status"], "completed")
        self.assertEqual(report["retired"]["entries"], [])
        self.assertIn(f"{REVIEW_PREFIX}.relation", report["missing"])
        self.assertIn(f"{REVIEW_PREFIX}.reason", report["missing"])
        self.assertIn("retired_review_incomplete", report["freeze_ready_blockers"])
        self.assertFalse(report["freeze_ready"])

        review = {"relation": "unrelated", "reason": "该文件为空，本候选不沿用任何旧方法", "entries": []}
        candidate = self.write_candidate({"groups": [GROUP_A], "retired_review": review}, name="empty-reviewed.json")
        result = self.draft(experiment_id=f"{EXPERIMENT_ID}-empty", candidate=candidate, retired=empty)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertNotIn("retired_review_incomplete", report["freeze_ready_blockers"])
        self.assertEqual([item for item in report["missing"] if item.startswith(REVIEW_PREFIX)], [])
        self.assertFalse(report["freeze_ready"])

        reopen = self.write_candidate(
            {
                "groups": [GROUP_A],
                "retired_review": {"relation": "reopen", "reason": "没有记录也声明复开", "entries": []},
            },
            name="empty-reopen.json",
        )
        result = self.draft(experiment_id=f"{EXPERIMENT_ID}-empty-reopen", candidate=reopen, retired=empty)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.experiments / f"{EXPERIMENT_ID}-empty-reopen").exists())
        self.assertIn("unrelated", result.stderr)

    def test_malformed_or_invalid_source_is_rejected(self):
        entry = retired_document()["entries"][0]
        cases = {
            "broken-json": '{"schema_version": 1,',
            "top-level-list": [],
            "schema-version": {"schema_version": 2, "scope": "s", "entries": []},
            "missing-scope": {"schema_version": 1, "entries": []},
            "unknown-field": {"schema_version": 1, "scope": "s", "entries": [], "top_k": 5},
            "entry-unknown-field": {"schema_version": 1, "scope": "s", "entries": [dict(entry, matches=[])]},
            "entry-missing-method": {
                "schema_version": 1,
                "scope": "s",
                "entries": [
                    {
                        "retired_id": RETIRED_ID_A,
                        "rejected_reason": "r",
                        "retired_at": "2026-10-20T00:00:00Z",
                        "evidence_groups": [],
                    }
                ],
            },
            "retired-at-without-timezone": {
                "schema_version": 1,
                "scope": "s",
                "entries": [dict(entry, retired_at="2026-10-20T00:00:00")],
            },
            "retired-at-unparsable": {
                "schema_version": 1,
                "scope": "s",
                "entries": [dict(entry, retired_at="last-week")],
            },
            "evidence-groups-type": {
                "schema_version": 1,
                "scope": "s",
                "entries": [dict(entry, evidence_groups=OLD_GROUP)],
            },
            "evidence-groups-duplicate": {
                "schema_version": 1,
                "scope": "s",
                "entries": [dict(entry, evidence_groups=[OLD_GROUP, OLD_GROUP])],
            },
            "duplicate-retired-id": {"schema_version": 1, "scope": "s", "entries": [entry, entry]},
        }
        candidate = self.write_candidate(candidate_with_review(), name="review.json")
        for name, document in cases.items():
            with self.subTest(case=name):
                path = self.write_retired(document, name=f"{name}.json")
                self.assert_rejected(self.draft(candidate=candidate, retired=path))

    def test_records_without_host_review_are_saved_but_blocked(self):
        result = self.draft(retired=self.retired_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retired"]["status"], "completed")
        self.assertEqual(len(report["retired"]["entries"]), 2)
        self.assertIn(f"{REVIEW_PREFIX}.relation", report["missing"])
        self.assertIn(f"{REVIEW_PREFIX}.reason", report["missing"])
        self.assertIn(f"{REVIEW_PREFIX}.entries[{RETIRED_ID_A}]", report["missing"])
        self.assertIn(f"{REVIEW_PREFIX}.entries[{RETIRED_ID_B}]", report["missing"])
        self.assertIn("retired_review_incomplete", report["freeze_ready_blockers"])
        self.assertFalse(report["freeze_ready"])

    def test_unrelated_review_keeps_retrieved_records(self):
        review = self.review(
            "unrelated",
            [
                {"retired_id": RETIRED_ID_A, "relation": "unrelated", "reason": "对象不同"},
                {"retired_id": RETIRED_ID_B, "relation": "unrelated", "reason": "对象不同"},
            ],
        )
        result = self.draft_with_review(review)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([entry["retired_id"] for entry in report["retired"]["entries"]], [RETIRED_ID_A, RETIRED_ID_B])
        self.assertEqual(
            [entry["rejected_reason"] for entry in report["retired"]["entries"]],
            ["规则过长，同类纠正次数没有下降", "增加墙钟时间，测试通过数没有提升"],
        )
        self.assertEqual(report["retired_findings"], [])
        self.assertFalse(report["freeze_ready"])
        self.assertNotIn("retired_review_incomplete", report["freeze_ready_blockers"])
        self.assertNotIn("retired_new_evidence_required", report["freeze_ready_blockers"])

    def test_reopen_without_new_evidence_is_blocked(self):
        review = self.review(
            "reopen",
            [
                {"retired_id": RETIRED_ID_A, "relation": "reopen", "reason": "重新尝试"},
                {"retired_id": RETIRED_ID_B, "relation": "unrelated", "reason": "对象不同"},
            ],
        )
        result = self.draft_with_review(review)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("retired_new_evidence_required", report["freeze_ready_blockers"])
        self.assertTrue(any(RETIRED_ID_A in finding for finding in report["retired_findings"]))
        self.assertFalse(report["freeze_ready"])

    def test_reused_or_too_old_evidence_is_blocked(self):
        reused = self.review(
            "reopen",
            [
                {
                    "retired_id": RETIRED_ID_A,
                    "relation": "reopen",
                    "reason": "重新尝试",
                    "new_evidence_groups": [OLD_GROUP],
                }
            ],
        )
        document = json.loads(json.dumps(groups_document()))
        old = json.loads(json.dumps(document["groups"][0]))
        old["correlation_group"] = OLD_GROUP
        old["count"] = 1
        old["episodes"] = [old["episodes"][0]]
        old["episodes"][0]["ts"] = "2026-10-28T03:00:00Z"
        document["groups"].append(old)
        groups = self.write_groups(document, name="with-old-group.json")
        candidate = candidate_with_review(reused)
        candidate["groups"] = [GROUP_A, GROUP_B, OLD_GROUP]
        path = self.write_candidate(candidate, name="reused.json")
        result = self.draft(groups=groups, candidate=path, retired=self.retired_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("retired_evidence_group_reused", report["freeze_ready_blockers"])
        self.assertFalse(report["freeze_ready"])

        document = retired_document()
        document["entries"][0]["retired_at"] = "2026-10-29T00:00:00Z"
        late = self.write_retired(document, name="late-retired.json")
        too_old = self.review(
            "alternative",
            [
                {
                    "retired_id": RETIRED_ID_A,
                    "relation": "alternative",
                    "reason": "机制不同",
                    "new_evidence_groups": [GROUP_A],
                }
            ],
        )
        result = self.draft_with_review(too_old, experiment_id=f"{EXPERIMENT_ID}-late", retired=late)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("retired_evidence_not_after_retirement", report["freeze_ready_blockers"])
        self.assertFalse(report["freeze_ready"])

    def test_group_without_known_time_is_blocked(self):
        document = groups_document()
        document["groups"][0]["episodes"][0]["ts"] = None
        groups = self.write_groups(document, name="no-time-groups.json")
        review = self.review(
            "reopen",
            [
                {
                    "retired_id": RETIRED_ID_A,
                    "relation": "reopen",
                    "reason": "重新尝试",
                    "new_evidence_groups": [GROUP_A],
                }
            ],
        )
        result = self.draft_with_review(review, groups=groups)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("retired_evidence_time_unknown", report["freeze_ready_blockers"])
        self.assertFalse(report["freeze_ready"])

    def test_valid_new_evidence_is_accepted_but_not_freeze_ready(self):
        result = self.draft_with_review(candidate_with_review()["retired_review"])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([blocker for blocker in report["freeze_ready_blockers"] if blocker.startswith("retired_")], [])
        self.assertEqual(report["retired_findings"], [])
        self.assertFalse(report["freeze_ready"])
        self.assertIn("prediction_freeze_not_implemented", report["freeze_ready_blockers"])
        stored = self.proposal()["candidate"]["host_judgment"]["retired_review"]
        self.assertEqual(stored["relation"], "alternative")
        self.assertEqual(stored["entries"][0]["new_evidence_groups"], [GROUP_A])

    def test_forged_review_is_rejected(self):
        cases = {
            "unknown-retired-id": self.review(
                "unrelated", [{"retired_id": "retired-does-not-exist", "relation": "unrelated", "reason": "r"}]
            ),
            "unknown-group": self.review(
                "alternative",
                [
                    {
                        "retired_id": RETIRED_ID_A,
                        "relation": "alternative",
                        "reason": "r",
                        "new_evidence_groups": ["ffffffffffff"],
                    }
                ],
            ),
            "illegal-relation": self.review(
                "partially-related", [{"retired_id": RETIRED_ID_A, "relation": "unrelated", "reason": "r"}]
            ),
            "illegal-entry-relation": self.review(
                "unrelated", [{"retired_id": RETIRED_ID_A, "relation": "same", "reason": "r"}]
            ),
            "user-authorized": self.review(
                "reopen", [{"retired_id": RETIRED_ID_A, "relation": "reopen", "reason": "r", "user_authorized": True}]
            ),
            "forged-status": self.review(
                "unrelated", [{"retired_id": RETIRED_ID_A, "relation": "unrelated", "reason": "r"}], status="completed"
            ),
            "forged-matches": self.review(
                "unrelated",
                [{"retired_id": RETIRED_ID_A, "relation": "unrelated", "reason": "r"}],
                matches=[{"retired_id": RETIRED_ID_A}],
            ),
            "duplicate-review-id": self.review(
                "unrelated",
                [
                    {"retired_id": RETIRED_ID_A, "relation": "unrelated", "reason": "r"},
                    {"retired_id": RETIRED_ID_A, "relation": "unrelated", "reason": "r"},
                ],
            ),
            "review-type": "no_match",
            "unrelated-with-reopen-entry": self.review(
                "unrelated",
                [
                    {"retired_id": RETIRED_ID_A, "relation": "reopen", "reason": "r"},
                    {"retired_id": RETIRED_ID_B, "relation": "unrelated", "reason": "r"},
                ],
            ),
        }
        for name, review in cases.items():
            with self.subTest(case=name):
                candidate = self.write_candidate(candidate_with_review(review), name=f"{name}.json")
                self.assert_rejected(self.draft(candidate=candidate, retired=self.retired_path))

    def test_review_without_retired_source_is_rejected(self):
        candidate = self.write_candidate(candidate_with_review(), name="review-no-source.json")
        result = self.draft(candidate=candidate)
        self.assert_rejected(result)
        self.assertIn("retired", result.stderr)

    def test_examples_m3a_samples_run_end_to_end(self):
        examples = self.repo / "examples/m3a"
        result = self.cli(
            "draft",
            "--groups",
            str(examples / "groups.sample.json"),
            "--candidate",
            str(examples / "candidate.sample.json"),
            "--retired",
            str(examples / "retired.sample.json"),
            "--experiment-id",
            "m3a-b1-example",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retired_findings"], [])
        self.assertEqual(report["missing"], [])
        self.assertFalse(report["freeze_ready"])
        stored = self.proposal("m3a-b1-example")["candidate"]["retired"]
        self.assertEqual(
            [entry["retired_id"] for entry in stored["entries"]],
            ["retired-format-guard-001", "retired-checklist-002"],
        )


if __name__ == "__main__":
    unittest.main()
