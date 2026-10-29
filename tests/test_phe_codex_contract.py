"""Codex Target 契约：目标选择、默认 argv、指纹与 JSONL 用量/失败语义（不调用模型）。

事件形状按上游 `rust-v0.163.0` 的 `exec/src/exec_events.rs` 手写合成；版本号一致不等于
二进制 commit 一致，也不是真实 Codex 会话证据。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_phe
from codex_fixture import USAGE, ZERO_USAGE, completed, event, jsonl, message

phe = test_phe.phe
phe_target = phe.phe_target


class SelectTests(unittest.TestCase):
    def test_missing_agent_defaults_to_legacy_claude(self):
        self.assertEqual(phe_target.select({}, {})["agent"], "claude-code")
        self.assertEqual(phe_target.select({"target": {}}, {})["agent"], "claude-code")

    def test_reads_proposal_agent_and_frozen_model(self):
        target = phe_target.select({"target": {"agent": "codex", "model_id": "frozen-model-x"}}, {})
        self.assertEqual(target, {"agent": "codex", "model_id": "frozen-model-x"})

    def test_config_agent_conflict_is_refused(self):
        p = {"target": {"agent": "codex", "model_id": "frozen-model-x"}}
        with self.assertRaisesRegex(ValueError, "冲突"):
            phe_target.select(p, {"target_agent": "claude-code"})

    def test_config_agent_must_match_when_present(self):
        p = {"target": {"agent": "codex", "model_id": "frozen-model-x"}}
        self.assertEqual(phe_target.select(p, {"target_agent": "codex"})["agent"], "codex")

    def test_unknown_agent_is_refused(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            phe_target.select({"target": {"agent": "gemini"}}, {})

    def test_codex_requires_frozen_model_id(self):
        for model in (None, "", "  "):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "model_id"):
                phe_target.select({"target": {"agent": "codex", "model_id": model}}, {})


class ArgvTests(unittest.TestCase):
    def test_codex_default_argv_has_no_bypass_or_budget_flags(self):
        target = {"agent": "codex", "model_id": "frozen-model-x"}
        argv = phe_target.default_argv(target, {"prompt": "do the task"}, Path("/slot/repo"), Path("/slot/output"))
        self.assertEqual(
            argv,
            [
                "codex",
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "-s",
                "workspace-write",
                "-C",
                "/slot/repo",
                "-m",
                "frozen-model-x",
                "-o",
                "/slot/output/last-message.txt",
                "do the task",
            ],
        )
        forbidden = ("--dangerously-bypass-approvals-and-sandbox", "--max-turns", "--add-dir", "--yolo")
        self.assertFalse(set(forbidden) & set(argv))

    def test_claude_default_argv_is_unchanged(self):
        cfg = {
            "prompt": "do the task",
            "claude_args": ["--output-format", "json"],
            "settings": {"disableAllHooks": True},
        }
        argv = phe_target.default_argv({"agent": "claude-code"}, cfg, Path("/slot/repo"), Path("/slot/output"))
        self.assertEqual(
            argv,
            [
                "claude",
                "-p",
                "do the task",
                "--output-format",
                "json",
                "--settings",
                json.dumps({"disableAllHooks": True}),
                "--debug-file",
                "/slot/output/debug.log",
            ],
        )

    def test_argv_hash_keeps_claude_legacy_scope(self):
        argv = ["claude", "-p", "prompt", "--model", "m"]
        self.assertEqual(phe_target.argv_sha256(argv, "claude-code", custom=False), phe.argv_digest(argv[3:]))
        self.assertEqual(phe_target.argv_sha256(argv, "claude-code", custom=True), phe.argv_digest(argv))
        self.assertEqual(phe_target.argv_sha256(argv, "codex", custom=False), phe.argv_digest(argv))


class FingerprintTests(unittest.TestCase):
    def test_codex_fingerprint_reads_only_agents_and_config(self):
        with tempfile.TemporaryDirectory(prefix="phe-codex-home-") as temp:
            home = Path(temp)
            (home / ".codex").mkdir()
            (home / ".codex/AGENTS.md").write_text("agents")
            (home / ".codex/config.toml").write_text("model = 'x'")
            (home / ".codex/auth.json").write_text("SECRET")
            calls: list[str] = []
            original = phe_target.sha256_file

            def spy(path):
                calls.append(str(path))
                return original(path)

            with patch.object(phe_target, "sha256_file", side_effect=spy):
                fingerprint = phe_target.user_fingerprint("codex", home=home)
            resolved = home.resolve()
            self.assertEqual(
                set(fingerprint),
                {str(resolved / ".codex/AGENTS.md"), str(resolved / ".codex/config.toml")},
            )
            self.assertNotIn(str(home.resolve() / ".codex/auth.json"), calls)
            self.assertNotIn(str(home.resolve() / ".codex/auth.json"), fingerprint)

    def test_missing_codex_path_is_none_not_an_error(self):
        with tempfile.TemporaryDirectory(prefix="phe-codex-home-") as temp:
            home = Path(temp)
            (home / ".codex").mkdir()
            (home / ".codex/AGENTS.md").write_text("agents")
            fingerprint = phe_target.user_fingerprint("codex", home=home)
            resolved = home.resolve()
            self.assertEqual(
                fingerprint[str(resolved / ".codex/AGENTS.md")], phe.sha256_file(resolved / ".codex/AGENTS.md")
            )
            self.assertIsNone(fingerprint[str(resolved / ".codex/config.toml")])

    def test_claude_fingerprint_keeps_existing_three_files(self):
        with tempfile.TemporaryDirectory(prefix="phe-claude-home-") as temp:
            home = Path(temp)
            (home / ".claude").mkdir()
            for name in ("CLAUDE.md", "RTK.md", "settings.json"):
                (home / ".claude" / name).write_text(name)
            fingerprint = phe_target.user_fingerprint("claude-code", home=home)
            resolved = home.resolve()
            self.assertEqual(
                set(fingerprint),
                {str(resolved / ".claude" / name) for name in ("CLAUDE.md", "RTK.md", "settings.json")},
            )


class ParseTests(unittest.TestCase):
    def parse(self, data: bytes, exit_code: int = 0, timed_out: bool = False) -> dict:
        return phe_target.parse_codex(data, exit_code, timed_out)

    def test_success_uses_last_completed_usage_without_adding_components(self):
        second = {
            "input_tokens": 200,
            "cached_input_tokens": 80,
            "cache_write_input_tokens": 120,
            "output_tokens": 20,
            "reasoning_output_tokens": 10,
        }
        parsed = self.parse(
            jsonl(
                event("thread.started", thread_id="t1"),
                event("turn.started"),
                message("first"),
                completed(USAGE),
                event("turn.started"),
                message("final"),
                completed(second),
            )
        )
        self.assertEqual(parsed["thread_id"], "t1")
        self.assertEqual(parsed["turn_status"], "completed")
        self.assertEqual(parsed["usage"], second)
        self.assertEqual(parsed["tokens"], 220)
        self.assertEqual(parsed["final_message"], "final")
        self.assertEqual(parsed["num_turns"], 2)
        self.assertEqual(parsed["num_turns_basis"], "codex_turn_started_events")
        self.assertFalse(parsed["is_error"])
        self.assertIsNone(parsed["parse_error"])

    def test_cached_reasoning_and_cache_write_are_not_added_to_tokens(self):
        parsed = self.parse(jsonl(event("turn.started"), completed(USAGE)))
        self.assertEqual(parsed["tokens"], 110)

    def test_missing_usage_is_invalid_not_zero(self):
        parsed = self.parse(jsonl(event("turn.started"), completed()))
        self.assertIsNone(parsed["usage"])
        self.assertIsNone(parsed["tokens"])
        self.assertIn("usage", parsed["parse_error"])
        self.assertTrue(parsed["is_error"])

    def test_bad_usage_fields_are_invalid_not_zero(self):
        cases = {
            "missing": {k: v for k, v in USAGE.items() if k != "output_tokens"},
            "string": dict(USAGE, input_tokens="100"),
            "negative": dict(USAGE, output_tokens=-1),
            "bool": dict(USAGE, input_tokens=True),
            "null": dict(USAGE, cached_input_tokens=None),
        }
        for name, usage in cases.items():
            with self.subTest(case=name):
                parsed = self.parse(jsonl(event("turn.started"), completed(usage)))
                self.assertIsNone(parsed["usage"])
                self.assertIsNone(parsed["tokens"])
                self.assertIsNotNone(parsed["parse_error"])
                self.assertTrue(parsed["is_error"])

    def test_all_zero_usage_is_not_a_valid_measurement(self):
        parsed = self.parse(jsonl(event("turn.started"), completed(ZERO_USAGE)))
        self.assertIsNone(parsed["usage"])
        self.assertIsNone(parsed["tokens"])
        self.assertTrue(parsed["usage_all_zero"])
        self.assertIsNone(parsed["parse_error"])

    def test_truncated_or_non_object_json_is_invalid(self):
        cases = {
            "truncated": jsonl(event("turn.started"), completed(USAGE)) + b'{"type":',
            "array": b"[1, 2]\n",
            "not_json": b"not json\n",
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                parsed = self.parse(data)
                self.assertIsNone(parsed["tokens"])
                self.assertIsNotNone(parsed["parse_error"])
                self.assertTrue(parsed["is_error"])

    def test_turn_failed_is_not_masked_by_later_completed(self):
        parsed = self.parse(
            jsonl(
                event("turn.started"),
                event("turn.failed", error={"message": "boom"}),
                event("turn.started"),
                completed(USAGE),
            )
        )
        self.assertEqual(parsed["turn_status"], "failed")
        self.assertTrue(parsed["is_error"])
        self.assertEqual(parsed["error_message"], "boom")

    def test_turn_failed_without_completed_has_no_usage(self):
        parsed = self.parse(jsonl(event("turn.started"), event("turn.failed", error={"message": "boom"})))
        self.assertEqual(parsed["turn_status"], "failed")
        self.assertIsNone(parsed["usage"])
        self.assertIsNone(parsed["tokens"])
        self.assertTrue(parsed["is_error"])

    def test_fatal_error_event_is_failure(self):
        parsed = self.parse(jsonl(event("turn.started"), event("error", message="fatal"), completed(USAGE)))
        self.assertEqual(parsed["turn_status"], "failed")
        self.assertTrue(parsed["is_error"])
        self.assertEqual(parsed["error_message"], "fatal")

    def test_nonzero_exit_is_error_even_with_completed_usage(self):
        parsed = self.parse(jsonl(event("turn.started"), completed(USAGE)), exit_code=1)
        self.assertTrue(parsed["is_error"])
        self.assertEqual(parsed["turn_status"], "completed")
        self.assertEqual(parsed["tokens"], 110)

    def test_missing_terminal_event_is_invalid(self):
        parsed = self.parse(jsonl(event("turn.started")))
        self.assertEqual(parsed["turn_status"], "missing")
        self.assertIsNone(parsed["tokens"])
        self.assertIn("terminal", parsed["parse_error"])
        self.assertTrue(parsed["is_error"])

    def test_unknown_event_is_tolerated(self):
        parsed = self.parse(jsonl(event("turn.started"), event("future.event", value=1), completed(USAGE)))
        self.assertIsNone(parsed["parse_error"])
        self.assertEqual(parsed["tokens"], 110)

    def test_command_exits_are_collected(self):
        parsed = self.parse(
            jsonl(
                event("turn.started"),
                event(
                    "item.completed",
                    item={"type": "command_execution", "command": "false", "exit_code": 1, "status": "failed"},
                ),
                completed(USAGE),
            )
        )
        self.assertEqual(parsed["command_exits"], [{"command": "false", "exit_code": 1, "status": "failed"}])

    def test_num_turns_counts_only_observable_turn_started(self):
        parsed = self.parse(jsonl(event("turn.started"), event("turn.started"), completed(USAGE)))
        self.assertEqual(parsed["num_turns"], 2)
        self.assertEqual(parsed["num_turns_basis"], "codex_turn_started_events")


if __name__ == "__main__":
    unittest.main()
