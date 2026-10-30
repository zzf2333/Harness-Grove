"""tools/proposal.py draft/check 的契约：证据只从分组 JSON 解析、缺项报告、拒绝与不覆盖。

所有输入都是手写合成材料，路径和名字都是虚构的（`/home/alice/...`），不来自任何真实会话。
仓库根由 `tools/proposal.py` 的源码位置推导，所以测试把 `tools/` 和 `examples/` 复制到临时仓库，
用真实 CLI 跑，从不写本仓库的 `experiments/`。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "examples/m-1/proposal.template.json").read_text(encoding="utf-8"))
EXPERIMENT_ID = "m3a-demo-001"
PROJECT = "/home/alice/proj"
SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"
CLAUDE_PATH = "/home/alice/.claude/projects/-home-alice-proj/aaaa.jsonl"
CODEX_PATH = "/home/alice/.codex/sessions/2026/10/28/rollout-bbbb.jsonl"
GROUP_A = "aaaaaaaaaaaa"
GROUP_B = "bbbbbbbbbbbb"
EXCERPT_SENTINEL = "SENTINEL-EXCERPT-不应进入提案"
REPO_FILES = ("tools", "examples")


def groups_document():
    return {
        "groups": [
            {
                "correlation_group": GROUP_A,
                "host": "claude",
                "session_id": SESSION_A,
                "project": PROJECT,
                "kind": "user_correction",
                "count": 2,
                "first_ts": "2026-10-28T01:00:00Z",
                "last_ts": "2026-10-28T01:00:05Z",
                "episodes": [
                    {"source_path": CLAUDE_PATH, "line": 3, "ts": "2026-10-28T01:00:00Z", "excerpt": EXCERPT_SENTINEL},
                    {"source_path": CLAUDE_PATH, "line": 9, "ts": "2026-10-28T01:00:05Z", "excerpt": EXCERPT_SENTINEL},
                ],
                "same_project": [],
            },
            {
                "correlation_group": GROUP_B,
                "host": "codex",
                "session_id": SESSION_B,
                "project": PROJECT,
                "kind": "retry",
                "count": 1,
                "first_ts": "2026-10-28T02:00:00Z",
                "last_ts": "2026-10-28T02:00:00Z",
                "episodes": [
                    {"source_path": CODEX_PATH, "line": 4, "ts": "2026-10-28T02:00:00Z", "excerpt": EXCERPT_SENTINEL}
                ],
                "same_project": [],
            },
        ],
        "sources_unavailable": [],
    }


def valid_candidate():
    return {
        "groups": [GROUP_A, GROUP_B],
        "hypothesis": {
            "observation": "同一会话里反复出现同类纠正",
            "change": "把该约束写成一条更短的规则",
            "expected_effect": "同类纠正减少，测试通过数不下降",
        },
        "predictions": [
            {
                "metric": "test_pass_v1",
                "expected_change": "不下降",
                "falsified_when": "处理组比基线低 2 次以上",
            }
        ],
    }


class RepoCopyCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-proposal-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.repo = self.tmp / "repo"
        for relative in REPO_FILES:
            shutil.copytree(
                ROOT / relative,
                self.repo / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        self.experiments = self.repo / "experiments"
        self.groups_path = self.tmp / "groups.json"
        self.candidate_path = self.tmp / "candidate.json"
        self.write_groups(groups_document())
        self.write_candidate(valid_candidate())

    def write_groups(self, document, name="groups.json"):
        path = self.tmp / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if name == "groups.json":
            self.groups_path = path
        return path

    def write_candidate(self, document, name="candidate.json"):
        path = self.tmp / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if name == "candidate.json":
            self.candidate_path = path
        return path

    def cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-I", str(self.repo / "tools/proposal.py"), *argv],
            cwd=self.tmp,
            capture_output=True,
            text=True,
            check=False,
        )

    def draft(self, experiment_id=EXPERIMENT_ID, groups=None, candidate=None):
        return self.cli(
            "draft",
            "--groups",
            str(groups or self.groups_path),
            "--candidate",
            str(candidate or self.candidate_path),
            "--experiment-id",
            experiment_id,
        )

    def check(self, experiment_id=EXPERIMENT_ID):
        return self.cli("check", "--experiment-id", experiment_id)

    def proposal(self, experiment_id=EXPERIMENT_ID):
        return json.loads((self.experiments / experiment_id / "proposal.json").read_text(encoding="utf-8"))

    def assert_no_artifact(self, experiment_id=EXPERIMENT_ID):
        self.assertFalse((self.experiments / experiment_id).exists())
        self.assertFalse(self.experiments.exists())

    def snapshot(self):
        # 解释器可能写 __pycache__，那不是本工具写出的产物。
        return {
            str(path.relative_to(self.repo)): b"<dir>" if path.is_dir() else path.read_bytes()
            for path in sorted(self.repo.rglob("*"))
            if "__pycache__" not in path.parts and path.suffix != ".pyc"
        }


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
            {"status": "unavailable", "reason": "尚无Retired数据源及检索实现"},
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
        path = self.experiments / EXPERIMENT_ID / "proposal.json"
        proposal = json.loads(path.read_text(encoding="utf-8"))
        proposal["candidate"]["evidence"]["selected_group_ids"] = [GROUP_B, GROUP_A]
        path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
