"""执行层写入隔离：profile 取值、fail closed，以及无模型本地 Target 的集成验收。

`ConfinementTests` 用本地 Python 桩程序充当 Target，不调用任何模型、不是真实 CLI 的 E2E。
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

# phe.py 载入时把 tools/ 放进 sys.path 并导入 phe_sandbox，这里沿用同一个模块对象。
import test_phe

phe = test_phe.phe
phe_sandbox = phe.phe_sandbox
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
REQUIRES_SANDBOX = unittest.skipUnless(
    platform.system() == "Darwin" and SANDBOX_EXEC.exists(), "需要 macOS 内置 /usr/bin/sandbox-exec"
)

# 本地桩程序：只写文件、不联网、不调用模型。退出码 0 表示写入边界符合预期。
STUB_TARGET = """\
import json, subprocess, sys
from pathlib import Path

art, temp_root, denied = (Path(arg) for arg in sys.argv[1:4])


def wrote(path):
    try:
        path.write_text("x")
        return True
    except OSError:
        return False


CHILD = (
    "import pathlib, sys\\n"
    "try:\\n"
    "    pathlib.Path(sys.argv[1]).write_text('x')\\n"
    "except OSError:\\n"
    "    sys.exit(0)\\n"
    "sys.exit(1)\\n"
)


def child_wrote(path):
    return subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, str(path)]).returncode != 0


probe = {
    "workdir": wrote(Path.cwd() / "stub.txt"),
    "artifacts": wrote(art / "stub-write.txt"),
    "explicit_temp": wrote(temp_root / "stub-write.txt"),
    "denied_dir": wrote(denied / "stub-write.txt"),
    "child_denied": child_wrote(denied / "child-write.txt"),
}
print(json.dumps({"modelUsage": {"stub": {"inputTokens": 100}}, "total_cost_usd": 0.1, "probe": probe}))
expected = {"workdir": True, "artifacts": True, "explicit_temp": True, "denied_dir": False, "child_denied": False}
sys.exit(0 if probe == expected else 1)
"""


def sandbox_section(**overrides) -> dict[str, Any]:
    section: dict[str, Any] = {"mechanism": "macos-sandbox-exec", "executable": sys.executable}
    section.update(overrides)
    return section


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sandbox-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_absent_section_means_no_wrapping(self):
        self.assertIsNone(phe_sandbox.load_settings({}))

    def test_unsupported_mechanism_and_bad_network_are_refused(self):
        cases = [
            {"sandbox": sandbox_section(mechanism="linux-namespaces")},
            {"sandbox": sandbox_section(network="maybe")},
            {"sandbox": "macos-sandbox-exec"},
        ]
        for cfg in cases:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                phe_sandbox.load_settings(cfg)

    def test_missing_executable_fails_closed(self):
        cfg = {"sandbox": sandbox_section(executable=str(self.root / "missing-sandbox-exec"))}
        with self.assertRaisesRegex(ValueError, "sandbox 程序不可用"):
            phe_sandbox.load_settings(cfg)

    def test_write_roots_must_be_absolute_existing_and_outside_home(self):
        home = phe_sandbox.real_home()
        cases = ["relative/dir", str(self.root / "missing"), str(home), str(home / "Library"), 3]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                phe_sandbox.load_settings({"sandbox": sandbox_section(write_roots=[value])})

    def test_network_defaults_to_allow_and_roots_are_reported(self):
        settings = phe_sandbox.load_settings({"sandbox": sandbox_section(write_roots=[str(self.root)])})
        self.assertEqual(settings["network"], "allow")
        self.assertEqual(settings["write_roots"], [Path(os.path.realpath(self.root))])


class ProfileTests(unittest.TestCase):
    def settings(self, network="allow") -> dict[str, Any]:
        return {"executable": Path(sys.executable), "network": network, "write_roots": []}

    def test_profile_denies_writes_lists_roots_and_allows_reads(self):
        text = phe_sandbox.profile_text(self.settings(), [Path("/private/tmp/slot")])
        self.assertIn("(allow default)", text)
        self.assertIn("(deny file-write*)", text)
        self.assertIn('(subpath "/private/tmp/slot")', text)
        self.assertIn('(literal "/dev/null")', text)
        self.assertNotIn("(deny network*)", text)

    def test_deny_network_adds_rule(self):
        self.assertIn("(deny network*)", phe_sandbox.profile_text(self.settings("deny"), []))

    def test_wrap_writes_profile_into_slot_artifacts(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sandbox-test-")
        self.addCleanup(temp.cleanup)
        work, art = Path(temp.name) / "work", Path(temp.name) / "art"
        work.mkdir()
        art.mkdir()
        prefix = phe_sandbox.wrap(self.settings(), work, art)
        policy = art / phe_sandbox.PROFILE_NAME
        self.assertEqual(prefix, [sys.executable, "-f", str(policy)])
        self.assertIn(f'(subpath "{Path(os.path.realpath(work))}")', policy.read_text())

    def test_wrap_without_settings_adds_no_prefix(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sandbox-test-")
        self.addCleanup(temp.cleanup)
        self.assertEqual(phe_sandbox.wrap(None, Path(temp.name), Path(temp.name)), [])


class RunFixture(unittest.TestCase):
    """合成实验夹具：3 组 × 1 次，Target 是本机桩程序。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sandbox-run-")
        self.addCleanup(temp.cleanup)
        self.exp = Path(temp.name)
        self.base = self.exp / "base"
        self.base.mkdir()
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        (self.base / "task.txt").write_text("fixture")
        self.p = test_phe.proposal(count=1, controls=False)
        del self.p["rules"]["required_control"]
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

    def common_config(self):
        trusted = self.exp / "trusted.txt"
        trusted.write_text("oracle fixture")
        self.cfg.update(
            {
                "base_dir": str(self.base),
                "experiment_id": "synthetic",
                "experiment_dir": str(self.exp),
                "env": {},
                "oracle_test_src": str(trusted),
                "oracle_test_dest": "oracle.txt",
                "oracle_cwd": ".",
                "oracle_argv": [sys.executable, "-I", "-c", "print('Tests 1 passed')"],
                "nonce_sha256": "unused",
            }
        )


