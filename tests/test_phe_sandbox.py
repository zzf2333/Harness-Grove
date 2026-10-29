"""执行层写入隔离：配置拒绝、哨兵，以及无模型本地 Target 的行为验收。

`ConfinementTests` 用本地 Python 桩程序充当 Target，不调用任何模型、不是真实 CLI 的 E2E。
"""

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

# phe.py 载入时把 tools/ 放进 sys.path 并导入 phe_sandbox，这里沿用同一个模块对象。
import test_phe
from sandbox_fixture import RunFixture, sandbox_section

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

art, config_dir, denied = (Path(arg) for arg in sys.argv[1:4])
root = Path.cwd().parent
tmp, output = root / "tmp", root / "output"


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
    "slot_tmp": wrote(tmp / "stub.txt"),
    "target_output": wrote(output / "stub.txt"),
    "denied_dir": wrote(denied / "stub.txt"),
    "evidence_dir": wrote(art / "forged.json"),
    "config_dir": wrote(config_dir / "forged.json"),
    "child_allowed": child_wrote(tmp / "child-written.txt"),
    "child_denied": child_wrote(denied / "child-written.txt"),
}
print(json.dumps({"modelUsage": {"stub": {"inputTokens": 100}}, "total_cost_usd": 0.1, "probe": probe}))
expected = {
    "workdir": True,
    "slot_tmp": True,
    "target_output": True,
    "denied_dir": False,
    "evidence_dir": False,
    "config_dir": False,
    "child_allowed": True,
    "child_denied": False,
}
sys.exit(0 if probe == expected else 1)
"""

# Target 在临时仓库里写 .git/config，让 Git 采集可能执行它留下的 helper。
GIT_TARGET = """\
import json, subprocess, sys
from pathlib import Path

work = Path.cwd()
denied, art = Path(sys.argv[1]), Path(sys.argv[2])
evil = work / "evil-helper.sh"
evil.write_text("#!/bin/sh\\nprintf x > '" + str(denied / "helper-wrote.txt") + "'\\n")
evil.chmod(0o755)
config = work / ".git/config"
config.write_text(
    config.read_text()
    + "\\n[core]\\n    fsmonitor = " + str(evil)
    + "\\n[diff]\\n    external = " + str(evil)
    + '\\n[diff "evil"]\\n    textconv = ' + str(evil) + "\\n"
)
(work / ".gitattributes").write_text("*.evil diff=evil\\n")
(work / "sample.evil").write_text("one\\n")
(work / "task.txt").write_text("changed\\n")
subprocess.run(["git", "add", "-A"], check=True)
print(json.dumps({"modelUsage": {"stub": {"inputTokens": 100}}, "total_cost_usd": 0.1}))
"""

# Target 写一段会被外置验收执行的项目代码，越界写必须被拒。
ORACLE_TARGET = """\
import json, sys
from pathlib import Path

