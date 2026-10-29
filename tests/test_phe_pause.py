"""主动暂停与续跑：--pause-after-new-slots 只停 Target 启动，不改停止条件和墙钟预算。

这些用例都不启动 Target：run_slot 被替换成只写一条完整记录的桩函数。
"""

import contextlib
import io
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

phe = test_phe.phe
ROOT = Path(__file__).resolve().parents[1]


class PauseTests(unittest.TestCase):
    def setUp(self):
        # 3 组 × 1 次 = 3 个 slot 的合成实验，harness 用临时文件。
        self.temp = tempfile.TemporaryDirectory(prefix="phe-test-")
        self.addCleanup(self.temp.cleanup)
        self.exp = Path(self.temp.name)
        self.p = test_phe.proposal(count=1, controls=False)
        del self.p["rules"]["required_control"]
        self.cfg: dict[str, Any] = {"harness": {}}
        for g, v in self.p["harness"]["variants"].items():
            h = self.exp / (g + ".md")
            h.write_text("synthetic harness")
            self.cfg["harness"][g] = str(h)
            v["snapshot_sha256"] = phe.sha(h)

    def freeze(self):
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        self.p["runner"] = {"config_sha256": phe.sha(self.exp / "config.json")}
        (self.exp / "proposal.json").write_text(json.dumps(self.p))
        digest = phe.sha(self.exp / "proposal.json")
        (self.exp / "proposal.sha256").write_text(digest)
        (self.exp / "approval.json").write_text(json.dumps({"proposal_sha256": digest}))

    def slot_stub(self, calls, **changes):
        """按 schedule 写一条完整记录，模拟 run_slot 已经落盘后的状态。"""

        def write_record(exp, cfg, item, budget, **_kwargs):
            calls.append(item["slot"])
            record = {
                "slot": item["slot"],
                "group": item["group"],
                "exit_code": 0,
                "process_timed_out": False,
                phe.TOK: 100,
                "estimated_cost_usd_v1": 0.1,
                "test_pass": True,
                "contract_pass": True,
                "user_files_unchanged": True,
                "wall_time_v1": 5.0,
                "oracle_seconds": 1.0,
                **changes,
            }
            with (exp / "runs.jsonl").open("a") as f:
                f.write(json.dumps(record) + "\n")

        return write_record

    def schedule(self):
        return [item["slot"] for item in self.p["experiment"]["schedule"]]

    def paused_run(self, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            phe.run(*args, **kwargs)
        return out.getvalue()

    def test_pause_after_first_slot_keeps_next_target_unstarted(self):
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls)):
            out = self.paused_run(self.exp, pause_after_new_slots=1)
        self.assertEqual(calls, self.schedule()[:1])
        self.assertEqual([r["slot"] for r in phe.records(self.exp)], self.schedule()[:1])
        self.assertIn("paused:", out)
        # 主动暂停不是停止事件：不写 stop.json，也不留下伪造的原因。
        self.assertFalse((self.exp / "stop.json").exists())

    def test_resume_after_pause_adds_no_slot_and_keeps_schedule(self):
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls)):
            first = self.paused_run(self.exp, pause_after_new_slots=1)
            second = self.paused_run(self.exp, pause_after_new_slots=1)
            third = self.paused_run(self.exp)
        self.assertIn("paused:", first)
        self.assertIn("paused:", second)
        self.assertNotIn("paused:", third)
        self.assertEqual([r["slot"] for r in phe.records(self.exp)], self.schedule())
        self.assertEqual(calls, self.schedule())
        self.assertIsNone(phe.stop_reason(self.exp))

    def test_no_new_slot_does_not_pause(self):
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls)):
            self.paused_run(self.exp)
            calls.clear()
            out = self.paused_run(self.exp, pause_after_new_slots=1)
        self.assertEqual(calls, [])
        self.assertNotIn("paused:", out)
        self.assertIsNone(phe.stop_reason(self.exp))

    def test_pause_cannot_bypass_cost_budget_stop(self):
        # 第 2 个 slot 的准入检查先于暂停：费用不够时记 pivot，而不是记暂停。
        self.p["experiment"]["budget"]["max_estimated_cost_usd_total"] = 1.0
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls)):
            out = self.paused_run(self.exp, pause_after_new_slots=1)
        self.assertEqual(calls, self.schedule()[:1])
        self.assertNotIn("paused:", out)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:cost_budget_exhausted")

    def test_pause_cannot_bypass_record_stop(self):
        # 刚完成的 slot 触发停止条件（用量缺失）时，暂停让位给停止。
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls, **{phe.TOK: None})):
            out = self.paused_run(self.exp, pause_after_new_slots=1)
        self.assertEqual(calls, self.schedule()[:1])
        self.assertNotIn("paused:", out)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:telemetry_missing")

    def test_pause_cannot_bypass_runner_crash(self):
        # 第 2 个 slot 崩在启动路径上：按崩溃处理，不记成一次主动暂停。
        self.freeze()
        calls: list[int] = []
        stub = self.slot_stub(calls)

        def crash(exp, cfg, item, budget, **kwargs):
            if item["slot"] != self.schedule()[0]:
                raise OSError("simulated runner failure")
            stub(exp, cfg, item, budget, **kwargs)

        with patch.object(phe, "run_slot", side_effect=crash):
            out = self.paused_run(self.exp, pause_after_new_slots=2)
        self.assertEqual(calls, self.schedule()[:1])
        self.assertNotIn("paused:", out)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:runner_crashed_no_record")

    def test_pause_resume_does_not_reset_wall_clock(self):
        # slot_wall = 10 + 1 + 10 = 21。第一个 slot 记 5 + 1 = 6 秒。
        # 上限 26：旧实现续跑从 0 计时（21 ≤ 26）会继续启动，累计执行时长后 6 + 21 > 26 必须停。
        self.p["experiment"]["budget"]["max_experiment_wall_seconds"] = 26
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls)):
            self.paused_run(self.exp, pause_after_new_slots=1)
            calls.clear()
            out = self.paused_run(self.exp)
        self.assertEqual(calls, [])
        self.assertNotIn("paused:", out)
        self.assertEqual(phe.stop_reason(self.exp), "pivot:wall_budget_exhausted")
        self.assertEqual([r["slot"] for r in phe.records(self.exp)], self.schedule()[:1])

    def test_human_wait_between_calls_does_not_consume_wall_budget(self):
        # 累计口径只算记录里的执行时长：两次调用之间的等待不计入。
        # 上限 30：已记录 6 秒 + 最坏一个 slot 21 秒 = 27 ≤ 30，等待多久都不影响。
        self.p["experiment"]["budget"]["max_experiment_wall_seconds"] = 30
        self.freeze()
        calls: list[int] = []
        with patch.object(phe, "run_slot", side_effect=self.slot_stub(calls)):
            self.paused_run(self.exp, pause_after_new_slots=1)
            out = self.paused_run(self.exp)
        self.assertNotIn("paused:", out)
        self.assertEqual(calls, self.schedule())
        self.assertIsNone(phe.stop_reason(self.exp))


