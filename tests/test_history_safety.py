"""tools/history.py collect 的边界：读不到的来源、坏行、脱敏、受保护输出路径、--since、Pi 默认排除。"""

import importlib.util
import json
import os
import pwd
import tempfile
import unittest
from pathlib import Path

from test_history import (
    CLAUDE_FIXTURE,
    PI_FIXTURE,
    PI_RELATIVE,
    PROJECT,
    SECRET,
    run_cli,
    write_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("history", ROOT / "tools/history.py")
assert SPEC is not None and SPEC.loader is not None
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)

PROTECTED_EXIT = 2
CLAUDE_SID = "11111111-1111-4111-8111-111111111111"


def user_record(timestamp, text):
    return json.dumps(
        {
            "type": "user",
            "sessionId": CLAUDE_SID,
            "cwd": PROJECT,
            "timestamp": timestamp,
            "message": {"role": "user", "content": text},
        },
        ensure_ascii=False,
    )


class TempRootCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-edge-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.root = self.tmp / "claude"
        self.out = self.tmp / "episodes.json"
        self.source = self.root / "projects/-home-alice-proj/11111111-1111-4111-8111-111111111111.jsonl"

    def collect(self, host="claude", *extra):
        return run_cli("--host", host, "--root", self.root, "--out", self.out, *extra)

    def read(self):
        return json.loads(self.out.read_text(encoding="utf-8"))


