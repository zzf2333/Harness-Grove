"""tools/history.py group 的契约：确定性结构分组、跨会话标记、不可用来源与输出守卫。

输入是手写的合成 Episode JSON，路径和名字都是虚构的（`/home/alice/...`），不来自任何真实会话。
"""

import importlib.util
import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/history.py"

SPEC = importlib.util.spec_from_file_location("history", TOOL)
assert SPEC is not None and SPEC.loader is not None
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)

PROJECT = "/home/alice/proj"
OTHER_PROJECT = "/home/alice/other"
SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"
CLAUDE_PATH = "/home/alice/.claude/projects/-home-alice-proj/aaaa.jsonl"
CODEX_PATH = "/home/alice/.codex/sessions/2026/10/28/rollout-bbbb.jsonl"
PI_PATH = "/home/alice/.pi/agent/sessions/--home-alice-proj--/cccc.jsonl"
REFUSAL_EXIT = 2


def episode(host, session_id, kind, line, ts, excerpt, source_path, project: str | None = PROJECT):
    return {
        "kind": kind,
        "line": line,
        "ts": ts,
        "excerpt": excerpt,
        "host": host,
        "source_path": source_path,
        "session_id": session_id,
        "sha256": "0" * 64,
        "project": project,
        "available": True,
    }


def correction(session_id, line, ts, excerpt="不对，改成这样", project: str | None = PROJECT):
    return episode("claude", session_id, "user_correction", line, ts, excerpt, CLAUDE_PATH, project=project)


def source(host, path, session_id, available=True, missing_reason=None):
    entry = {
        "host": host,
        "source_path": path,
        "session_id": session_id,
        "sha256": "0" * 64,
        "available": available,
        "episode_count": 0,
    }
    if missing_reason is not None:
        entry["missing_reason"] = missing_reason
    return entry


def run_group(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), "group", *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        check=False,
    )


class GroupCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-group-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.out = self.tmp / "groups.json"

    def write(self, name, episodes, sources=()):
        path = self.tmp / name
        document = {"episodes": list(episodes), "sources": list(sources)}
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def group(self, *paths, out=None):
        out = out or self.out
        result = run_group("--episodes", *paths, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(out.read_text(encoding="utf-8"))


class CorrelationGroupTests(GroupCase):
    def test_three_corrections_in_one_session_make_one_group(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z", "不对，第一处"),
                correction(SESSION_A, 2, "2026-10-28T01:00:05Z", "错了，第二处"),
                correction(SESSION_A, 3, "2026-10-28T01:00:09Z", "不是这样，第三处"),
            ],
        )
        data = self.group(path)
        self.assertEqual(len(data["groups"]), 1)
        group = data["groups"][0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["host"], "claude")
        self.assertEqual(group["session_id"], SESSION_A)
        self.assertEqual(group["project"], PROJECT)
        self.assertEqual(group["kind"], "user_correction")
        self.assertEqual([pointer["line"] for pointer in group["episodes"]], [1, 2, 3])
        self.assertEqual(group["first_ts"], "2026-10-28T01:00:00Z")
        self.assertEqual(group["last_ts"], "2026-10-28T01:00:09Z")
        self.assertEqual(group["same_project"], [])
        self.assertRegex(group["correlation_group"], r"^[0-9a-f]{12}$")

    def test_group_id_is_deterministic_for_the_same_key(self):
        first = self.write("a.json", [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")])
        second = self.write("b.json", [correction(SESSION_A, 7, "2026-10-28T02:00:00Z")])
        ids = [self.group(path)["groups"][0]["correlation_group"] for path in (first, second)]
        self.assertEqual(ids[0], ids[1])

    def test_kinds_are_separate_groups(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z"),
                episode("claude", SESSION_A, "tool_failure", 2, "2026-10-28T01:00:01Z", "Bash: boom", CLAUDE_PATH),
            ],
        )
        groups = self.group(path)["groups"]
        self.assertEqual([group["kind"] for group in groups], ["tool_failure", "user_correction"])
        self.assertEqual([group["count"] for group in groups], [1, 1])

    def test_episode_pointer_keeps_source_path_and_redacted_excerpt(self):
        path = self.write("a.json", [correction(SESSION_A, 5, "2026-10-28T01:00:00Z", "不对，key=<key>")])
        pointer = self.group(path)["groups"][0]["episodes"][0]
        self.assertEqual(set(pointer), {"source_path", "line", "ts", "excerpt"})
        self.assertEqual(pointer["source_path"], CLAUDE_PATH)
        self.assertEqual(pointer["line"], 5)
        self.assertEqual(pointer["excerpt"], "不对，key=<key>")

    def test_two_sessions_are_linked_but_not_merged(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_B, 1, "2026-10-28T02:00:00Z"),
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z"),
            ],
        )
        groups = self.group(path)["groups"]
        self.assertEqual([group["session_id"] for group in groups], [SESSION_A, SESSION_B])
        self.assertEqual(len(groups), 2)
        for index, group in enumerate(groups):
            other = groups[1 - index]["correlation_group"]
            self.assertEqual(group["same_project"], [other])

    def test_same_session_id_on_two_hosts_stays_apart(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z"),
                episode("codex", SESSION_A, "user_correction", 1, "2026-10-28T01:00:00Z", "不是这样", CODEX_PATH),
            ],
        )
        groups = self.group(path)["groups"]
        self.assertEqual([group["host"] for group in groups], ["claude", "codex"])
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]["same_project"]), 1)

    def test_different_project_gets_no_same_project_link(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z"),
                episode(
                    "claude",
                    SESSION_B,
                    "user_correction",
                    1,
                    "2026-10-28T01:00:00Z",
                    "不对",
                    CLAUDE_PATH,
                    project=OTHER_PROJECT,
                ),
            ],
        )
        groups = self.group(path)["groups"]
        # 组按 host、project、session_id、kind 排序。
        self.assertEqual([group["project"] for group in groups], [OTHER_PROJECT, PROJECT])
        for group in groups:
            self.assertEqual(group["same_project"], [])

    def test_same_pointer_in_two_inputs_is_counted_once(self):
        path = self.write(
            "a.json",
            [correction(SESSION_A, 1, "2026-10-28T01:00:00Z"), correction(SESSION_A, 2, "2026-10-28T01:00:01Z")],
        )
        data = self.group(path, path)
        self.assertEqual(data["groups"][0]["count"], 2)

    def test_two_runs_write_identical_bytes(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_B, 2, "2026-10-28T02:00:01Z"),
                correction(SESSION_A, 3, "2026-10-28T01:00:02Z"),
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z"),
            ],
        )
        first = self.tmp / "first.json"
        second = self.tmp / "second.json"
        self.group(path, out=first)
        self.group(path, out=second)
        self.assertEqual(first.read_bytes(), second.read_bytes())


