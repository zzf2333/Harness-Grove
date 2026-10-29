"""真实 runner 入口 + 本地假 Target：Codex JSONL 的成功/失败/截断与冻结绑定。

假 Target 只打印按源码契约手写的 JSONL，不联网、不调用模型；这些用例不是真实 Codex
会话，也不证明 `codex exec` 在当前隔离下能跑完。
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import test_phe
from codex_fixture import USAGE, ZERO_USAGE, completed, event, message

phe = test_phe.phe
ROOT = Path(__file__).resolve().parents[1]


class RunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-codex-run-")
        self.addCleanup(temp.cleanup)
        self.exp = Path(temp.name)
        self.base = self.exp / "base"
        self.base.mkdir()
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        (self.base / "task.txt").write_text("fixture")
        self.p = test_phe.proposal(count=1, controls=False)
        del self.p["rules"]["required_control"]
        self.p["target"].update({"agent": "codex", "model_id": "frozen-model-x"})
        self.cfg: dict[str, Any] = {"harness": {}}
        for group, variant in self.p["harness"]["variants"].items():
            harness = self.exp / (group + ".md")
            harness.write_text("synthetic harness")
            self.cfg["harness"][group] = str(harness)
            variant["snapshot_sha256"] = phe.sha(harness)
        trusted = self.exp / "trusted.txt"
        trusted.write_text("oracle fixture")
        self.cfg.update(
            {
                "base_dir": str(self.base),
                "experiment_id": "synthetic-codex",
                "experiment_dir": str(self.exp),
                "prompt": "do the task",
                "target_agent": "codex",
                "env": {},
                "oracle_test_src": str(trusted),
                "oracle_test_dest": "oracle.txt",
                "oracle_cwd": ".",
                "oracle_argv": [sys.executable, "-I", "-c", "print('Tests 1 passed')"],
                "nonce_sha256": "unused",
            }
        )
        self.addCleanup(self.clean_workdirs)

    def clean_workdirs(self):
        for record in phe.records(self.exp):
            shutil.rmtree(Path(record["workdir"]).parent, ignore_errors=True)

    def freeze(self, runner_hashes: dict | None = None, *, omit_runner_hashes: bool = False):
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        runner = {"config_sha256": phe.sha(self.exp / "config.json")}
        if not omit_runner_hashes:
            runner["runner_files_sha256"] = runner_hashes or phe.runner_file_hashes()
        self.p["runner"] = runner
        (self.exp / "proposal.json").write_text(json.dumps(self.p))
        digest = phe.sha(self.exp / "proposal.json")
        (self.exp / "proposal.sha256").write_text(digest)
        (self.exp / "approval.json").write_text(json.dumps({"proposal_sha256": digest}))

    def run_fake(self, body: str, exit_code: int = 0) -> subprocess.CompletedProcess:
        fake = self.exp / "target.py"
        fake.write_text(body + f"\nraise SystemExit({exit_code})\n")
        self.cfg["target_argv"] = [sys.executable, "-I", str(fake)]
        self.freeze()
        return subprocess.run(
            [sys.executable, "-I", str(ROOT / "tools/phe.py"), "run", str(self.exp)],
            capture_output=True,
            text=True,
            check=False,
        )

    def one_record(self) -> dict:
        records = phe.records(self.exp)
        self.assertEqual(len(records), 1)
        return records[0]

    def test_success_tokens_cost_none_and_explicit_pivot(self):
        body = "\n".join(
            [
                "import json",
                f"print(json.dumps({event('thread.started', thread_id='t1')!r}))",
                f"print(json.dumps({event('turn.started')!r}))",
                f"print(json.dumps({message('done')!r}))",
                f"print(json.dumps({completed(USAGE)!r}))",
            ]
        )
        completed_run = self.run_fake(body)
        self.assertEqual(completed_run.returncode, 0, completed_run.stderr)
        record = self.one_record()
        self.assertEqual(record[phe.TOK], 110)
        self.assertIsNone(record["estimated_cost_usd_v1"])
        self.assertEqual(record["target_agent"], "codex")
        self.assertEqual(record["turn_status"], "completed")
        self.assertEqual(record["final_message"], "done")
        self.assertEqual(record["num_turns"], 1)
        self.assertEqual(record["num_turns_basis"], "codex_turn_started_events")
        self.assertFalse(record["is_error"])
        self.assertEqual(record["usage"], USAGE)
        self.assertIsNone(record["parse_error"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")
        art = self.exp / "artifacts/slot-01"
        self.assertIn(b"turn.completed", (art / "stdout.json").read_bytes())
        self.assertTrue((art / "stderr.txt").exists())
        launch = json.loads((art / "argv.json").read_text())
        self.assertEqual(launch["target_agent"], "codex")
        self.assertTrue(launch["target_argv_custom"])
        self.assertEqual(launch["argv"], self.cfg["target_argv"])
        self.assertEqual(record["argv_sha256"], phe.argv_digest(self.cfg["target_argv"]))

    def test_failure_preserves_error_and_missing_usage(self):
        body = "\n".join(
            [
                "import json",
                f"print(json.dumps({event('turn.started')!r}))",
                f"print(json.dumps({event('turn.failed', error={'message': 'boom'})!r}))",
            ]
        )
        completed_run = self.run_fake(body, exit_code=1)
        self.assertEqual(completed_run.returncode, 0, completed_run.stderr)
        record = self.one_record()
        self.assertEqual(record["turn_status"], "failed")
        self.assertTrue(record["is_error"])
        self.assertEqual(record["error_message"], "boom")
        self.assertIsNone(record[phe.TOK])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_truncated_json_is_invalid_and_not_zero_filled(self):
        body = "\n".join(
            [
                "import json",
                f"print(json.dumps({event('turn.started')!r}))",
                f"print(json.dumps({completed(USAGE)!r}))",
                "print('{\"type\":')",
            ]
        )
        completed_run = self.run_fake(body)
        self.assertEqual(completed_run.returncode, 0, completed_run.stderr)
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertIsNotNone(record["parse_error"])
        self.assertTrue(record["is_error"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_missing_usage_is_invalid(self):
        body = "\n".join(
            [
                "import json",
                f"print(json.dumps({event('turn.started')!r}))",
                f"print(json.dumps({completed()!r}))",
            ]
        )
        completed_run = self.run_fake(body)
        self.assertEqual(completed_run.returncode, 0, completed_run.stderr)
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertIn("usage", record["parse_error"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_all_zero_usage_is_not_accepted(self):
        body = "\n".join(
            [
                "import json",
                f"print(json.dumps({event('turn.started')!r}))",
                f"print(json.dumps({completed(ZERO_USAGE)!r}))",
            ]
        )
        completed_run = self.run_fake(body)
        self.assertEqual(completed_run.returncode, 0, completed_run.stderr)
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertTrue(record["usage_all_zero"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_nonzero_exit_keeps_error_with_complete_telemetry(self):
        body = "\n".join(
            [
                "import json",
                f"print(json.dumps({event('turn.started')!r}))",
                f"print(json.dumps({completed(USAGE)!r}))",
            ]
        )
        completed_run = self.run_fake(body, exit_code=1)
        self.assertEqual(completed_run.returncode, 0, completed_run.stderr)
        record = self.one_record()
        self.assertTrue(record["is_error"])
        self.assertEqual(record[phe.TOK], 110)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_config_target_agent_conflict_refuses_before_target(self):
        fake = self.exp / "target.py"
        fake.write_text("print('never')")
        self.cfg["target_argv"] = [sys.executable, "-I", str(fake)]
        self.cfg["target_agent"] = "claude-code"
        self.freeze()
        completed_run = subprocess.run(
            [sys.executable, "-I", str(ROOT / "tools/phe.py"), "run", str(self.exp)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed_run.returncode, 1)
        self.assertIn("冲突", completed_run.stderr)
        self.assertFalse((self.exp / "stop.json").exists())
        self.assertFalse((self.exp / "artifacts").exists())

    def test_codex_experiment_requires_full_runner_hash_freeze(self):
        fake = self.exp / "target.py"
        fake.write_text("print('never')")
        self.cfg["target_argv"] = [sys.executable, "-I", str(fake)]
        hashes = phe.runner_file_hashes()
        legacy = {"phe.py": hashes["phe.py"], "phe_sandbox.py": hashes["phe_sandbox.py"]}
        for name, runner_hashes, omit in (
            ("missing", None, True),
            ("legacy", legacy, False),
        ):
            with self.subTest(case=name):
                self.freeze(runner_hashes=runner_hashes, omit_runner_hashes=omit)
                (self.exp / "stop.json").unlink(missing_ok=True)
                subprocess.run(
                    [sys.executable, "-I", str(ROOT / "tools/phe.py"), "run", str(self.exp)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(phe.stop_reason(self.exp), "invalid_run:sandbox_runner_hash_missing")
                self.assertFalse((self.exp / "artifacts").exists())

    def test_runner_hashes_include_the_codex_module(self):
        hashes = phe.runner_file_hashes()
        self.assertEqual(set(hashes), {"phe.py", "phe_sandbox.py", "phe_target.py"})
        self.assertEqual(hashes["phe_target.py"], phe.sha256_file(ROOT / "tools/phe_target.py"))

    def test_old_claude_sandbox_experiment_keeps_two_file_hash_scope(self):
        self.p["target"].update({"agent": "claude-code", "model_id": None})
        self.cfg["target_agent"] = "claude-code"
        self.cfg["sandbox"] = {"mechanism": "macos-sandbox-exec"}
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        hashes = phe.runner_file_hashes()
        legacy = {"phe.py": hashes["phe.py"], "phe_sandbox.py": hashes["phe_sandbox.py"]}
        self.p["runner"] = {"config_sha256": phe.sha(self.exp / "config.json"), "runner_files_sha256": legacy}
        self.assertIsNone(phe.integrity_problem(self.p, self.cfg, self.exp))
        self.p["runner"]["runner_files_sha256"] = dict(legacy, **{"phe_sandbox.py": "0" * 64})
        self.assertEqual(phe.integrity_problem(self.p, self.cfg, self.exp), "sandbox_runner_hash_changed")

    def test_phe_stays_below_twenty_kilobytes(self):
        self.assertLess((ROOT / "tools/phe.py").stat().st_size, 20 * 1024)


if __name__ == "__main__":
    unittest.main()