class UnavailableSourceTests(TempRootCase):
    def test_unreadable_source_is_reported_not_skipped(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行无法用 chmod 构造权限错误")
        write_fixture(self.source, CLAUDE_FIXTURE)
        self.addCleanup(os.chmod, self.source, 0o600)
        os.chmod(self.source, 0o000)
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read()
        self.assertEqual(data["episodes"], [])
        self.assertEqual(len(data["sources"]), 1)
        source = data["sources"][0]
        self.assertFalse(source["available"])
        self.assertEqual(source["missing_reason"], "PermissionError")
        self.assertIsNone(source["sha256"])
        self.assertEqual(source["episode_count"], 0)

    def test_bad_line_marks_source_unavailable_and_keeps_good_lines(self):
        lines = CLAUDE_FIXTURE.splitlines()
        lines.insert(1, "{not json")
        self.source.parent.mkdir(parents=True)
        self.source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read()
        source = data["sources"][0]
        self.assertFalse(source["available"])
        self.assertEqual(source["bad_lines"], [{"line": 2, "missing_reason": "invalid_json"}])
        self.assertIn("line 2", source["missing_reason"])
        self.assertTrue(data["episodes"], "坏行之前的记录不应丢失")
        self.assertEqual(len(data["episodes"]), source["episode_count"])
        # 坏行之后的行号整体后移一位，指针仍指向原记录。
        for episode in data["episodes"]:
            if episode["line"] > 2:
                self.assertIn("SENTINEL-", Path(episode["source_path"]).read_text().splitlines()[episode["line"] - 1])


class SinceFilterTests(TempRootCase):
    def test_since_keeps_only_newer_records(self):
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            "\n".join(
                [
                    user_record("2020-01-01T00:00:00Z", "不对，OLD-SENTINEL"),
                    user_record("2026-10-28T00:00:00Z", "不对，NEW-SENTINEL"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.collect("claude", "--since", "2026-01-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        episodes = self.read()["episodes"]
        self.assertEqual(len(episodes), 1)
        self.assertIn("NEW-SENTINEL", episodes[0]["excerpt"])
        self.assertEqual(episodes[0]["line"], 2)

    def test_since_rejects_unknown_format(self):
        write_fixture(self.source, CLAUDE_FIXTURE)
        result = self.collect("claude", "--since", "yesterday")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.out.exists())


class PiExcludeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-pi-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.root = self.tmp / "pi"
        self.out = self.tmp / "episodes.json"
        self.main = self.root / "sessions/--home-alice-proj--/2026-10-28T01-00-00-000Z_main.jsonl"
        self.subagent = self.root / "sessions/subagent/child/run-0/session.jsonl"
        write_fixture(self.main, PI_FIXTURE)
        write_fixture(self.subagent, PI_FIXTURE)

    def collect(self, *extra):
        return run_cli("--host", "pi", "--root", self.root, "--out", self.out, *extra)

    def test_subagent_sessions_excluded_by_default(self):
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual([Path(source["source_path"]).name for source in data["sources"]], [self.main.name])

    def test_exclude_appends_to_default(self):
        result = self.collect("--exclude", "sessions/--home-alice-proj--/*")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["sources"], [])
        self.assertEqual(data["episodes"], [])

    def test_relative_layout_is_what_excludes_match(self):
        self.assertEqual(history.DEFAULT_EXCLUDES["pi"], ("sessions/subagent/*",))
        self.assertEqual(PI_RELATIVE.split("/")[0], "sessions")


class RedactionTests(unittest.TestCase):
    def test_each_secret_shape_is_replaced(self):
        samples = {
            "<key>": ["sk-" + "a" * 24, "ghp_" + "b" * 24, "AKIA" + "C" * 16, "Bearer " + "d" * 20],
            "<private-key>": ["-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----"],
            "<redacted>": ["password=hunter2", "api_key=abcdef123456", "token: t-987654321"],
        }
        for replacement, texts in samples.items():
            for text in texts:
                with self.subTest(text=text[:12]):
                    self.assertIn(replacement, history.excerpt(text))

    def test_original_values_are_gone(self):
        text = f"password=hunter2 api_key=abcdef123456 Bearer {'e' * 20} key={SECRET}"
        cleaned = history.excerpt(text)
        for value in ("hunter2", "abcdef123456", "e" * 20, SECRET):
            self.assertNotIn(value, cleaned)

    def test_excerpt_is_clipped(self):
        self.assertEqual(len(history.excerpt("不对 " + "x" * 400)), 300)


class ProtectedOutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-out-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.root = self.tmp / "claude"
        self.source = self.root / "projects/-home-alice-proj/11111111-1111-4111-8111-111111111111.jsonl"
        write_fixture(self.source, CLAUDE_FIXTURE)

    def test_out_inside_real_agent_directory_is_refused(self):
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        for relative in history.PROTECTED_SUBPATHS:
            out = home / relative / "phe-history-refusal-test.json"
            with self.subTest(protected=relative):
                result = run_cli("--host", "claude", "--root", self.root, "--out", out)
                self.assertEqual(result.returncode, PROTECTED_EXIT, result.stderr)
                self.assertFalse(out.exists(), "拒绝时不得写文件")

    def test_out_outside_real_home_is_allowed(self):
        out = self.tmp / "episodes.json"
        result = run_cli("--host", "claude", "--root", self.root, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.exists())

    def test_root_and_out_are_required(self):
        self.assertEqual(run_cli("--host", "claude", "--out", self.tmp / "x.json").returncode, 2)
        self.assertEqual(run_cli("--host", "claude", "--root", self.root).returncode, 2)

    def test_missing_root_is_an_error(self):
        result = run_cli("--host", "claude", "--root", self.tmp / "absent", "--out", self.tmp / "x.json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.tmp / "x.json").exists())


class MalformedRecordTests(TempRootCase):
    """结构异常的行只影响自己：不中断采集，也不把原文写进 sources。"""

    def test_parser_error_is_recorded_as_a_bad_line(self):
        lines = [
            user_record("2026-10-28T01:00:00Z", "不对，FIRST-SENTINEL"),
            '{"type":"user","message":"unexpected"}',
            user_record("2026-10-28T01:00:02Z", "不是这样，SECOND-SENTINEL"),
        ]
        self.source.parent.mkdir(parents=True)
        self.source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read()
        self.assertEqual([episode["line"] for episode in data["episodes"]], [1, 3])
        source = data["sources"][0]
        self.assertFalse(source["available"])
        self.assertEqual(source["bad_lines"], [{"line": 2, "missing_reason": "malformed_record: AttributeError"}])
        self.assertIn("line 2", source["missing_reason"])
        self.assertNotIn("unexpected", json.dumps(source, ensure_ascii=False), "missing_reason 不得带原文")


class UnsupportedSchemaTests(TempRootCase):
    """整份文件都认不出本 Host 的已知记录类型时，在 source 上标出来，不静默跳过。"""

    def write_pi(self, name, records):
        path = self.root / "sessions/--home-alice-proj--" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8"
        )
        return path

    def test_transcript_shape_is_marked_unsupported(self):
        self.write_pi(
            "2026-10-28T01-00-00-000Z_44444444-4444-4444-8444-444444444444_transcript.jsonl",
            [
                {"recordType": "message", "agent": "child", "runId": "run-1", "role": "user", "text": "SENTINEL"},
                {"recordType": "message", "agent": "child", "runId": "run-1", "role": "assistant", "text": "ok"},
            ],
        )
        result = self.collect("pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read()
        self.assertEqual(data["episodes"], [])
        self.assertEqual(len(data["sources"]), 1)
        source = data["sources"][0]
        self.assertFalse(source["available"])
        self.assertEqual(source["missing_reason"], "unsupported_schema")
        self.assertNotIn("bad_lines", source)
        self.assertEqual(source["episode_count"], 0)

    def test_known_but_uncollected_types_are_not_unsupported(self):
        self.write_pi(
            "2026-10-28T01-00-00-000Z_55555555-5555-4555-8555-555555555555.jsonl",
            [
                {
                    "type": "session",
                    "id": "55555555-5555-4555-8555-555555555555",
                    "cwd": PROJECT,
                    "timestamp": "2026-10-28T01:00:00.000Z",
                },
                {"type": "model_change", "timestamp": "2026-10-28T01:00:01.000Z", "modelId": "synthetic"},
            ],
        )
        result = self.collect("pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read()
        self.assertEqual(data["episodes"], [])
        source = data["sources"][0]
        self.assertTrue(source["available"])
        self.assertNotIn("missing_reason", source)

    def test_only_bad_lines_report_the_bad_line_not_the_schema(self):
        self.source.parent.mkdir(parents=True)
        self.source.write_text("{not json\n", encoding="utf-8")
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        source = self.read()["sources"][0]
        self.assertFalse(source["available"])
        self.assertEqual(source["missing_reason"], "line 1: invalid_json")


# 合成私钥：分两段拼，正文是标记串，不含任何真实密钥材料。
KEY_BEGIN = "-----BEGIN " + "RSA PRIVATE KEY-----"
KEY_BODY = ("SYNTHETICKEYBODY0", "SYNTHETICKEYBODY1")


class IncompletePrivateKeyTests(TempRootCase):
    """找不到 END 的私钥块要从 BEGIN 一直脱敏到文本末尾，不能只删头部。"""

    @staticmethod
    def message(timestamp, content, role="user"):
        return {
            "type": role,
            "sessionId": CLAUDE_SID,
            "cwd": PROJECT,
            "timestamp": timestamp,
            "message": {"role": role, "content": content},
        }

    def write_records(self, records):
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8"
        )

    def collect_text(self):
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.out.read_text(encoding="utf-8")

    def assert_body_redacted(self, text):
        self.assertIn("<private-key>", text)
        for fragment in KEY_BODY:
            self.assertNotIn(fragment, text)

    def test_unclosed_key_in_a_single_text_block(self):
        body = "\n".join([KEY_BEGIN, *KEY_BODY])
        self.write_records([self.message("2026-10-28T01:00:00Z", f"不对，看这个\n{body}")])
        text = self.collect_text()
        self.assertIn("user_correction", text)
        self.assert_body_redacted(text)

    def test_unclosed_key_in_a_later_content_block(self):
        body = "\n".join([KEY_BEGIN, *KEY_BODY])
        self.write_records(
            [
                self.message(
                    "2026-10-28T01:00:00Z",
                    [{"type": "text", "text": "错了，看这个"}, {"type": "text", "text": body}],
                )
            ]
        )
        text = self.collect_text()
        self.assertIn("user_correction", text)
        self.assert_body_redacted(text)

    def test_unclosed_key_in_tool_failure_output(self):
        body = "\n".join([KEY_BEGIN, *KEY_BODY])
        self.write_records(
            [
                self.message(
                    "2026-10-28T01:00:01Z",
                    [{"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {"command": "cat id_rsa"}}],
                    role="assistant",
                ),
                self.message(
                    "2026-10-28T01:00:02Z",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_9",
                            "is_error": True,
                            "content": f"读取失败\n{body}",
                        }
                    ],
                ),
            ]
        )
        text = self.collect_text()
        self.assertIn("tool_failure", text)
        self.assert_body_redacted(text)


class OutInsideRootTests(unittest.TestCase):
    """--out 不得落进 --root：那里是源会话文件。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-out-root-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.root = self.tmp / "claude"
        self.source = self.root / "projects/-home-alice-proj" / f"{CLAUDE_SID}.jsonl"
        write_fixture(self.source, CLAUDE_FIXTURE)

    def test_out_must_not_overwrite_a_session_file(self):
        before = self.source.read_bytes()
        result = run_cli("--host", "claude", "--root", self.root, "--out", self.source)
        self.assertEqual(result.returncode, PROTECTED_EXIT, result.stderr)
        self.assertEqual(self.source.read_bytes(), before, "拒绝时源文件字节不变")

    def test_out_must_not_be_a_new_path_under_root(self):
        out = self.root / "episodes.json"
        result = run_cli("--host", "claude", "--root", self.root, "--out", out)
        self.assertEqual(result.returncode, PROTECTED_EXIT, result.stderr)
        self.assertFalse(out.exists())

    def test_sibling_directory_with_the_same_prefix_is_allowed(self):
        out = self.tmp / "claude-elsewhere" / "episodes.json"
        result = run_cli("--host", "claude", "--root", self.root, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