class CliPauseTests(unittest.TestCase):
    """集成：本地桩程序充当 Target（不调用模型），只验证 CLI 参数的退出码和 stdout。"""

    def test_cli_pause_then_resume(self):
        subprocess.run(["git", "init", "-q", self.base], check=True)
        (self.base / "task.txt").write_text("fixture")
        trusted = self.exp / "trusted.txt"
        trusted.write_text("oracle fixture")
        stub = "import json;print(json.dumps({'modelUsage': {'stub': {'inputTokens': 100}}, 'total_cost_usd': 0.1}))"
        self.cfg.update(
            {
                "base_dir": str(self.base),
                "experiment_id": "synthetic",
                "experiment_dir": str(self.exp),
                "target_argv": [sys.executable, "-I", "-c", stub],
                "env": {},
                "oracle_test_src": str(trusted),
                "oracle_test_dest": "oracle.txt",
                "oracle_cwd": ".",
                "oracle_argv": [sys.executable, "-I", "-c", "print('Tests 1 passed')"],
                "nonce_sha256": "unused",
            }
        )
        del self.p["rules"]["required_control"]
        self.freeze()
        command = [sys.executable, "-I", str(ROOT / "tools/phe.py"), "run", str(self.exp)]
        first = subprocess.run([*command, "--pause-after-new-slots", "1"], capture_output=True, text=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("paused:", first.stdout)
        self.assertEqual(len(phe.records(self.exp)), 1)
        self.assertFalse((self.exp / "stop.json").exists())
        second = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("paused:", second.stdout)
        self.assertEqual([r["slot"] for r in phe.records(self.exp)], self.schedule())
        self.assertIsNone(phe.stop_reason(self.exp))

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-pause-cli-")
        self.addCleanup(temp.cleanup)
        self.exp = Path(temp.name)
        self.base = self.exp / "base"
        self.base.mkdir()
        self.p = test_phe.proposal(count=1, controls=False)
        self.cfg: dict[str, Any] = {"harness": {}}
        for g, v in self.p["harness"]["variants"].items():
            h = self.exp / (g + ".md")
            h.write_text("synthetic harness")
            self.cfg["harness"][g] = str(h)
            v["snapshot_sha256"] = phe.sha(h)
        self.addCleanup(self.clean_workdirs)

    def clean_workdirs(self):
        for record in phe.records(self.exp):
            shutil.rmtree(Path(record["workdir"]).parent, ignore_errors=True)

    def freeze(self):
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        self.p["runner"] = {"config_sha256": phe.sha(self.exp / "config.json")}
        (self.exp / "proposal.json").write_text(json.dumps(self.p))
        digest = phe.sha(self.exp / "proposal.json")
        (self.exp / "proposal.sha256").write_text(digest)
        (self.exp / "approval.json").write_text(json.dumps({"proposal_sha256": digest}))

    def schedule(self):
        return [item["slot"] for item in self.p["experiment"]["schedule"]]


if __name__ == "__main__":
    unittest.main()
