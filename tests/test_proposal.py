"""`tools/proposal.py draft` 的契约：证据只从分组 JSON 解析、缺项报告、拒绝与不覆盖。

夹具在 `proposal_fixture.py`：合成输入、临时仓库副本和真实 CLI 调用。这里只覆盖保存路径和
draft 阶段的拒绝；`check` 的契约在 `test_proposal_check.py`。
"""

import hashlib
import json
import unittest

from proposal_fixture import (
    CLAUDE_PATH,
    EXCERPT_SENTINEL,
    EXPERIMENT_ID,
    GROUP_A,
    GROUP_B,
    SESSION_A,
    TEMPLATE,
    RepoCopyCase,
    groups_document,
    valid_candidate,
)


class DraftSaveTests(RepoCopyCase):
    def test_valid_candidate_saves_complete_template_shape(self):
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["action"], "draft")
        self.assertTrue(report["saved"])
        self.assertFalse(report["freeze_ready"])
        self.assertEqual(report["missing"], [])
        self.assertIn("retired_unavailable", report["freeze_ready_blockers"])

        proposal = self.proposal()
        self.assertEqual(set(proposal), set(TEMPLATE) | {"candidate"})
        self.assertEqual(proposal["metrics"], TEMPLATE["metrics"])
        self.assertEqual(proposal["spec_version"], TEMPLATE["spec_version"])
        self.assertEqual(proposal["experiment_id"], EXPERIMENT_ID)
        self.assertEqual(proposal["status"], "draft")
        self.assertEqual(proposal["paths"]["artifact_root_relative_to_phe_repo"], f"experiments/{EXPERIMENT_ID}")

    def test_evidence_comes_from_groups_file_and_never_copies_excerpt(self):
        groups_path = self.write_groups(groups_document())
        raw = groups_path.read_bytes()
        self.assertEqual(self.draft().returncode, 0)
        proposal = self.proposal()
        evidence = proposal["candidate"]["evidence"]
        self.assertEqual(evidence["groups_file"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(evidence["groups_file"]["path"], str(groups_path))
        self.assertEqual(evidence["selected_group_ids"], [GROUP_A, GROUP_B])
        first = evidence["groups"][0]
        self.assertEqual(first["correlation_group"], GROUP_A)
        self.assertEqual(first["count"], 2)
        self.assertEqual(
            first["pointers"],
            [
                {"source_path": CLAUDE_PATH, "line": 3, "ts": "2026-10-28T01:00:00Z"},
                {"source_path": CLAUDE_PATH, "line": 9, "ts": "2026-10-28T01:00:05Z"},
            ],
        )
        text = (self.experiments / EXPERIMENT_ID / "proposal.json").read_text(encoding="utf-8")
        self.assertNotIn(EXCERPT_SENTINEL, text)
        self.assertNotIn('"excerpt"', text)

    def test_candidate_marks_host_judgment_and_unavailable_retired(self):
        self.assertEqual(self.draft().returncode, 0)
        candidate = self.proposal()["candidate"]
        self.assertEqual(candidate["host_judgment"]["source"], "host_judgment_not_measurement")
        self.assertEqual(
            candidate["retired"],
            {"status": "unavailable", "reason": "未提供 --retired 文件，本工具不做自动检索或扫描"},
        )
        self.assertNotIn("matches", candidate["retired"])

    def test_missing_hypothesis_and_predictions_are_reported_but_saved(self):
        path = self.write_candidate({"groups": [GROUP_A]}, name="sparse.json")
        result = self.draft(candidate=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["saved"])
        self.assertFalse(report["freeze_ready"])
        self.assertIn("candidate.host_judgment.hypothesis.observation", report["missing"])
        self.assertIn("candidate.host_judgment.hypothesis.change", report["missing"])
        self.assertIn("candidate.host_judgment.hypothesis.expected_effect", report["missing"])
        self.assertIn("candidate.host_judgment.predictions", report["missing"])
        self.assertIn("candidate_incomplete", report["freeze_ready_blockers"])
        judgment = self.proposal()["candidate"]["host_judgment"]
        self.assertEqual(judgment["hypothesis"], {"observation": None, "change": None, "expected_effect": None})
        self.assertEqual(judgment["predictions"], [])

    def test_partial_prediction_fields_are_listed(self):
        candidate = valid_candidate()
        candidate["predictions"] = [{"metric": "test_pass_v1"}]
        path = self.write_candidate(candidate, name="partial.json")
        result = self.draft(candidate=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        missing = json.loads(result.stdout)["missing"]
        self.assertIn("candidate.host_judgment.predictions[0].expected_change", missing)
        self.assertIn("candidate.host_judgment.predictions[0].falsified_when", missing)

    def test_template_null_fields_are_reported_recursively(self):
        self.assertEqual(self.draft().returncode, 0)
        report = json.loads(self.draft(experiment_id="m3a-demo-002").stdout)
        self.assertIn("paths.phe_repo_abs", report["null_fields"])
        self.assertIn("target.cli_version", report["null_fields"])
        self.assertIn("template_null_fields", report["freeze_ready_blockers"])
        self.assertTrue(all(not path.startswith("candidate") for path in report["null_fields"]))


class DraftRejectTests(RepoCopyCase):
    def assert_rejected(self, result, code):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_no_artifact()

    def test_unknown_metric_is_rejected(self):
        candidate = valid_candidate()
        candidate["predictions"][0]["metric"] = "made_up_v1"
        path = self.write_candidate(candidate, name="bad-metric.json")
        result = self.draft(candidate=path)
        self.assert_rejected(result, 1)
        self.assertIn("metric", result.stderr)

    def test_nonexistent_group_is_rejected(self):
        candidate = valid_candidate()
        candidate["groups"] = ["ffffffffffff"]
        path = self.write_candidate(candidate, name="bad-group.json")
        result = self.draft(candidate=path)
        self.assert_rejected(result, 1)
        self.assertIn("不存在", result.stderr)

    def test_ambiguous_group_id_is_rejected(self):
        document = groups_document()
        twin = json.loads(json.dumps(document["groups"][0]))
        twin["kind"] = "retry"
        document["groups"].append(twin)
        groups = self.write_groups(document, name="ambiguous.json")
        result = self.draft(groups=groups)
        self.assert_rejected(result, 1)
        self.assertIn("歧义", result.stderr)

    def test_malformed_inputs_are_rejected(self):
        cases = {
            "groups-json": ("groups", '{"groups": ['),
            "groups-top-level": ("groups", "[]"),
            "groups-missing-episodes": ("groups", {"groups": [{"correlation_group": GROUP_A, "host": "claude"}]}),
            "groups-bad-line": (
                "groups",
                {
                    "groups": [
                        {
                            "correlation_group": GROUP_A,
                            "host": "claude",
                            "session_id": SESSION_A,
                            "kind": "user_correction",
                            "episodes": [{"source_path": CLAUDE_PATH, "line": 0, "ts": None}],
                        }
                    ]
                },
            ),
            "candidate-json": ("candidate", "{not json"),
            "candidate-groups-empty": ("candidate", {"groups": []}),
            "candidate-groups-type": ("candidate", {"groups": "aaaaaaaaaaaa"}),
            "candidate-hypothesis-type": ("candidate", {"groups": [GROUP_A], "hypothesis": "观察"}),
            "candidate-observation-type": (
                "candidate",
                {"groups": [GROUP_A], "hypothesis": {"observation": 5}},
            ),
            "candidate-predictions-type": ("candidate", {"groups": [GROUP_A], "predictions": {}}),
            "candidate-prediction-type": ("candidate", {"groups": [GROUP_A], "predictions": ["test_pass_v1"]}),
            "candidate-unknown-field": (
                "candidate",
                {"groups": [GROUP_A], "evidence": [{"source_path": CLAUDE_PATH, "line": 3}]},
            ),
            "candidate-retired-claim": (
                "candidate",
                {"groups": [GROUP_A], "retired": {"status": "searched", "matches": []}},
            ),
        }
        for name, (kind, document) in cases.items():
            with self.subTest(case=name):
                if kind == "groups":
                    path = self.write_groups(document, name=f"{name}.json")
                    result = self.draft(groups=path)
                else:
                    path = self.write_candidate(document, name=f"{name}.json")
                    result = self.draft(candidate=path)
                self.assert_rejected(result, 1)

    def test_path_traversal_is_rejected(self):
        for experiment_id in ("../escape", "..", "a/b", ".hidden", ""):
            with self.subTest(experiment_id=experiment_id):
                result = self.draft(experiment_id=experiment_id)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((self.tmp / "escape").exists())
                self.assertFalse((self.repo / "escape").exists())
                self.assertFalse(self.experiments.exists())

    def test_symlinked_experiments_directory_is_rejected(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        self.experiments.symlink_to(outside, target_is_directory=True)
        result = self.draft()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_or_frozen_directory_is_not_overwritten(self):
        exp = self.experiments / EXPERIMENT_ID
        exp.mkdir(parents=True)
        proposal = exp / "proposal.json"
        proposal.write_text('{"experiment_id": "frozen", "status": "draft"}', encoding="utf-8")
        (exp / "proposal.sha256").write_text("0" * 64 + "\n", encoding="utf-8")
        before = self.snapshot()
        result = self.draft()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(proposal.read_text(encoding="utf-8"), '{"experiment_id": "frozen", "status": "draft"}')
        self.assertEqual(self.snapshot(), before)

    def test_existing_empty_directory_is_not_overwritten(self):
        (self.experiments / EXPERIMENT_ID).mkdir(parents=True)
        result = self.draft()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(list((self.experiments / EXPERIMENT_ID).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
