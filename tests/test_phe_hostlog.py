"""host_log 续作记录与 status 阶段推导的合成实验目录验收。"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("phe_hostlog", ROOT / "tools/phe_hostlog.py")
assert SPEC is not None and SPEC.loader is not None
hostlog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hostlog)

# 合成提案：带中文、没有结尾换行，用来核对冻结哈希对字节敏感。
PROPOSAL = '{"experiment_id": "synthetic-01", "note": "合成提案，无真实路径"}'.encode()
PROPOSAL_SHA = hashlib.sha256(PROPOSAL).hexdigest()


def write_stage(exp: Path, stage: str) -> None:
    """按阶段顺序写证据文件；status 只看存在性，内容为空即可。"""
    for name, name_stage in hostlog.GATED:
        if hostlog.STAGES.index(name_stage) <= hostlog.STAGES.index(stage):
            (exp / name).write_text("")


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): b"<dir>" if p.is_dir() else p.read_bytes() for p in sorted(root.rglob("*"))}


class SyntheticExperiment(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="phe-hostlog-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = tmp
        self.exp = self.root / "experiments" / "e1"
        self.exp.mkdir(parents=True)

    def freeze(self):
        (self.exp / "proposal.json").write_bytes(PROPOSAL)
        (self.exp / "proposal.sha256").write_text(PROPOSAL_SHA + "\n")

    def run_log(self, experiment: Path, **kwargs) -> tuple[int, str]:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = hostlog.log(experiment, root=self.root, **kwargs)
        return code, err.getvalue()


class StageDerivationTests(SyntheticExperiment):
    def test_each_stage_comes_from_its_own_evidence_file(self):
        for stage in hostlog.STAGES:
            with self.subTest(stage=stage):
                shutil.rmtree(self.exp)
                self.exp.mkdir()
                write_stage(self.exp, stage)
                report = hostlog.derive(self.exp)
                self.assertEqual(report["stage"], stage)
                self.assertIsNone(report["last_log"])
                self.assertFalse(report["inconsistent"])
                self.assertEqual(report["notes"], [])
                expected = [n for n, s in hostlog.GATED if hostlog.STAGES.index(s) <= hostlog.STAGES.index(stage)]
                self.assertEqual([n for n, present in report["evidence"].items() if present], expected)

    def test_evidence_covers_staged_and_unstaged_files(self):
        report = hostlog.derive(self.exp)
        self.assertEqual(
            sorted(report["evidence"]),
            sorted([n for n, _ in hostlog.GATED] + list(hostlog.UNSTAGED)),
        )

    def test_gap_in_the_chain_is_a_note_not_a_failure(self):
        (self.exp / "runs.jsonl").write_text("")
        report = hostlog.derive(self.exp)
        self.assertEqual(report["stage"], "running")
        self.assertEqual(len(report["notes"]), 1)
        self.assertIn("proposal.json", report["notes"][0])
        self.assertIn("approval.json", report["notes"][0])


class LogTests(SyntheticExperiment):
    def test_log_appends_entries_and_leaves_proposal_bytes_and_hash_alone(self):
        self.freeze()
        before = (self.exp / "proposal.json").read_bytes()
        self.assertEqual(self.run_log(self.exp, host="claude", step="draft", note="起草")[0], 0)
        self.assertEqual(self.run_log(self.exp, host="codex", step="freeze")[0], 0)
        self.assertEqual((self.exp / "proposal.json").read_bytes(), before)
        self.assertEqual(hashlib.sha256(before).hexdigest(), (self.exp / "proposal.sha256").read_text().strip())
        self.assertEqual(
            sorted(p.name for p in self.exp.iterdir()), ["host_log.jsonl", "proposal.json", "proposal.sha256"]
        )
        lines = (self.exp / "host_log.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(set(first), {"ts", "host", "step", "note"})
        self.assertEqual((first["host"], first["step"], first["note"]), ("claude", "draft", "起草"))
        self.assertEqual((json.loads(lines[1])["host"], json.loads(lines[1])["step"]), ("codex", "freeze"))
        self.assertEqual(json.loads(lines[1])["note"], "")

    def test_existing_unterminated_line_is_not_merged(self):
        self.freeze()
        legacy = '{"ts": "2026-10-28T10:00:00+0800", "host": "pi", "step": "draft", "note": "旧"}'
        (self.exp / "host_log.jsonl").write_text(legacy)
        self.assertEqual(self.run_log(self.exp, host="claude", step="freeze")[0], 0)
        text = (self.exp / "host_log.jsonl").read_text()
        self.assertTrue(text.startswith(legacy + "\n"))
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(json.loads(text.splitlines()[1])["step"], "freeze")

    def test_note_length_limit_is_200_chars(self):
        self.assertEqual(self.run_log(self.exp, host="pi", step="report", note="字" * 200)[0], 0)
        self.assertEqual(self.run_log(self.exp, host="pi", step="report", note="字" * 201)[0], 2)
        self.assertEqual(len((self.exp / "host_log.jsonl").read_text().splitlines()), 1)

    def test_invalid_arguments_exit_2_without_writing(self):
        self.freeze()
        before = sorted(p.name for p in self.exp.iterdir())
        cases = (
            ("host", {"host": "cursor", "step": "draft"}),
            ("step", {"host": "claude", "step": "start"}),
            ("note", {"host": "claude", "step": "draft", "note": "字" * 201}),
        )
        for name, kwargs in cases:
            with self.subTest(case=name):
                code, err = self.run_log(self.exp, **kwargs)
                self.assertEqual(code, 2)
                self.assertIn("phe log:", err)
                self.assertEqual(sorted(p.name for p in self.exp.iterdir()), before)

    def test_experiment_outside_experiments_exits_2(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.assertEqual(self.run_log(outside, host="claude", step="draft")[0], 2)
        self.assertEqual(list(outside.iterdir()), [])

    def test_experiments_root_itself_is_not_an_experiment(self):
        self.assertEqual(self.run_log(self.root / "experiments", host="claude", step="draft")[0], 2)
        self.assertEqual(list((self.root / "experiments").iterdir()), [self.exp])

    def test_symlink_escape_exits_2(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "experiments" / "escape"
        link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.run_log(link, host="claude", step="draft")[0], 2)
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_experiment_dir_exits_2(self):
        missing = self.root / "experiments" / "nope"
        code, err = self.run_log(missing, host="claude", step="draft")
        self.assertEqual(code, 2)
        self.assertIn("不存在", err)
        self.assertFalse(missing.exists())

    def test_symlinked_host_log_exits_2(self):
        target = self.root / "elsewhere.jsonl"
        target.write_text("")
        (self.exp / "host_log.jsonl").symlink_to(target)
        code, err = self.run_log(self.exp, host="claude", step="draft")
        self.assertEqual(code, 2)
        self.assertIn("软链", err)
        self.assertEqual(target.read_text(), "")
        self.assertTrue((self.exp / "host_log.jsonl").is_symlink())

    def test_hardlinked_host_log_exits_2_and_target_bytes_unchanged(self):
        self.freeze()
        outside = self.root / "outside.txt"
        outside.write_text("outside\n")
        for target in (self.exp / "proposal.json", outside):
            with self.subTest(target=target.name):
                log = self.exp / "host_log.jsonl"
                log.unlink(missing_ok=True)
                os.link(target, log)
                before = target.read_bytes()
                code, err = self.run_log(self.exp, host="claude", step="draft")
                self.assertEqual(code, 2)
                self.assertIn("硬链接", err)
                self.assertEqual(target.read_bytes(), before)
        self.assertEqual(hashlib.sha256((self.exp / "proposal.json").read_bytes()).hexdigest(), PROPOSAL_SHA)

    def test_unicode_line_separator_in_note_round_trips(self):
        note = "第一段\u2028第二段\u2029第三段"
        self.assertEqual(self.run_log(self.exp, host="pi", step="draft", note=note)[0], 0)
        report = hostlog.derive(self.exp)
        self.assertEqual(report["notes"], [])
        self.assertEqual(report["last_log"]["note"], note)


class StatusTests(SyntheticExperiment):
    def test_log_ahead_of_file_evidence_is_inconsistent(self):
        write_stage(self.exp, "approved")
        self.run_log(self.exp, host="codex", step="run", note="开始运行")
        report = hostlog.derive(self.exp)
        self.assertEqual(report["stage"], "approved")
        self.assertTrue(report["inconsistent"])
        self.assertEqual(report["last_log"]["step"], "run")
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = hostlog.status(self.exp, root=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["stage"], "approved")

    def test_log_behind_or_equal_to_evidence_is_consistent(self):
        write_stage(self.exp, "running")
        self.run_log(self.exp, host="pi", step="run")
        report = hostlog.derive(self.exp)
        self.assertEqual(report["stage"], "running")
        self.assertFalse(report["inconsistent"])

    def test_bad_lines_are_reported_by_line_number(self):
        write_stage(self.exp, "frozen")
        good = json.dumps({"ts": "2026-10-28T10:00:00+0800", "host": "claude", "step": "draft", "note": ""})
        (self.exp / "host_log.jsonl").write_text(
            "not json\n" + good + "\n" + json.dumps({"ts": "t", "host": "cursor", "step": "draft", "note": ""}) + "\n"
        )
        report = hostlog.derive(self.exp)
        self.assertEqual(report["stage"], "frozen")
        self.assertEqual(report["last_log"]["host"], "claude")
        self.assertEqual(len(report["notes"]), 2)
        self.assertIn("第 1 行", report["notes"][0])
        self.assertIn("第 3 行", report["notes"][1])
        self.assertFalse(report["inconsistent"])

    def test_status_is_read_only(self):
        write_stage(self.exp, "judged")
        (self.exp / "host_log.jsonl").write_text(
            json.dumps({"ts": "t", "host": "pi", "step": "judge", "note": ""}) + "\n"
        )
        before = snapshot(self.root)
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = hostlog.status(self.exp, root=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["stage"], "judged")
        self.assertIn("stage=judged", err.getvalue())
        self.assertEqual(snapshot(self.root), before)

    def test_status_on_missing_experiment_exits_2(self):
        missing = self.root / "experiments" / "nope"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(hostlog.status(missing, root=self.root), 2)
        self.assertFalse(missing.exists())


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="phe-hostlog-cli-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.outside = tmp / "exp"
        self.outside.mkdir()

    def phe(self, *argv) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(ROOT / "tools/phe.py"), *argv], capture_output=True, text=True, check=False
        )

    def test_cli_rejects_paths_outside_repo_experiments_with_exit_2(self):
        cases = (
            ("log-outside", ["log", str(self.outside), "--host", "claude", "--step", "draft"]),
            ("log-bad-host", ["log", str(self.outside), "--host", "cursor", "--step", "draft"]),
            ("log-missing-args", ["log", str(self.outside)]),
            ("status-outside", ["status", str(self.outside)]),
            ("status-missing", ["status", str(self.outside / "nope")]),
        )
        for name, argv in cases:
            with self.subTest(case=name):
                proc = self.phe(*argv)
                self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_cli_success_path_in_a_synthetic_repo_copy(self):
        # 仓库根由模块 __file__ 决定：把 tools/ 复制到临时仓库，走真实 CLI 接线，不碰本仓库 experiments/。
        repo = self.outside.parent / "repo"
        shutil.copytree(ROOT / "tools", repo / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        exp = repo / "experiments" / "e1"
        exp.mkdir(parents=True)
        (exp / "proposal.json").write_bytes(PROPOSAL)
        phe = [sys.executable, "-I", str(repo / "tools/phe.py")]
        logged = subprocess.run(
            [*phe, "log", str(exp), "--host", "codex", "--step", "draft", "--note", "起草"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(logged.returncode, 0, logged.stderr)
        shown = subprocess.run([*phe, "status", str(exp)], capture_output=True, text=True, check=False)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        report = json.loads(shown.stdout)
        self.assertEqual(report["stage"], "drafted")
        self.assertEqual((report["last_log"]["host"], report["last_log"]["step"]), ("codex", "draft"))
        self.assertFalse(report["inconsistent"])
        self.assertEqual((exp / "proposal.json").read_bytes(), PROPOSAL)


if __name__ == "__main__":
    unittest.main()
