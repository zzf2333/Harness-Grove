"""`tools/history.py digest` 的摘要契约：三个 Host 各一份手写合成 fixture，走真实 CLI。

fixture 全部手写合成，不含任何真实会话内容；路径和名字都是虚构的。每行一条记录，行号就是摘要里
`L<行号>` 指针指向的源文件物理行号。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/history.py"

PROJECT = "/home/alice/proj"
CLAUDE_SID = "11111111-1111-4111-8111-111111111111"
CLAUDE_BARE_SID = "11111111-1111-4111-8111-222222222222"
CODEX_SID = "22222222-2222-4222-8222-222222222222"
CODEX_BARE_SID = "22222222-2222-4222-8222-333333333333"
PI_SID = "33333333-3333-4333-8333-333333333333"
PI_BARE_SID = "33333333-3333-4333-8333-444444444444"
# 合成密钥：分段拼接，文件里不出现形似真实密钥的字面量。
SECRET = "sk-" + "synthetic" + "0" * 30

CLAUDE_RELATIVE = "projects/-home-alice-proj/{sid}.jsonl"
CODEX_RELATIVE = "sessions/2026/10/28/rollout-2026-10-28T01-00-00-{sid}.jsonl"
PI_RELATIVE = "sessions/--home-alice-proj--/2026-10-28T01-00-00-000Z_{sid}.jsonl"

# 第 1–2 行两条真人消息（第二条命中纠正词）；第 3–4 行注入上下文；第 5–6 行一次失败的 Bash；
# 第 7–8 行连续两次相同调用。
CLAUDE_FIXTURE = """\
{"type":"user","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:00Z","message":{"role":"user","content":"SENTINEL-USER-PLAIN 帮我看一下这段代码 key={SECRET} {LONG_USER}"}}
{"type":"user","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:01Z","message":{"role":"user","content":"不对，SENTINEL-USER-CORRECTION"}}
{"type":"user","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:02Z","isMeta":true,"message":{"role":"user","content":"SENTINEL-INJECT-META"}}
{"type":"user","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:03Z","message":{"role":"user","content":"<system-reminder>SENTINEL-INJECT-PREFIX</system-reminder>"}}
{"type":"assistant","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:04Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_01","name":"Bash","input":{"command":"echo a"}}]}}
{"type":"user","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:05Z","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01","is_error":true,"content":"SENTINEL-FAILURE boom {LONG_FAILURE}"}]}}
{"type":"assistant","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:06Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_02","name":"Bash","input":{"command":"echo SENTINEL-RETRY"}}]}}
{"type":"assistant","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:07Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_03","name":"Bash","input":{"command":"echo SENTINEL-RETRY"}}]}}
"""

# 只有注入内容：整节跳过，只在索引里计数。
CLAUDE_INJECTED_ONLY = """\
{"type":"user","sessionId":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:00Z","isMeta":true,"message":{"role":"user","content":"SENTINEL-INJECT-ONLY"}}
"""

CODEX_FIXTURE = """\
{"type":"session_meta","timestamp":"2026-10-28T02:00:00Z","payload":{"id":"{sid}","cwd":"{PROJECT}"}}
{"type":"response_item","timestamp":"2026-10-28T02:00:01Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"SENTINEL-USER-PLAIN 帮我看一下这段代码 key={SECRET} {LONG_USER}"}]}}
{"type":"response_item","timestamp":"2026-10-28T02:00:02Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"不是这样，SENTINEL-USER-CORRECTION"}]}}
{"type":"response_item","timestamp":"2026-10-28T02:00:03Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"SENTINEL-INJECT-KINDS"}],"internal_chat_message_metadata_passthrough":{"content_item_kinds":["environments.environment_context"]}}}
{"type":"response_item","timestamp":"2026-10-28T02:00:04Z","payload":{"type":"custom_tool_call","call_id":"call_1","name":"shell","input":"echo a"}}
{"type":"response_item","timestamp":"2026-10-28T02:00:05Z","payload":{"type":"custom_tool_call_output","call_id":"call_1","output":[{"type":"output_text","text":"Script failed SENTINEL-FAILURE {LONG_FAILURE}"}]}}
{"type":"response_item","timestamp":"2026-10-28T02:00:06Z","payload":{"type":"custom_tool_call","call_id":"call_2","name":"shell","input":"echo SENTINEL-RETRY"}}
{"type":"response_item","timestamp":"2026-10-28T02:00:07Z","payload":{"type":"custom_tool_call","call_id":"call_3","name":"shell","input":"echo SENTINEL-RETRY"}}
"""

CODEX_INJECTED_ONLY = """\
{"type":"session_meta","timestamp":"2026-10-28T02:00:00Z","payload":{"id":"{sid}","cwd":"{PROJECT}"}}
{"type":"response_item","timestamp":"2026-10-28T02:00:01Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"SENTINEL-INJECT-ONLY"}],"internal_chat_message_metadata_passthrough":{"content_item_kinds":["permissions.instructions"]}}}
"""

PI_FIXTURE = """\
{"type":"session","id":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T03:00:00.000Z","version":"0"}
{"type":"message","timestamp":"2026-10-28T03:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"SENTINEL-USER-PLAIN 帮我看一下这段代码 key={SECRET} {LONG_USER}"}]}}
{"type":"message","timestamp":"2026-10-28T03:00:02.000Z","message":{"role":"user","content":[{"type":"text","text":"错了，SENTINEL-USER-CORRECTION"}]}}
{"type":"message","timestamp":"2026-10-28T03:00:03.000Z","message":{"role":"user","content":[{"type":"text","text":"<skill name=\\"demo\\">SENTINEL-INJECT-SKILL</skill>"}]}}
{"type":"message","timestamp":"2026-10-28T03:00:04.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c1","name":"read","arguments":{"path":"a.txt"}}]}}
{"type":"message","timestamp":"2026-10-28T03:00:05.000Z","message":{"role":"toolResult","toolName":"read","toolCallId":"c1","isError":true,"content":[{"type":"text","text":"SENTINEL-FAILURE no such file {LONG_FAILURE}"}]}}
{"type":"message","timestamp":"2026-10-28T03:00:06.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c2","name":"read","arguments":{"path":"SENTINEL-RETRY.txt"}}]}}
{"type":"message","timestamp":"2026-10-28T03:00:07.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c3","name":"read","arguments":{"path":"SENTINEL-RETRY.txt"}}]}}
"""

PI_INJECTED_ONLY = """\
{"type":"session","id":"{sid}","cwd":"{PROJECT}","timestamp":"2026-10-28T03:00:00.000Z","version":"0"}
{"type":"message","timestamp":"2026-10-28T03:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"<skill name=\\"demo\\">SENTINEL-INJECT-ONLY</skill>"}]}}
"""

# host -> (主会话模板, 只有注入内容的会话模板, 相对路径模板, 主会话 id, 空会话 id)
FIXTURES = {
    "claude": (CLAUDE_FIXTURE, CLAUDE_INJECTED_ONLY, CLAUDE_RELATIVE, CLAUDE_SID, CLAUDE_BARE_SID),
    "codex": (CODEX_FIXTURE, CODEX_INJECTED_ONLY, CODEX_RELATIVE, CODEX_SID, CODEX_BARE_SID),
    "pi": (PI_FIXTURE, PI_INJECTED_ONLY, PI_RELATIVE, PI_SID, PI_BARE_SID),
}

POINTER = re.compile(r"^- L(\d+) (\S+)：", re.MULTILINE)


def render(text, session_id):
    return (
        text.replace("{PROJECT}", PROJECT)
        .replace("{SECRET}", SECRET)
        .replace("{sid}", session_id)
        .replace("{LONG_USER}", "正常需求说明。" * 60 + "USER-AFTER-LIMIT")
        .replace("{LONG_FAILURE}", "工具失败说明。" * 40 + "FAILURE-AFTER-LIMIT")
    )


def write_session(path, text, session_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(text, session_id), encoding="utf-8")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-I", str(TOOL), *[str(arg) for arg in args]], capture_output=True, text=True, check=False
    )


def line_text(path, number):
    return path.read_text(encoding="utf-8").splitlines()[number - 1]


def sentinel_lines(path, sentinel):
    return [number for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if sentinel in line]


class DigestTests(unittest.TestCase):
    """三个 Host 跑同一套契约；每个 Host 一份主会话和一份只有注入内容的会话。"""

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="phe-digest-")
        cls.addClassCleanup(temp.cleanup)
        cls.tmp = Path(os.path.realpath(temp.name))
        cls.roots, cls.sources, cls.out_dirs, cls.indexes, cls.blocks = {}, {}, {}, {}, {}
        for host, (fixture, injected, relative, session_id, bare_id) in FIXTURES.items():
            root = cls.tmp / host
            write_session(root / relative.format(sid=session_id), fixture, session_id)
            write_session(root / relative.format(sid=bare_id), injected, bare_id)
            if host == "pi":
                write_session(root / "sessions/subagent/parent/run-1/session.jsonl", fixture, "excluded-subagent")
            out_dir = cls.tmp / f"digest-{host}"
            result = run_cli("digest", "--host", host, "--root", root, "--out-dir", out_dir, "--since", "2026-10-28")
            assert result.returncode == 0, result.stderr
            index = json.loads((out_dir / f"{host}-index.json").read_text(encoding="utf-8"))
            cls.roots[host] = root
            cls.sources[host] = root / relative.format(sid=session_id)
            cls.out_dirs[host] = out_dir
            cls.indexes[host] = index
            cls.blocks[host] = "".join(
                (out_dir / entry["file"]).read_text(encoding="utf-8") for entry in index["blocks"]
            )

    def test_human_messages_in_and_injected_out(self):
        for host, text in self.blocks.items():
            with self.subTest(host=host):
                self.assertIn("SENTINEL-USER-PLAIN", text)
                self.assertIn("SENTINEL-USER-CORRECTION", text)
                self.assertIn("SENTINEL-FAILURE", text)
                self.assertIn("SENTINEL-RETRY", text)
                self.assertNotIn("SENTINEL-INJECT", text)
                self.assertNotIn("USER-AFTER-LIMIT", text)
                self.assertNotIn("FAILURE-AFTER-LIMIT", text)
                for line in text.splitlines():
                    if line.startswith("- L"):
                        label, body = line.split("：", 1)
                        self.assertLessEqual(len(body), 300 if "用户" in label else 200)
                self.assertNotIn(SECRET, text)
                self.assertIn("<key>", text)
                self.assertIn(FIXTURES[host][3], text)
                self.assertIn(str(self.sources[host]), text)
                self.assertIn(PROJECT, text)
                self.assertIn("用户消息 2；工具调用 3；工具失败 1", text)

    def test_pointers_match_source_lines(self):
        for host, text in self.blocks.items():
            with self.subTest(host=host):
                source = self.sources[host]
                expected = {(number, "用户") for number in sentinel_lines(source, "SENTINEL-USER")}
                expected |= {(sentinel_lines(source, "SENTINEL-FAILURE")[0], "失败")}
                expected |= {(sentinel_lines(source, "SENTINEL-RETRY")[-1], "重试")}
                self.assertEqual({(int(number), label) for number, label in POINTER.findall(text)}, expected)
                numbers = [int(number) for number, _label in POINTER.findall(text)]
                self.assertEqual(numbers, sorted(numbers))
                for number, label in POINTER.findall(text):
                    with self.subTest(host=host, line=number, label=label):
                        self.assertIn("SENTINEL-", line_text(source, int(number)))

    def test_index_counts_skipped_sessions(self):
        for host, index in self.indexes.items():
            with self.subTest(host=host):
                self.assertEqual(index["host"], host)
                self.assertEqual(index["root"], str(self.roots[host]))
                self.assertEqual(index["since"], "2026-10-28")
                self.assertTrue(index["window_start"].startswith("2026-10-28T00:00:00"))
                self.assertEqual(index["sessions_total"], 2)
                self.assertEqual(index["sessions_included"], 1)
                self.assertEqual(index["sessions_skipped"], 1)
                self.assertEqual(index["sessions_unavailable"], 0)
                self.assertEqual(index["unavailable_sources"], [])
                self.assertGreaterEqual(len(index["blocks"]), 1)
                self.assertEqual(sum(entry["sessions"] for entry in index["blocks"]), 1)
                for entry in index["blocks"]:
                    path = self.out_dirs[host] / entry["file"]
                    self.assertEqual(entry["bytes"], path.stat().st_size)
                self.assertNotIn(FIXTURES[host][4], self.blocks[host])

    def test_collect_output_unchanged(self):
        for host in FIXTURES:
            with self.subTest(host=host):
                out = self.tmp / f"collect-{host}.json"
                result = run_cli("collect", "--host", host, "--root", self.roots[host], "--out", out)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(out.read_text(encoding="utf-8"))
                kinds = {episode["kind"] for episode in data["episodes"]}
                self.assertEqual(kinds, {"user_correction", "tool_failure", "retry"})
                plain = sentinel_lines(self.sources[host], "SENTINEL-USER-PLAIN")
                self.assertFalse({episode["line"] for episode in data["episodes"]} & set(plain))
                collected = [source for source in data["sources"] if source["source_path"] == str(self.sources[host])]
                self.assertEqual(collected[0]["episode_count"], len(data["episodes"]))


class DigestBlockTests(unittest.TestCase):
    """会话多到超过目标块大小时分块，会话不跨块。"""

    def test_sessions_do_not_span_blocks(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-digest-blocks-")
        self.addCleanup(temp.cleanup)
        tmp = Path(os.path.realpath(temp.name))
        root = tmp / "claude"
        sessions = 25
        session_ids = []
        for index in range(sessions):
            session_id = f"11111111-1111-4111-8111-{index:012d}"
            session_ids.append(session_id)
            lines = []
            for number in range(20):
                text = f"第 {number} 条消息 " + "补充说明。" * 12
                lines.append(
                    json.dumps(
                        {
                            "type": "user",
                            "sessionId": session_id,
                            "cwd": PROJECT,
                            "timestamp": f"2026-10-28T0{index % 10}:00:{number:02d}Z",
                            "message": {"role": "user", "content": text},
                        },
                        ensure_ascii=False,
                    )
                )
            write_session(root / CLAUDE_RELATIVE.format(sid=session_id), "\n".join(lines) + "\n", session_id)
        out_dir = tmp / "digest"
        result = run_cli("digest", "--host", "claude", "--root", root, "--out-dir", out_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        index = json.loads((out_dir / "claude-index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["sessions_included"], sessions)
        self.assertGreaterEqual(len(index["blocks"]), 2)
        seen = {}
        for entry in index["blocks"]:
            text = (out_dir / entry["file"]).read_text(encoding="utf-8")
            for session_id in session_ids:
                if f"## {session_id} " in text:
                    seen[session_id] = seen.get(session_id, 0) + 1
        self.assertEqual(seen, dict.fromkeys(session_ids, 1))


if __name__ == "__main__":
    unittest.main()
