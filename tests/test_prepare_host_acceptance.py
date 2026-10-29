"""M2 合成任务夹具：本地任务/patch/规则/引用核验，不调用模型或 run/judge。"""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_host_acceptance", ROOT / "tools/m2/prepare_host_acceptance.py")
assert SPEC is not None and SPEC.loader is not None
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class HostAcceptanceFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phe-host-fixture-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proposal = prepare.generate(self.tmp / "corpus", "m2-test")
        self.inputs = self.proposal.parent
        self.target = self.tmp / "corpus/target"

    def test_structure_has_no_nulls_but_reports_formal_blockers(self):
        report = prepare.check(self.proposal)
        self.assertTrue(report["structural_ok"], report)
        self.assertFalse(report["formal_freeze_ready"])
        self.assertEqual(len(report["formal_blockers"]), 5)
        self.assertEqual(prepare.null_fields(json.loads(self.proposal.read_text())), [])
        self.assertFalse(list((self.tmp / "corpus/repo/experiments/m2-test").iterdir()))

    def test_patch_repairs_task_and_oracle_rejects_broken_start(self):
        def oracle():
            return subprocess.run(
                [sys.executable, "-I", "-B", "-c", "import sys; sys.path.insert(0, '.'); " + prepare.ORACLE],
                cwd=self.target,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(oracle().returncode, 0)
        prepare.git(self.target, "apply", "--check", str(self.inputs / "known-good.patch"))
        prepare.git(self.target, "apply", str(self.inputs / "known-good.patch"))
        result = oracle()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "calc.py").read_text(), prepare.FIXED)

    def test_variants_remove_only_the_named_rule_and_nonce_hash_matches(self):
        baseline = (self.inputs / "harness/baseline.md").read_text()
        required = (self.inputs / "rules/required.md").read_text()
        self.assertEqual((self.inputs / "harness/A1.md").read_text(), baseline)
        self.assertEqual((self.inputs / "harness/A2.md").read_text(), baseline)
        self.assertEqual((self.inputs / "harness/B_real.md").read_text(), baseline.replace(prepare.REAL, "", 1))
        self.assertEqual((self.inputs / "harness/B_required.md").read_text(), baseline.replace(required, "", 1))
        self.assertEqual(baseline, (self.inputs / "harness/B_redundant.md").read_text() + prepare.REDUNDANT)
        p = json.loads(self.proposal.read_text())
        cfg = json.loads((self.inputs / "config.json").read_text())
        self.assertEqual(cfg["nonce_sha256"], p["rules"]["required_control"]["nonce_sha256"])

    def test_mutations_are_reported_without_changing_proposal(self):
        original = self.proposal.read_bytes()
        (self.inputs / "harness/A1.md").write_text("tampered")
        self.assertIn("hash: harness.variants.A1.snapshot_sha256", prepare.check(self.proposal)["problems"])
        self.assertEqual(self.proposal.read_bytes(), original)
        p = json.loads(original)
        p["target"]["model_id"] = None
        p["experiment"]["schedule"][0]["slot"] = 0
        self.proposal.write_text(json.dumps(p))
        problems = prepare.check(self.proposal)["problems"]
        self.assertIn("null: target.model_id", problems)
        self.assertTrue(any(x.startswith("schedule:") for x in problems))

    def test_existing_root_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            prepare.generate(self.tmp / "corpus", "m2-test")
        self.assertTrue(prepare.check(self.proposal)["structural_ok"])


if __name__ == "__main__":
    unittest.main()