class BindingTests(RunFixture):
    def test_sandbox_section_is_covered_by_the_config_hash(self):
        self.common_config()
        self.cfg["sandbox"] = sandbox_section()
        self.freeze()
        self.cfg["sandbox"] = sandbox_section(network="deny")
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(phe.stop_reason(self.exp), "invalid_run:config_hash_changed")

    @REQUIRES_SANDBOX
    def test_unavailable_sandbox_refuses_before_any_target(self):
        self.common_config()
        self.cfg["target_argv"] = [sys.executable, "-I", "-c", "print('never')"]
        self.cfg["sandbox"] = sandbox_section(executable=str(self.exp / "missing-sandbox-exec"))
        self.freeze()
        with (
            patch.object(phe, "run_slot") as target,
            self.assertRaisesRegex(ValueError, "sandbox 程序不可用"),
        ):
            phe.run(self.exp)
        target.assert_not_called()
        # fail closed：拒绝启动，也不留下伪造的停止原因或半成品。
        self.assertFalse((self.exp / "stop.json").exists())
        self.assertFalse((self.exp / "runs.jsonl").exists())


class SentinelTests(unittest.TestCase):
    def test_refuses_a_program_that_does_not_apply_the_profile(self):
        # 传入一个不认 -f 的程序：允许目录写不进去，哨兵必须拒绝启动。
        settings = phe_sandbox.load_settings({"sandbox": sandbox_section(executable=sys.executable)})
        with self.assertRaisesRegex(ValueError, "沙箱哨兵"):
            phe_sandbox.verify(settings, timeout_s=10)


@REQUIRES_SANDBOX
class ConfinementTests(RunFixture):
    def test_stub_target_and_child_are_confined_to_declared_roots(self):
        self.common_config()
        stub = self.exp / "stub_target.py"
        stub.write_text(STUB_TARGET)
        temp_root = Path(tempfile.mkdtemp(prefix="phe-sandbox-allowed-", dir="/tmp"))
        denied = Path(tempfile.mkdtemp(prefix="phe-sandbox-denied-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, temp_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, denied, ignore_errors=True)
        art = self.exp / "artifacts" / "slot-01"
        self.cfg["target_argv"] = [
            sys.executable,
            "-I",
            "-B",
            str(stub),
            str(art),
            str(temp_root),
            str(denied),
        ]
        self.cfg["sandbox"] = {
            "mechanism": "macos-sandbox-exec",
            "executable": str(SANDBOX_EXEC),
            "write_roots": [str(temp_root)],
        }
        self.freeze()
        phe.run(self.exp)
        record = phe.records(self.exp)[0]
        self.assertEqual(record["exit_code"], 0, record)
        self.assertTrue(record["test_pass"])
        probe = json.loads((art / "stdout.json").read_text())["probe"]
        self.assertEqual(
            probe,
            {
                "workdir": True,
                "artifacts": True,
                "explicit_temp": True,
                "denied_dir": False,
                "child_denied": False,
            },
        )
        # 每个 slot 的生效 profile 留在 artifacts 里；被拒目录不留文件。
        self.assertTrue((art / phe_sandbox.PROFILE_NAME).exists())
        self.assertFalse((denied / "stub-write.txt").exists())
        self.assertFalse((denied / "child-write.txt").exists())

    def test_sentinel_refuses_a_profile_that_does_not_confine(self):
        settings = phe_sandbox.load_settings({"sandbox": sandbox_section(executable=str(SANDBOX_EXEC))})
        with (
            patch.object(phe_sandbox, "profile_text", return_value="(version 1)\n(allow default)\n"),
            self.assertRaisesRegex(ValueError, "沙箱哨兵"),
        ):
            phe_sandbox.verify(settings)


if __name__ == "__main__":
    unittest.main()
