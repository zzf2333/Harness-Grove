"""M2 合成任务夹具：本地任务/patch/规则/引用核验，不调用模型或 run/judge。"""

import importlib.util
import json
import os
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


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.stat().st_mode,
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *sorted(root.rglob("*"))]
    }


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

    def test_deleted_required_fields_are_rejected(self):
        original = json.loads(self.proposal.read_text())
        for field in ("target.model_id", "freeze.approval_user_supplied", "task.oracle_files_sha256"):
            with self.subTest(field=field):
                p = json.loads(json.dumps(original))
                parent, key = field.rsplit(".", 1)
                del p[parent][key]
                self.proposal.write_text(json.dumps(p))
                before = snapshot(self.tmp / "corpus")
                self.assertFalse(prepare.check(self.proposal)["structural_ok"])
                self.assertEqual(snapshot(self.tmp / "corpus"), before)

    def test_reference_manifest_cannot_remove_or_redirect_required_checks(self):
        manifest = self.inputs / "references.json"
        original = json.loads(manifest.read_text())
        field = "harness.variants.A1.snapshot_sha256"
        (self.inputs / "harness/A1.md").write_text("tampered")
        for mutation in ("missing", "redirected", "extra"):
            with self.subTest(mutation=mutation):
                refs = dict(original)
                if mutation == "missing":
                    del refs[field]
                elif mutation == "redirected":
                    refs[field] = "harness/A2.md"
                else:
                    refs["unsupported.hash"] = "harness/A2.md"
                manifest.write_text(json.dumps(refs))
                before = snapshot(self.tmp / "corpus")
                report = prepare.check(self.proposal)
                self.assertFalse(report["structural_ok"], report)
                self.assertIn("hash: " + field, report["problems"])
                self.assertEqual(snapshot(self.tmp / "corpus"), before)

    def test_oracle_hashes_and_exact_file_set_are_checked(self):
        original = json.loads(self.proposal.read_text())
        oracle = self.inputs / "oracle.py"
        for mutation in ("content", "hash", "missing", "extra"):
            with self.subTest(mutation=mutation):
                p = json.loads(json.dumps(original))
                oracle.write_text(prepare.ORACLE)
                if mutation == "content":
                    oracle.write_text("tampered")
                    p["task"]["oracle_sha256"] = prepare.digest(oracle)
                elif mutation == "hash":
                    p["task"]["oracle_files_sha256"][str(oracle)] = "0" * 64
                elif mutation == "missing":
                    p["task"]["oracle_files_sha256"] = {}
                else:
                    p["task"]["oracle_files_sha256"]["unsupported.py"] = "0" * 64
                self.proposal.write_text(json.dumps(p))
                before = snapshot(self.tmp / "corpus")
                self.assertFalse(prepare.check(self.proposal)["structural_ok"])
                self.assertEqual(snapshot(self.tmp / "corpus"), before)

    def test_cli_generate_and_check_leave_source_and_checked_tree_unchanged(self):
        source = self.tmp / "source"
        for rel in ("tools", "docs", "skill", "examples"):
            shutil.copytree(ROOT / rel, source / rel, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        script = source / "tools/m2/prepare_host_acceptance.py"
        source_before = snapshot(source)
        generated = self.tmp / "cli-corpus"
        result = subprocess.run(
            [sys.executable, "-I", str(script), "--root", str(generated)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(source), source_before)
        # 用复制后的入口启动新进程，连同 inputs、target 和 Git 元数据一起核对。
        before = snapshot(generated)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(generated / "repo/tools/m2/prepare_host_acceptance.py"),
                "--check",
                str(generated / "inputs/proposal.json"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(source), source_before)
        self.assertEqual(snapshot(generated), before)
        proposal = generated / "inputs/proposal.json"
        p = json.loads(proposal.read_text())
        del p["target"]["model_id"]
        proposal.write_text(json.dumps(p))
        before = snapshot(generated)
        result = subprocess.run(
            [sys.executable, "-I", str(script), "--check", str(proposal)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("missing: target.model_id", json.loads(result.stdout)["problems"])
        self.assertEqual(snapshot(source), source_before)
        self.assertEqual(snapshot(generated), before)
        self.assertFalse(list(source.rglob("*.pyc")))
        self.assertFalse(list(generated.rglob("*.pyc")))

    def test_git_injection_cannot_modify_external_temporary_repository(self):
        external = self.tmp / "external"
        external.mkdir()
        (external / "sentinel.txt").write_text("untouched\n")
        prepare.git(external, "init", "-q")
        prepare.git(external, "add", "sentinel.txt")
        prepare.git(external, "commit", "-qm", "external sentinel")
        config = self.tmp / "injected.gitconfig"
        config.write_text("[core]\n\tbare = true\n")
        template = self.tmp / "template"
        (template / "hooks").mkdir(parents=True)
        hook = template / "hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 91\n")
        hook.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            {
                "GIT_DIR": str(external / ".git"),
                "GIT_WORK_TREE": str(external),
                "GIT_COMMON_DIR": str(external / ".git"),
                "GIT_INDEX_FILE": str(external / ".git/index"),
                "GIT_OBJECT_DIRECTORY": str(external / ".git/objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(external / ".git/objects"),
                "GIT_CONFIG": str(config),
                "GIT_CONFIG_SYSTEM": str(config),
                "GIT_CONFIG_GLOBAL": str(config),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.bare",
                "GIT_CONFIG_VALUE_0": "true",
                "GIT_CONFIG_PARAMETERS": "'core.bare=true'",
                "GIT_TEMPLATE_DIR": str(template),
            }
        )
        before = snapshot(external)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "tools/m2/prepare_host_acceptance.py"),
                "--root",
                str(self.tmp / "injection-corpus"),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(snapshot(external), before)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["structural_ok"])

    def test_existing_root_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            prepare.generate(self.tmp / "corpus", "m2-test")
        self.assertTrue(prepare.check(self.proposal)["structural_ok"])


if __name__ == "__main__":
    unittest.main()