class UnknownProjectTests(GroupCase):
    def test_sessions_without_project_are_not_linked(self):
        path = self.write(
            "a.json",
            [
                correction(SESSION_A, 1, "2026-10-28T01:00:00Z", project=""),
                correction(SESSION_B, 1, "2026-10-28T02:00:00Z", project=None),
            ],
        )
        groups = self.group(path)["groups"]
        self.assertEqual(len(groups), 2)
        for group in groups:
            # project 未知时按空串输出，且不建 same_project 索引：未知项目不是同一个项目。
            self.assertEqual(group["project"], "")
            self.assertEqual(group["same_project"], [])


class UnavailableSourceTests(GroupCase):
    def test_unavailable_sources_are_summarized_without_content(self):
        path = self.write(
            "a.json",
            [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")],
            [
                source("pi", PI_PATH, SESSION_B, available=False, missing_reason="unsupported_schema"),
                source("pi", "/home/alice/.pi/agent/sessions/--x--/dddd.jsonl", SESSION_A),
            ],
        )
        data = self.group(path)
        self.assertEqual(
            data["sources_unavailable"],
            [{"source_path": PI_PATH, "host": "pi", "missing_reason": "unsupported_schema"}],
        )

    def test_repeated_unavailable_source_appears_once(self):
        unavailable = source("pi", PI_PATH, SESSION_B, available=False, missing_reason="PermissionError")
        first = self.write("a.json", [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")], [unavailable])
        second = self.write("b.json", [], [unavailable])
        data = self.group(first, second)
        self.assertEqual(len(data["sources_unavailable"]), 1)


class SessionSourceGuardTests(GroupCase):
    def setUp(self):
        super().setUp()
        # 合成会话目录：source_path 指向这里，模拟 collect 产物里的真实指针。
        self.session_dir = self.tmp / "sessions" / "--home-alice-proj--"
        self.session_dir.mkdir(parents=True)
        self.session_source = self.session_dir / "eeee.jsonl"
        self.session_source.write_text('{"type": "user"}\n', encoding="utf-8")

    def episodes_pointing_at_session_source(self):
        pointer = str(self.session_source)
        return self.write(
            "a.json",
            [episode("claude", SESSION_A, "user_correction", 1, "2026-10-28T01:00:00Z", "不对", pointer)],
            [source("claude", pointer, SESSION_A)],
        )

    def test_out_equal_to_a_session_source_is_refused(self):
        path = self.episodes_pointing_at_session_source()
        before = self.session_source.read_bytes()
        result = run_group("--episodes", path, "--out", self.session_source)
        self.assertEqual(result.returncode, REFUSAL_EXIT, result.stderr)
        self.assertEqual(self.session_source.read_bytes(), before, "拒绝时会话源字节不变")

    def test_out_linking_to_a_session_source_is_refused(self):
        path = self.episodes_pointing_at_session_source()
        link = self.tmp / "link.json"
        link.symlink_to(self.session_source)
        before = self.session_source.read_bytes()
        result = run_group("--episodes", path, "--out", link)
        self.assertEqual(result.returncode, REFUSAL_EXIT, result.stderr)
        self.assertEqual(self.session_source.read_bytes(), before, "拒绝时会话源字节不变")

    def test_out_in_a_session_source_directory_is_refused(self):
        path = self.episodes_pointing_at_session_source()
        before = self.session_source.read_bytes()
        out = self.session_dir / "groups.json"
        result = run_group("--episodes", path, "--out", out)
        self.assertEqual(result.returncode, REFUSAL_EXIT, result.stderr)
        self.assertFalse(out.exists(), "拒绝时不得写文件")
        self.assertEqual(self.session_source.read_bytes(), before, "拒绝时会话源字节不变")


class OutputGuardTests(GroupCase):
    def test_episodes_and_out_are_required(self):
        path = self.write("a.json", [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")])
        self.assertEqual(run_group("--out", self.tmp / "x.json").returncode, 2)
        self.assertEqual(run_group("--episodes", path).returncode, 2)
        self.assertFalse((self.tmp / "x.json").exists())

    def test_out_equal_to_input_is_refused_and_input_is_unchanged(self):
        path = self.write("a.json", [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")])
        before = path.read_bytes()
        result = run_group("--episodes", path, "--out", path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before, "拒绝时输入文件字节不变")

    def test_out_linking_to_input_is_refused(self):
        path = self.write("a.json", [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")])
        link = self.tmp / "link.json"
        link.symlink_to(path)
        before = path.read_bytes()
        result = run_group("--episodes", path, "--out", link)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before, "拒绝时输入文件字节不变")

    def test_out_inside_real_agent_directory_is_refused(self):
        path = self.write("a.json", [correction(SESSION_A, 1, "2026-10-28T01:00:00Z")])
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        for relative in history.PROTECTED_SUBPATHS:
            out = home / relative / "phe-history-group-refusal-test.json"
            with self.subTest(protected=relative):
                result = run_group("--episodes", path, "--out", out)
                self.assertEqual(result.returncode, REFUSAL_EXIT, result.stderr)
                self.assertFalse(out.exists(), "拒绝时不得写文件")

    def test_unknown_input_schema_is_an_error(self):
        path = self.tmp / "bad.json"
        path.write_text('{"episode": []}\n', encoding="utf-8")
        result = run_group("--episodes", path, "--out", self.out)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.out.exists())


class InputValidationTests(GroupCase):
    def assert_rejected(self, episodes, sources=()):
        path = self.write("bad.json", episodes, sources)
        result = run_group("--episodes", path, "--out", self.out)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.out.exists(), "校验失败时不得写输出")

    def test_empty_session_id_is_rejected(self):
        self.assert_rejected([correction("", 1, "2026-10-28T01:00:00Z")])

    def test_non_string_excerpt_is_rejected(self):
        broken = correction(SESSION_A, 1, "2026-10-28T01:00:00Z")
        broken["excerpt"] = {"text": "不对"}
        self.assert_rejected([broken])

    def test_non_positive_line_is_rejected(self):
        for line in (0, -1, True):
            with self.subTest(line=line):
                self.assert_rejected([correction(SESSION_A, line, "2026-10-28T01:00:00Z")])

    def test_non_boolean_available_is_rejected(self):
        broken = source("pi", PI_PATH, SESSION_B)
        broken["available"] = "false"
        self.assert_rejected([], [broken])

    def test_unavailable_source_without_source_path_is_rejected(self):
        broken = source("pi", PI_PATH, SESSION_B, available=False, missing_reason="PermissionError")
        del broken["source_path"]
        self.assert_rejected([], [broken])


if __name__ == "__main__":
    unittest.main()