denied = Path(sys.argv[1])
Path("agent.py").write_text(
    "import sys\\n"
    "from pathlib import Path\\n"
    "try:\\n"
    "    Path(sys.argv[1]).write_text('x')\\n"
    "    wrote = True\\n"
    "except OSError:\\n"
    "    wrote = False\\n"
    "print(f'DENIED {wrote}')\\n"
    "print('Tests 1 passed')\\n"
)
print(json.dumps({"modelUsage": {"stub": {"inputTokens": 100}}, "total_cost_usd": 0.1}))
"""


class SettingsTests(unittest.TestCase):
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

    def test_executable_override_and_extra_write_roots_are_refused(self):
        # 生产不接受替换沙箱程序或追加写目录：这两类泛化入口是实际的隔离漏洞。
        cases = [
            (sandbox_section(executable="/usr/bin/true"), "executable"),
            (sandbox_section(write_roots=[tempfile.gettempdir()]), "write_roots"),
            (sandbox_section(profile=str(Path(tempfile.gettempdir()) / "x.sb")), "profile"),
        ]
        for section, key in cases:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                phe_sandbox.load_settings({"sandbox": section})

    @REQUIRES_SANDBOX
    def test_settings_use_the_fixed_macos_executable(self):
        settings = phe_sandbox.load_settings({"sandbox": sandbox_section()})
        self.assertEqual(settings, {"executable": SANDBOX_EXEC, "network": "allow"})
        self.assertEqual(phe_sandbox.load_settings({"sandbox": sandbox_section(network="deny")})["network"], "deny")


class ProfileTests(unittest.TestCase):
    def settings(self, network="allow") -> dict[str, Any]:
        return {"executable": SANDBOX_EXEC, "network": network}

    def test_profile_denies_writes_lists_roots_and_allows_reads(self):
        text = phe_sandbox.profile_text(self.settings(), [Path("/private/tmp/slot")])
        self.assertIn("(allow default)", text)
        self.assertIn("(deny file-write*)", text)
        self.assertIn('(subpath "/private/tmp/slot")', text)
        self.assertIn('(literal "/dev/null")', text)
        self.assertNotIn("(deny network*)", text)

    def test_deny_network_adds_rule(self):
        self.assertIn("(deny network*)", phe_sandbox.profile_text(self.settings("deny"), []))

    def test_write_policy_records_hash_of_the_written_file(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sandbox-test-")
        self.addCleanup(temp.cleanup)
        policy = Path(temp.name) / "policy.sb"
        digest = phe_sandbox.write_policy(self.settings(), policy, [Path(temp.name)])
        self.assertEqual(digest, hashlib.sha256(policy.read_bytes()).hexdigest())
        self.assertIsNone(phe_sandbox.write_policy(None, policy, [Path(temp.name)]))

    def test_wrap_adds_prefix_only_when_declared(self):
        policy = Path("/artifacts/policy.sb")
        self.assertEqual(phe_sandbox.wrap(self.settings(), policy), [str(SANDBOX_EXEC), "-f", str(policy)])
        self.assertEqual(phe_sandbox.wrap(None, policy), [])


class SentinelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sentinel-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()

    def test_refuses_a_program_that_does_not_apply_the_policy(self):
        # 测试注入：不走产品配置，直接给 verify 一个不认 -f 的程序。
        settings = {"executable": Path(sys.executable), "network": "allow"}
        policy = self.root / "policy.sb"
        policy.write_text(phe_sandbox.profile_text(settings, [self.allowed]))
        with self.assertRaisesRegex(phe_sandbox.SandboxError, "沙箱哨兵"):
            phe_sandbox.verify(settings, policy, [self.allowed], timeout_s=10)

    @REQUIRES_SANDBOX
    def test_refuses_a_policy_that_does_not_confine(self):
        settings = {"executable": SANDBOX_EXEC, "network": "allow"}
        policy = self.root / "weak.sb"
        policy.write_text("(version 1)\n(allow default)\n")
        with self.assertRaisesRegex(phe_sandbox.SandboxError, "沙箱哨兵失败"):
            phe_sandbox.verify(settings, policy, [self.allowed])

    @REQUIRES_SANDBOX
    def test_tests_every_allowed_root_against_the_given_policy(self):
        # 只放行 allowed 的 policy 不能通过"both allowed"的完整清单：哨兵不能测更严的另一 profile。
        settings = {"executable": SANDBOX_EXEC, "network": "allow"}
        second = self.root / "second"
        second.mkdir()
        policy = self.root / "partial.sb"
        policy.write_text(phe_sandbox.profile_text(settings, [self.allowed]))
        with self.assertRaisesRegex(phe_sandbox.SandboxError, "沙箱哨兵失败"):
            phe_sandbox.verify(settings, policy, [self.allowed, second])


@REQUIRES_SANDBOX
class ConfinementTests(RunFixture):
    def run_with_stub(self, stub: str, *args: str) -> list[dict[str, Any]]:
        script = self.exp / "stub_target.py"
        script.write_text(stub)
        self.cfg["target_argv"] = [sys.executable, "-I", "-B", str(script), *args]
        self.cfg["sandbox"] = {"mechanism": "macos-sandbox-exec"}
        self.freeze()
        phe.run(self.exp)
        return phe.records(self.exp)

    def denied_dir(self) -> Path:
        denied = Path(tempfile.mkdtemp(prefix="phe-sandbox-denied-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, denied, ignore_errors=True)
        return denied

    def test_stub_target_and_child_are_confined_to_runner_dirs(self):
        self.common_config()
        denied = self.denied_dir()
        art = self.exp / "artifacts" / "slot-01"
        records = self.run_with_stub(STUB_TARGET, str(self.exp / "artifacts" / "slot-01"), str(self.exp), str(denied))
        self.assertEqual([r["exit_code"] for r in records], [0, 0, 0])
        self.assertTrue(all(r["test_pass"] for r in records))
        self.assertIsNone(phe.stop_reason(self.exp))
        probe = json.loads((art / "stdout.json").read_text())["probe"]
        self.assertEqual(
            probe,
            {
                "workdir": True,
                "slot_tmp": True,
                "target_output": True,
                "denied_dir": False,
                "evidence_dir": False,
                "config_dir": False,
                "child_allowed": True,
                "child_denied": False,
            },
        )
        # 证据目录和配置没有被 Target 写入；被拒目录不留文件。
        self.assertFalse((art / "forged.json").exists())
        self.assertFalse((self.exp / "forged.json").exists())
        self.assertFalse((denied / "stub.txt").exists())
        self.assertFalse((denied / "child-written.txt").exists())
        self.assertIsNone(phe.integrity_problem(*phe.load(self.exp), self.exp))

    def test_policy_and_launch_record_cover_the_real_writable_roots(self):
        self.common_config()
        records = self.run_with_stub(
            STUB_TARGET, str(self.exp / "artifacts" / "slot-01"), str(self.exp), str(self.denied_dir())
        )
        record, art = records[0], self.exp / "artifacts" / "slot-01"
        policy = art / phe_sandbox.TARGET_POLICY
        text = policy.read_text()
        work = Path(record["workdir"])
        for root in (work, work.parent / "tmp", work.parent / "output"):
            self.assertIn(f'(subpath "{Path(os.path.realpath(root))}")', text)
        for forbidden in (art, self.exp, Path("/", "tmp")):
            self.assertNotIn(f'(subpath "{Path(os.path.realpath(forbidden))}")', text)
        self.assertEqual(record["policy_sha256"], hashlib.sha256(policy.read_bytes()).hexdigest())
        launch = json.loads((art / "argv.json").read_text())
        self.assertEqual(launch["wrapped_argv"][0], str(SANDBOX_EXEC))
        self.assertEqual(launch["wrapped_argv"][-len(launch["argv"]) :], launch["argv"])
        self.assertEqual(record["wrapped_argv_sha256"], phe.argv_digest(launch["wrapped_argv"]))
        self.assertEqual(record["target_output"], str(work.parent / "output"))
        self.assertEqual(phe.integrity_problem(*phe.load(self.exp), self.exp), None)

    def test_git_helper_left_by_target_cannot_write_outside(self):
        self.common_config()
        denied = self.denied_dir()
        records = self.run_with_stub(GIT_TARGET, str(denied), str(self.exp / "artifacts" / "slot-01"))
        self.assertEqual([r["exit_code"] for r in records], [0, 0, 0])
        self.assertIsNone(phe.stop_reason(self.exp))
        self.assertTrue(all(r["diff_stat"] for r in records), [r["diff_stat"] for r in records])
        self.assertFalse((denied / "helper-wrote.txt").exists())
        patch_text = (self.exp / "artifacts" / "slot-01" / "diff.patch").read_text()
        self.assertIn("task.txt", patch_text)

    def test_oracle_runs_target_modified_code_under_the_same_write_limits(self):
        self.common_config()
        denied = self.denied_dir()
        self.cfg["oracle_argv"] = [sys.executable, "-I", "agent.py", str(denied)]
        records = self.run_with_stub(ORACLE_TARGET, str(denied))
        self.assertEqual([r["exit_code"] for r in records], [0, 0, 0])
        self.assertTrue(all(r["test_pass"] for r in records))
        self.assertFalse((denied / "oracle-wrote.txt").exists())
        text = (self.exp / "artifacts" / "slot-01" / "oracle.txt").read_text()
        self.assertIn("DENIED False", text)
        self.assertTrue((self.exp / "artifacts" / "slot-01" / phe_sandbox.ORACLE_POLICY).exists())


if __name__ == "__main__":
    unittest.main()
