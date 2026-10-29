"""rehearse_host_handoff 的故障注入回归：失败保留现场、报告落盘才清理、报告目录不覆盖。

故障通过包装真实 `phe.py` 调用来注入，只改 status 的返回；用例不复制脚本内部的断言文本。
"""

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rehearse_host_handoff", ROOT / "tools/m2/rehearse_host_handoff.py")
assert SPEC is not None and SPEC.loader is not None
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


def status_rewriter(mutate):
    """包住真实 `phe` 调用，只替换 status 的返回，模拟 CLI 异常。"""
    real_phe = rehearsal.Rehearsal.phe

    def phe(self, *argv):
        proc = real_phe(self, *argv)
        return mutate(proc) if argv and argv[0] == "status" else proc

    return phe


class RehearsalRun(unittest.TestCase):
    """每个用例用临时报告根和临时合成仓库，不碰仓库里的 `experiments/`。"""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="phe-handoff-test-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = tmp
        self.sandbox = tmp / "sandbox"
        self.sandbox.mkdir()
        self.report_root = tmp / "reports"

    def run_main(self, phe=None):
        patches = [
            mock.patch.object(rehearsal, "REPORT_ROOT", self.report_root),
            mock.patch.object(rehearsal.tempfile, "mkdtemp", lambda prefix="": str(self.sandbox)),
        ]
        if phe is not None:
            patches.append(mock.patch.object(rehearsal.Rehearsal, "phe", phe))
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = rehearsal.main()
        return code, err.getvalue()

    def reports(self):
        if not self.report_root.is_dir():
            return []
        return sorted(p for p in self.report_root.iterdir() if (p / "report.md").is_file())

    def report_text(self):
        reports = self.reports()
        self.assertEqual(len(reports), 1, f"应只有一份报告，实际 {reports}")
        return (reports[0] / "report.md").read_text()


class FailureKeepsEvidenceTests(RehearsalRun):
    def test_missing_stage_writes_partial_report_and_keeps_sandbox(self):
        def drop_stage(proc):
            payload = json.loads(proc.stdout)
            payload.pop("stage")
            return subprocess.CompletedProcess(proc.args, 0, json.dumps(payload), proc.stderr)

        code, err = self.run_main(phe=status_rewriter(drop_stage))

        self.assertNotEqual(code, 0)
        text = self.report_text()
        self.assertIn("中断", text)
        self.assertIn("KeyError", text)
        self.assertIn("命令与退出码", text)
        self.assertIn("--step draft", text, "异常前已执行的命令必须留在报告里")
        self.assertTrue(self.sandbox.exists(), "失败时必须保留合成仓库现场")
        self.assertIn(str(self.sandbox), err)

    def test_status_cli_failure_writes_failure_report_and_keeps_sandbox(self):
        real_phe = rehearsal.Rehearsal.phe

        def status_fails(run, *argv):
            """模拟 `status` 退出 2：返回并记录一次失败调用，其余命令走真实 CLI。"""
            if argv and argv[0] != "status":
                return real_phe(run, *argv)
            cmd = [sys.executable, "-I", str(run.sandbox / "tools/phe.py"), *map(str, argv)]
            stderr = "phe status: 实验目录不存在"
            run.calls.append({"argv": cmd, "exit": 2, "stdout": "", "stderr": stderr})
            return subprocess.CompletedProcess(cmd, 2, "", stderr)

        code, err = self.run_main(phe=status_fails)

        self.assertNotEqual(code, 0)
        text = self.report_text()
        self.assertIn("场景失败", text)
        self.assertIn("exit 2", text, "已执行命令的退出码必须留在报告里")
        self.assertIn("实验目录不存在", text)
        self.assertTrue(self.sandbox.exists(), "失败时必须保留合成仓库现场")
        self.assertIn(str(self.sandbox), err)

    def test_report_save_failure_is_nonzero_and_keeps_sandbox(self):
        blocked = self.tmp / "blocked"
        blocked.write_text("")
        self.report_root = blocked / "reports"

        code, err = self.run_main()

        self.assertNotEqual(code, 0)
        self.assertIn("报告保存失败", err)
        self.assertTrue(self.sandbox.exists(), "报告没落盘时不能清理现场")
        self.assertIn(str(self.sandbox), err)


class KeyboardInterruptTests(RehearsalRun):
    def test_keyboard_interrupt_after_real_cli_keeps_evidence_and_writes_failure_report(self):
        """真实 CLI 写下 proposal 和 host_log 后按 Ctrl-C：报告判失败，产物和现场仍可读。"""
        real_phe = rehearsal.Rehearsal.phe

        def interrupt_after_draft(run, *argv):
            """真实 `log ... --step draft` 落盘后抛 KeyboardInterrupt（继承 BaseException）。"""
            proc = real_phe(run, *argv)
            if argv[:1] == ("log",) and "draft" in argv:
                raise KeyboardInterrupt
            return proc

        code, err = self.run_main(phe=interrupt_after_draft)

        self.assertNotEqual(code, 0)
        text = self.report_text()
        self.assertIn("中断", text)
        self.assertIn("KeyboardInterrupt", text)
        self.assertIn("--step draft", text, "中断前已执行的命令必须留在报告里")
        exp = self.sandbox / "repo/experiments/synthetic-handoff"
        self.assertEqual((exp / "proposal.json").read_bytes(), rehearsal.PROPOSAL_BYTES)
        entries = [json.loads(line) for line in (exp / "host_log.jsonl").read_text().split("\n") if line.strip()]
        self.assertEqual(entries[-1]["step"], "draft")
        self.assertTrue(self.sandbox.exists(), "中断时必须保留合成仓库现场")
        self.assertIn(str(self.sandbox), err)


class ReportPersistenceTests(RehearsalRun):
    def test_success_writes_report_then_removes_sandbox(self):
        code, err = self.run_main()

        self.assertEqual(code, 0, err)
        self.assertIn("场景通过", self.report_text())
        self.assertFalse(self.sandbox.exists(), "成功时应在报告落盘后清理现场")


class RunDirTests(unittest.TestCase):
    def test_same_second_runs_get_separate_dirs(self):
        tmp = Path(tempfile.mkdtemp(prefix="phe-handoff-dir-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "reports"
        stamp = "20260101T000000Z"

        first_id, first = rehearsal.reserve_run_dir(root, stamp)
        (first / "report.md").write_text("first run")
        second_id, second = rehearsal.reserve_run_dir(root, stamp)

        self.assertEqual(first_id, stamp)
        self.assertNotEqual(first_id, second_id)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "report.md").read_text(), "first run")
        self.assertTrue(second.is_dir())


if __name__ == "__main__":
    unittest.main()
