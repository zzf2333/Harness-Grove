"""tools/release.py 的四层守卫验收：参数必填、真实环境拒绝、写入路径守卫、每个子命令的真实 home 用例。"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release import (
    RELEASE,
    RELEASE_ID,
    ReleaseFixture,
    release_deploy,
    release_tool,
    rmtree_force,
    run_cli,
    tree,
)

# 第 4 层的断言对象：守卫命中时这些写函数一次都不能被调用。
WRITE_FUNCTIONS = ("symlink", "replace", "mkdir", "chmod", "unlink", "rename", "rmdir", "remove")


class RequiredArgumentTests(ReleaseFixture):
    """第 1 层：--home 和 --phe-home 必填，没有默认值。"""

    def base_arguments(self) -> dict[str, list[str]]:
        return {
            "import": ["--registry", str(self.reg), "--inventory", str(self.inv)],
            "release": [],
            "deploy": ["--agent", "claude", "--release", RELEASE_ID, "--registry", str(self.reg)],
            "rollback": ["--journal", str(self.out / "journal.json")],
        }

    def test_home_and_phe_home_are_required(self):
        for command, extra in self.base_arguments().items():
            for missing in ("--home", "--phe-home"):
                argv = [command, *extra]
                if missing != "--home":
                    argv += ["--home", str(self.home)]
                if missing != "--phe-home":
                    argv += ["--phe-home", str(self.phe)]
                with self.subTest(command=command, missing=missing):
                    result = run_cli(RELEASE, *argv)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(missing, result.stderr)


class RealEnvironmentTests(ReleaseFixture):
    """第 2、4 层：真实 home 一律退出 2，沙箱目录树前后一致，写函数一次都没被调用。"""

    def command_argv(self, home: Path, phe_home: Path) -> dict[str, list[str]]:
        return {
            "import": [
                "import",
                "--registry",
                str(self.reg),
                "--inventory",
                str(self.inv),
                "--phe-home",
                str(phe_home),
                "--home",
                str(home),
            ],
            "release": ["release", "--phe-home", str(phe_home), "--home", str(home)],
            "deploy": [
                "deploy",
                "--agent",
                "claude",
                "--release",
                RELEASE_ID,
                "--registry",
                str(self.reg),
                "--phe-home",
                str(phe_home),
                "--home",
                str(home),
            ],
            "rollback": [
                "rollback",
                "--journal",
                str(self.out / "journal.json"),
                "--phe-home",
                str(phe_home),
                "--home",
                str(home),
            ],
        }

    def guarded_main(self, argv: list[str]) -> None:
        before = tree(self.tmp)
        with contextlib.ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch(f"os.{name}", side_effect=AssertionError(f"写函数被调用：{name}")))
                for name in WRITE_FUNCTIONS
            }
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.assertEqual(release_tool.main(argv), 2)
        self.assertEqual(tree(self.tmp), before)
        for name, mock in mocks.items():
            self.assertEqual(mock.call_count, 0, f"os.{name} 被调用了")

    def test_real_home_is_refused_by_every_command(self):
        for command, argv in self.command_argv(release_deploy.real_home(), self.phe).items():
            with self.subTest(command=command):
                self.guarded_main(argv)

    def test_real_home_as_phe_home_is_refused_by_every_command(self):
        for command, argv in self.command_argv(self.home, release_deploy.real_home()).items():
            with self.subTest(command=command):
                self.guarded_main(argv)

    def test_ancestor_of_real_home_is_refused(self):
        argv = self.command_argv(release_deploy.real_home().parent, self.phe)["release"]
        self.guarded_main(argv)

    def test_protected_paths_under_real_home_are_refused(self):
        home = release_deploy.real_home()
        for relative in (".claude", ".codex", ".pi", ".phe", ".agents", "AI/temper"):
            with self.subTest(path=relative):
                self.guarded_main(self.command_argv(home / relative, self.phe)["release"])

    def test_symlink_to_real_home_is_refused(self):
        link_path = self.tmp / "home-link"
        os.symlink(release_deploy.real_home(), link_path)
        self.guarded_main(self.command_argv(link_path, self.phe)["release"])

    def test_guard_runs_before_reading_any_input(self):
        argv = [
            "import",
            "--registry",
            str(self.out / "no-such-registry.json"),
            "--inventory",
            str(self.out / "no-such-inventory.json"),
            "--phe-home",
            str(self.phe),
            "--home",
            str(release_deploy.real_home()),
        ]
        self.guarded_main(argv)


class GuardedWritePathTests(unittest.TestCase):
    """第 2、3 层的单元契约：写入路径的判定边界，以及 $TMPDIR 在 home 下不被误拒。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="release-guard-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.home = self.tmp / "home"
        self.phe = self.tmp / "phe"
        self.home.mkdir()
        self.phe.mkdir()
        self.roots = [self.home, self.phe]

    def test_paths_inside_roots_pass(self):
        for path in (
            self.home / "a.md",
            self.home / "deep/a/b.md",
            self.phe / "assets/asset-0001/content/x.md",
            self.home,
            self.phe,
        ):
            with self.subTest(path=str(path)):
                release_deploy.guarded_write_path(path, self.roots)

    def test_paths_outside_roots_are_rejected(self):
        for path in (self.tmp / "outside.md", self.tmp / "outside/x.md", Path(tempfile.gettempdir()) / "outside.md"):
            with self.subTest(path=str(path)), self.assertRaises(release_deploy.GuardError):
                release_deploy.guarded_write_path(path, self.roots)

    def test_parent_symlink_escaping_the_sandbox_is_rejected(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, self.home / "escape")
        with self.assertRaises(release_deploy.GuardError):
            release_deploy.guarded_write_path(self.home / "escape/x.md", self.roots)

    def test_symlink_write_checks_the_link_directory(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        # 目标可以合法地位于沙箱内，但链接自身在沙箱外，所以拒绝：检查的是链接所在目录。
        release_deploy.guarded_write_path(self.phe / "target.md", self.roots)
        with self.assertRaises(release_deploy.GuardError):
            release_deploy.guarded_write_path(outside / "link", self.roots)

    def test_sandbox_roots_rejects_the_real_environment(self):
        home = release_deploy.real_home()
        for bad in (home, home.parent, home / ".claude", home / "AI/temper"):
            with self.subTest(home=str(bad)), self.assertRaises(release_deploy.GuardError):
                release_deploy.sandbox_roots(bad, self.phe)
            with self.subTest(phe_home=str(bad)), self.assertRaises(release_deploy.GuardError):
                release_deploy.sandbox_roots(self.home, bad)

    def test_temp_directory_under_real_home_is_not_refused(self):
        home = release_deploy.real_home()
        for relative in ("tmp", "Library/Caches/phe-release", "AI/Harness-Grove"):
            with self.subTest(path=relative):
                self.assertIsNone(release_deploy.refusal_reason(home / relative))
        self.assertEqual(release_deploy.sandbox_roots(self.home, self.phe), self.roots)


if __name__ == "__main__":
    unittest.main()
