"""Git 采集同策略包裹：固定安全选项、干净环境，声明 sandbox 时加同一机制的前缀。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# phe.py 载入时把 tools/ 放进 sys.path 并导入 phe_sandbox，这里沿用同一个模块对象。
import test_phe

phe_sandbox = test_phe.phe.phe_sandbox
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


class GitEnvironmentTests(unittest.TestCase):
    def test_git_env_drops_inherited_git_configuration(self):
        # 父进程的 GIT_* 注入不能改变 runner 的采集行为。
        fake = str(Path(tempfile.gettempdir()) / "evil")
        poisoned = {"GIT_EXTERNAL_DIFF": fake, "GIT_CONFIG_GLOBAL": fake + "-config", "GIT_DIR": fake}
        with patch.dict(os.environ, poisoned):
            env = phe_sandbox.git_env()
        self.assertNotIn("GIT_EXTERNAL_DIFF", env)
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")


class GitCommandTests(unittest.TestCase):
    def collect(self, settings, policy):
        with patch.object(phe_sandbox.subprocess, "run") as run:
            run.return_value.stdout = "output"
            text = phe_sandbox.git_collect(Path("/work"), ["diff", "HEAD"], settings, policy, Path("/slot/tmp"))
        self.assertEqual(text, "output")
        return run.call_args

    def test_declared_sandbox_wraps_with_the_same_mechanism(self):
        policy = Path("/artifacts/policy-git.sb")
        settings = {"executable": SANDBOX_EXEC, "network": "allow"}
        call = self.collect(settings, policy)
        argv = call.args[0]
        self.assertEqual(argv[:3], [str(SANDBOX_EXEC), "-f", str(policy)])
        self.assertEqual(argv[3:5], ["git", "--no-optional-locks"])
        self.assertIn("core.fsmonitor=false", argv)
        self.assertEqual(call.kwargs["env"]["TMPDIR"], "/slot/tmp")

    def test_no_sandbox_keeps_the_command_unwrapped(self):
        call = self.collect(None, None)
        self.assertEqual(call.args[0][:2], ["git", "--no-optional-locks"])


if __name__ == "__main__":
    unittest.main()
