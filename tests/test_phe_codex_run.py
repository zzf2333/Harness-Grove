"""真实 runner 入口 + 测试层替换 subprocess 边界：Codex JSONL 语义与冻结绑定。

Codex 路径不接受 `config.target_argv` 覆盖，也没有本地可执行入口；这里在测试层替换
`phe_sandbox.run_with_timeout`，runner 仍按规范 argv 调用，oracle 仍真实执行。合成 JSONL
不是真实 `codex exec` 会话证据。
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_phe
from codex_fixture import USAGE, ZERO_USAGE, completed, event, jsonl, message

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

    def run_fake(self, stdout: bytes, exit_code: int = 0) -> list[list[str]]:
        """跑真实 runner 入口；Codex 调用返回合成输出，oracle 仍真实执行。"""
        self.freeze()
        real = phe.phe_sandbox.run_with_timeout
        calls: list[list[str]] = []

        def fake(argv, cwd, env, timeout_s, grace_s=3):
            if argv[-1] == self.cfg["prompt"]:
                calls.append(list(argv))
                return exit_code, stdout, b"", 0.1, False
            return real(argv, cwd, env, timeout_s, grace_s)

        with patch.object(phe.phe_sandbox, "run_with_timeout", side_effect=fake):
            phe.run(self.exp)
        return calls

    def one_record(self) -> dict:
        records = phe.records(self.exp)
        self.assertEqual(len(records), 1)
        return records[0]

    def test_success_tokens_cost_none_and_explicit_pivot(self):
        stdout = jsonl(
            event("thread.started", thread_id="t1"),
            event("turn.started"),
            message("done"),
            completed(USAGE),
        )
        calls = self.run_fake(stdout)
        self.assertEqual(len(calls), 1)
        record = self.one_record()
        self.assertEqual(record[phe.TOK], 110)
        self.assertIsNone(record["estimated_cost_usd_v1"])
        self.assertEqual(record["target_agent"], "codex")
        self.assertEqual(record["target_model_id"], "frozen-model-x")
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
        # 正式调用只用规范 argv：冻结模型经 -m 传入，输出目录由 runner 指定，提示词在最后。
        argv = calls[0]
        self.assertEqual(argv[:2], ["codex", "exec"])
        self.assertEqual(argv[argv.index("-m") + 1], "frozen-model-x")
        self.assertEqual(argv[argv.index("-o") + 1], str(Path(record["target_output"]) / "last-message.txt"))
        self.assertEqual(argv[-1], self.cfg["prompt"])
        self.assertFalse(record["target_argv_custom"])
        self.assertEqual(record["argv_sha256"], phe.argv_digest(argv))
        self.assertEqual(record["argv_sha256_scope"], "full_argv")
        launch = json.loads((art / "argv.json").read_text())
        self.assertEqual(launch["target_agent"], "codex")
        self.assertEqual(launch["target_model_id"], "frozen-model-x")
        self.assertFalse(launch["target_argv_custom"])
        self.assertEqual(launch["argv"], argv)

    def test_matching_declaration_is_accepted(self):
        self.cfg["target_agent"] = "codex"
        self.freeze()
        calls = self.run_fake(jsonl(event("turn.started"), completed(USAGE)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.one_record()["target_agent"], "codex")

    def test_failure_preserves_error_and_missing_usage(self):
        stdout = jsonl(event("turn.started"), event("turn.failed", error={"message": "boom"}))
        self.run_fake(stdout, exit_code=1)
        record = self.one_record()
        self.assertEqual(record["turn_status"], "failed")
        self.assertTrue(record["is_error"])
        self.assertEqual(record["error_message"], "boom")
        self.assertIsNone(record[phe.TOK])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_truncated_json_is_invalid_and_not_zero_filled(self):
        stdout = jsonl(event("turn.started"), completed(USAGE)) + b'{"type":'
        self.run_fake(stdout)
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertIsNotNone(record["parse_error"])
        self.assertTrue(record["is_error"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_trailing_open_turn_after_completed_is_incomplete(self):
        stdout = jsonl(event("turn.started"), completed(USAGE), event("turn.started"))
        self.run_fake(stdout)
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertIsNone(record["usage"])
        self.assertIsNotNone(record["parse_error"])
        self.assertTrue(record["is_error"])
        self.assertEqual(record["num_turns"], 2)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_missing_usage_is_invalid(self):
        self.run_fake(jsonl(event("turn.started"), completed()))
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertIn("usage", record["parse_error"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_all_zero_usage_is_not_accepted(self):
        self.run_fake(jsonl(event("turn.started"), completed(ZERO_USAGE)))
        record = self.one_record()
        self.assertIsNone(record[phe.TOK])
        self.assertTrue(record["usage_all_zero"])
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_nonzero_exit_keeps_error_with_complete_telemetry(self):
        self.run_fake(jsonl(event("turn.started"), completed(USAGE)), exit_code=1)
        record = self.one_record()
        self.assertTrue(record["is_error"])
        self.assertEqual(record[phe.TOK], 110)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_codex_refuses_target_argv_override(self):
        self.cfg["target_argv"] = [sys.executable, "-I", "-c", "print('never')"]
        self.freeze()
        with patch.object(phe.phe_sandbox, "run_with_timeout") as runner:
            with self.assertRaisesRegex(ValueError, "target_argv"):
                phe.run(self.exp)
            runner.assert_not_called()
        self.assertFalse((self.exp / "stop.json").exists())
        self.assertFalse((self.exp / "artifacts").exists())

    def test_cli_refuses_codex_target_argv_override(self):
        self.cfg["target_argv"] = [sys.executable, "-I", "-c", "print('never')"]
        self.freeze()
        done = subprocess.run(
            [sys.executable, "-I", str(ROOT / "tools/phe.py"), "run", str(self.exp)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("target_argv", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertFalse((self.exp / "stop.json").exists())

    def test_freshly_frozen_conflicting_declaration_refuses_before_target(self):
        self.cfg["target_agent"] = "claude-code"
        self.freeze()
        with patch.object(phe, "run_slot") as target, self.assertRaisesRegex(ValueError, "冲突"):
            phe.run(self.exp)
        target.assert_not_called()
        self.assertFalse((self.exp / "stop.json").exists())
        self.assertFalse((self.exp / "artifacts").exists())

    def test_tampered_declaration_records_config_hash_changed_over_pivot(self):
        self.freeze()
        (self.exp / "stop.json").write_text(json.dumps({"stop_reason": "pivot:telemetry_missing"}))
        # 冻结后只改 config.target_agent：完整性事故优先，不能被声明解析的异常提前中断。
        self.cfg["target_agent"] = "claude-code"
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(phe.stop_reason(self.exp), "invalid_run:config_hash_changed")
        self.assertFalse((self.exp / "artifacts").exists())

    def test_codex_without_declaration_requires_full_runner_hash_freeze(self):
        hashes = phe.runner_file_hashes()
        legacy = {"phe.py": hashes["phe.py"], "phe_sandbox.py": hashes["phe_sandbox.py"]}
        for name, runner_hashes, omit in (
            ("missing", None, True),
            ("legacy", legacy, False),
        ):
            with self.subTest(case=name):
                self.freeze(runner_hashes=runner_hashes, omit_runner_hashes=omit)
                (self.exp / "stop.json").unlink(missing_ok=True)
                with patch.object(phe.phe_sandbox, "run_with_timeout") as runner:
                    phe.run(self.exp)
                    runner.assert_not_called()
                self.assertEqual(phe.stop_reason(self.exp), "invalid_run:sandbox_runner_hash_missing")
                self.assertFalse((self.exp / "artifacts").exists())

    def test_runner_hash_requirement_ignores_drifting_selector(self):
        hashes = phe.runner_file_hashes()
        legacy = {"phe.py": hashes["phe.py"], "phe_sandbox.py": hashes["phe_sandbox.py"]}
        self.freeze(runner_hashes=legacy)
        p, cfg = phe.load(self.exp)
        self.assertNotIn("target_agent", cfg)
        self.assertNotIn("sandbox", cfg)
        # 冻结 proposal 写的是 Codex：适配器漂移成 Claude 也不能降级成免校验。
        with patch.object(phe.phe_target, "select", return_value={"agent": "claude-code", "model_id": None}):
            self.assertEqual(phe.integrity_problem(p, cfg, self.exp), "sandbox_runner_hash_missing")

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
