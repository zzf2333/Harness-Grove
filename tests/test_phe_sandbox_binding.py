"""沙箱配置与 runner 模块的冻结绑定：篡改必须记 invalid_run 且不启动 Target。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_phe
from sandbox_fixture import RunFixture, sandbox_section

phe = test_phe.phe
phe_sandbox = phe.phe_sandbox


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

    def test_tampered_sandbox_config_is_invalid_run_not_an_exception(self):
        # 配置 Hash 先于 sandbox 解析：损坏或不支持的隔离参数也必须记 invalid_run，不提前抛错漏 stop。
        broken = [
            sandbox_section(mechanism="linux-namespaces"),
            sandbox_section(executable="/bin/true"),
            sandbox_section(write_roots=[tempfile.gettempdir()]),
        ]
        for section in broken:
            with self.subTest(section=section):
                self.common_config()
                self.cfg["sandbox"] = sandbox_section()
                self.freeze()
                self.cfg["sandbox"] = section
                (self.exp / "config.json").write_text(json.dumps(self.cfg))
                with patch.object(phe, "run_slot") as target:
                    phe.run(self.exp)
                    target.assert_not_called()
                self.assertEqual(phe.stop_reason(self.exp), "invalid_run:config_hash_changed")

    def test_unsupported_sandbox_settings_with_matching_hash_fail_closed(self):
        self.common_config()
        self.cfg["sandbox"] = sandbox_section(executable="/bin/true")
        self.freeze()
        with patch.object(phe, "run_slot") as target, self.assertRaisesRegex(ValueError, "不接受"):
            phe.run(self.exp)
        target.assert_not_called()
        self.assertFalse((self.exp / "stop.json").exists())

    def test_frozen_runner_module_hash_mismatch_blocks_before_target(self):
        self.common_config()
        self.cfg["sandbox"] = sandbox_section()
        self.freeze()
        cases = [("changed", "sandbox_runner_hash_changed"), ("missing", "sandbox_runner_hash_missing")]
        for name, expected in cases:
            with self.subTest(case=name):
                if name == "changed":
                    self.p["runner"]["runner_files_sha256"]["phe_sandbox.py"] = "0" * 64
                else:
                    self.p["runner"].pop("runner_files_sha256")
                self.write_proposal()
                (self.exp / "stop.json").unlink(missing_ok=True)
                with patch.object(phe, "run_slot") as target:
                    phe.run(self.exp)
                    target.assert_not_called()
                self.assertEqual(phe.stop_reason(self.exp), "invalid_run:" + expected)

    def test_old_experiment_without_sandbox_needs_no_runner_hashes(self):
        self.common_config()
        self.freeze()
        p, cfg = phe.load(self.exp)
        self.assertNotIn("runner_files_sha256", p["runner"])
        self.assertIsNone(phe.integrity_problem(p, cfg, self.exp))

    def test_unavailable_sandbox_program_refuses_before_any_target(self):
        self.common_config()
        self.cfg["sandbox"] = sandbox_section()
        self.freeze()
        # 测试注入：只把固定程序换成不存在的路径，不提供产品配置入口。
        with (
            patch.object(phe_sandbox, "EXECUTABLE", self.exp / "missing-sandbox-exec"),
            patch.object(phe, "run_slot") as target,
            self.assertRaisesRegex(phe_sandbox.SandboxError, "sandbox 程序不可用"),
        ):
            phe.run(self.exp)
            target.assert_not_called()
        self.assertFalse((self.exp / "stop.json").exists())

    def test_sentinel_failure_refuses_to_start_target_without_stop(self):
        self.common_config()
        self.cfg["target_argv"] = [sys.executable, "-I", "-c", "print('never')"]
        self.cfg["sandbox"] = sandbox_section()
        self.freeze()
        refused = phe_sandbox.SandboxError("沙箱哨兵失败")
        with (
            patch.object(phe_sandbox, "EXECUTABLE", Path("/usr/bin/true")),
            patch.object(phe_sandbox, "verify", side_effect=refused),
            self.assertRaises(phe_sandbox.SandboxError),
        ):
            phe.run(self.exp)
        self.assertFalse((self.exp / "stop.json").exists())
        self.assertFalse((self.exp / "artifacts").exists())
        self.assertFalse((self.exp / "runs.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
