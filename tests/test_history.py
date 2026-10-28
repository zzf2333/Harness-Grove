"""tools/history.py collect 的 Episode 契约：三个 Host 各一份手写合成 fixture，覆盖三种 kind 与指针回读。

fixture 全部手写合成，不含任何真实会话内容；路径和名字都是虚构的。
fixture 直接写成 JSONL 文本，一行一条记录，行号就是 Episode 的 line 指针。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/history.py"

PROJECT = "/home/alice/proj"
CLAUDE_SID = "11111111-1111-4111-8111-111111111111"
CODEX_SID = "22222222-2222-4222-8222-222222222222"
PI_SID = "33333333-3333-4333-8333-333333333333"
# 合成密钥：分段拼接，文件里不出现形似真实密钥的字面量。
SECRET = "sk-" + "synthetic" + "0" * 30

CLAUDE_RELATIVE = "projects/-home-alice-proj/{CLAUDE_SID}.jsonl"
CODEX_RELATIVE = "sessions/2026/10/28/rollout-2026-10-28T01-00-00-{CODEX_SID}.jsonl"
PI_RELATIVE = "sessions/--home-alice-proj--/2026-10-28T01-00-00-000Z_{PI_SID}.jsonl"

# 第 1 行用户纠正；第 2–3 行一次失败的 Bash 调用；第 4–5 行连续两次相同调用。
CLAUDE_FIXTURE = """\
{"type":"user","sessionId":"{CLAUDE_SID}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:00Z","message":{"role":"user","content":"不对，SENTINEL-CORRECTION key={SECRET}"}}
{"type":"assistant","sessionId":"{CLAUDE_SID}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:05Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_01","name":"Bash","input":{"command":"echo a"}}]}}
{"type":"user","sessionId":"{CLAUDE_SID}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:06Z","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01","is_error":true,"content":"SENTINEL-FAILURE boom"}]}}
{"type":"assistant","sessionId":"{CLAUDE_SID}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:07Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_02","name":"Bash","input":{"command":"echo SENTINEL-RETRY"}}]}}
{"type":"assistant","sessionId":"{CLAUDE_SID}","cwd":"{PROJECT}","timestamp":"2026-10-28T01:00:08Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_03","name":"Bash","input":{"command":"echo SENTINEL-RETRY"}}]}}
"""

# 第 1 行会话元数据；第 2 行用户纠正；第 3–5 行一次工具调用、退出码非 0、Script failed 输出；
# 第 6–7 行连续两次相同调用。
CODEX_FIXTURE = """\
{"type":"session_meta","timestamp":"2026-10-28T02:00:00Z","payload":{"id":"{CODEX_SID}","cwd":"{PROJECT}"}}
{"type":"response_item","timestamp":"2026-10-28T02:00:01Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"不是这样，SENTINEL-CORRECTION key={SECRET}"}]}}
{"type":"response_item","timestamp":"2026-10-28T02:00:02Z","payload":{"type":"custom_tool_call","call_id":"call_1","name":"shell","input":"echo a"}}
{"type":"event_msg","timestamp":"2026-10-28T02:00:03Z","payload":{"type":"item_completed","item":{"id":"exec-1","exit_code":2,"command":["bash","-lc","SENTINEL-FAILURE"]}}}
{"type":"response_item","timestamp":"2026-10-28T02:00:04Z","payload":{"type":"custom_tool_call_output","call_id":"call_1","output":[{"type":"output_text","text":"Script failed SENTINEL-SCRIPT"}]}}
{"type":"response_item","timestamp":"2026-10-28T02:00:05Z","payload":{"type":"custom_tool_call","call_id":"call_2","name":"shell","input":"echo SENTINEL-RETRY"}}
{"type":"response_item","timestamp":"2026-10-28T02:00:06Z","payload":{"type":"custom_tool_call","call_id":"call_3","name":"shell","input":"echo SENTINEL-RETRY"}}
"""

# 第 1 行会话头；第 2 行用户纠正；第 3–4 行一次失败的 read；第 5–6 行连续两次相同调用。
PI_FIXTURE = """\
{"type":"session","id":"{PI_SID}","cwd":"{PROJECT}","timestamp":"2026-10-28T03:00:00.000Z","version":"0"}
{"type":"message","timestamp":"2026-10-28T03:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"错了，SENTINEL-CORRECTION key={SECRET}"}]}}
{"type":"message","timestamp":"2026-10-28T03:00:02.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c1","name":"read","arguments":{"path":"a.txt"}}]}}
{"type":"message","timestamp":"2026-10-28T03:00:03.000Z","message":{"role":"toolResult","toolName":"read","toolCallId":"c1","isError":true,"content":[{"type":"text","text":"SENTINEL-FAILURE no such file"}]}}
{"type":"message","timestamp":"2026-10-28T03:00:04.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c2","name":"read","arguments":{"path":"SENTINEL-RETRY.txt"}}]}}
{"type":"message","timestamp":"2026-10-28T03:00:05.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"c3","name":"read","arguments":{"path":"SENTINEL-RETRY.txt"}}]}}
"""

FIXTURES = {
    "claude": (CLAUDE_FIXTURE, CLAUDE_RELATIVE, CLAUDE_SID),
    "codex": (CODEX_FIXTURE, CODEX_RELATIVE, CODEX_SID),
    "pi": (PI_FIXTURE, PI_RELATIVE, PI_SID),
}


def render(text):
    for name, value in (
        ("PROJECT", PROJECT),
        ("SECRET", SECRET),
        ("CLAUDE_SID", CLAUDE_SID),
        ("CODEX_SID", CODEX_SID),
        ("PI_SID", PI_SID),
    ):
        text = text.replace("{" + name + "}", value)
    return text


def write_fixture(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(text), encoding="utf-8")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), "collect", *[str(arg) for arg in args]], capture_output=True, text=True, check=False
    )


def line_text(episode):
    lines = Path(episode["source_path"]).read_text(encoding="utf-8").splitlines()
    return lines[episode["line"] - 1]


def sentinel_lines(path, sentinel):
    return [number for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if sentinel in line]


def episodes_of(data, kind):
    return [episode for episode in data["episodes"] if episode["kind"] == kind]


class CollectTests(unittest.TestCase):
    """三个 Host 跑同一套契约；每个 Host 一份合成 fixture。"""

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-")
        cls.addClassCleanup(temp.cleanup)
        cls.tmp = Path(os.path.realpath(temp.name))
        cls.data, cls.sources, cls.sessions = {}, {}, {}
        for host, (fixture, relative, session_id) in FIXTURES.items():
            root = cls.tmp / host
            source = root / render(relative)
            write_fixture(source, fixture)
            out = cls.tmp / f"{host}.json"
            result = run_cli("--host", host, "--root", root, "--out", out)
            assert result.returncode == 0, result.stderr
            cls.data[host] = json.loads(out.read_text(encoding="utf-8"))
            cls.sources[host] = source
            cls.sessions[host] = session_id

    def test_episode_fields_and_pointer(self):
        for host, data in self.data.items():
            for episode in data["episodes"]:
                with self.subTest(host=host, line=episode["line"], kind=episode["kind"]):
                    self.assertEqual(
                        set(episode),
                        {
                            "host",
                            "source_path",
                            "session_id",
                            "line",
                            "sha256",
                            "project",
                            "ts",
                            "kind",
                            "excerpt",
                            "available",
                        },
                    )
                    self.assertEqual(episode["host"], host)
                    self.assertEqual(episode["source_path"], str(self.sources[host]))
                    self.assertEqual(episode["project"], PROJECT)
                    self.assertEqual(episode["sha256"], hashlib.sha256(self.sources[host].read_bytes()).hexdigest())
                    self.assertEqual(episode["session_id"], self.sessions[host])
                    self.assertTrue(episode["available"])
                    self.assertIsInstance(episode["line"], int)
                    self.assertGreaterEqual(episode["line"], 1)
                    self.assertLessEqual(len(episode["excerpt"]), 300)
                    self.assertTrue(line_text(episode).strip(), "指针指向空行")

    def test_three_kinds_present(self):
        for host, data in self.data.items():
            with self.subTest(host=host):
                self.assertEqual(
                    {episode["kind"] for episode in data["episodes"]}, {"user_correction", "tool_failure", "retry"}
                )

    def test_source_entry_matches_file(self):
        for host, data in self.data.items():
            with self.subTest(host=host):
                self.assertEqual(len(data["sources"]), 1)
                source = data["sources"][0]
                self.assertEqual(source["host"], host)
                self.assertEqual(source["source_path"], str(self.sources[host]))
                self.assertEqual(source["session_id"], self.sessions[host])
                self.assertTrue(source["available"])
                self.assertEqual(source["episode_count"], len(data["episodes"]))
                self.assertEqual(source["sha256"], hashlib.sha256(self.sources[host].read_bytes()).hexdigest())
                self.assertNotIn("missing_reason", source)

    def test_correction_pointer_and_redaction(self):
        for host, data in self.data.items():
            with self.subTest(host=host):
                corrections = episodes_of(data, "user_correction")
                self.assertEqual(len(corrections), 1)
                episode = corrections[0]
                self.assertIn("SENTINEL-CORRECTION", line_text(episode))
                self.assertIn("SENTINEL-CORRECTION", episode["excerpt"])
                self.assertNotIn(SECRET, episode["excerpt"])
                self.assertIn("<key>", episode["excerpt"])

    def test_tool_failure_pointer(self):
        for host, data in self.data.items():
            with self.subTest(host=host):
                failures = episodes_of(data, "tool_failure")
                self.assertGreaterEqual(len(failures), 1)
                self.assertTrue(all("SENTINEL-" in line_text(episode) for episode in failures))

    def test_retry_pointer_is_second_identical_call(self):
        for host, data in self.data.items():
            with self.subTest(host=host):
                retries = episodes_of(data, "retry")
                self.assertEqual(len(retries), 1)
                self.assertEqual(retries[0]["line"], sentinel_lines(self.sources[host], "SENTINEL-RETRY")[1])

    def test_host_specific_failure_signals(self):
        claude = episodes_of(self.data["claude"], "tool_failure")
        self.assertEqual(len(claude), 1)
        self.assertIn("Bash", claude[0]["excerpt"])
        self.assertIn("SENTINEL-FAILURE", line_text(claude[0]))

        codex = episodes_of(self.data["codex"], "tool_failure")
        self.assertEqual(len(codex), 2)
        pointers = [line_text(episode) for episode in codex]
        self.assertTrue(any("SENTINEL-FAILURE" in line for line in pointers), "退出码非 0 未识别")
        self.assertTrue(any("SENTINEL-SCRIPT" in line for line in pointers), "Script failed 未识别")

        pi = episodes_of(self.data["pi"], "tool_failure")
        self.assertEqual(len(pi), 1)
        self.assertIn("read", pi[0]["excerpt"])
        self.assertIn("SENTINEL-FAILURE", line_text(pi[0]))

    def test_timestamp_passthrough(self):
        self.assertEqual(episodes_of(self.data["claude"], "user_correction")[0]["ts"], "2026-10-28T01:00:00Z")
        self.assertEqual(episodes_of(self.data["pi"], "user_correction")[0]["ts"], "2026-10-28T03:00:01.000Z")


class RetryRunTests(unittest.TestCase):
    """连续相同的调用只在第一对记一次 retry；signature 一变就重新计数。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-history-retry-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.root = self.tmp / "claude"
        self.source = self.root / render(CLAUDE_RELATIVE)
        self.out = self.tmp / "episodes.json"

    def write_calls(self, commands):
        lines = []
        for index, command in enumerate(commands, 1):
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": CLAUDE_SID,
                        "cwd": PROJECT,
                        "timestamp": f"2026-10-28T04:00:{index:02d}Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": f"toolu_{index}",
                                    "name": "Bash",
                                    "input": {"command": command},
                                }
                            ],
                        },
                    }
                )
            )
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def retry_lines(self):
        result = run_cli("--host", "claude", "--root", self.root, "--out", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        return [episode["line"] for episode in episodes_of(data, "retry")]

    def test_run_of_four_identical_calls_records_one_retry(self):
        self.write_calls(["echo poll"] * 4)
        self.assertEqual(self.retry_lines(), [2])

    def test_signature_change_resets_the_run(self):
        self.write_calls(["echo poll", "echo poll", "echo other", "echo poll", "echo poll"])
        self.assertEqual(self.retry_lines(), [2, 5])


if __name__ == "__main__":
    unittest.main()
