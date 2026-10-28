"""第 1 轮审查修复的验收：大小写别名守卫、冻结拒绝软链、临时链接名、发布失败清理、回滚容错。"""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release import (
    RELEASE_ID,
    ReleaseFixture,
    release_deploy,
    release_tool,
    rmtree_force,
    write,
)
from test_release_guards import WRITE_FUNCTIONS


def case_alias(path: Path) -> Path:
    """同一路径的大小写变体；只在大小写不敏感的文件系统上指向同一个目录。"""
    return Path(str(path).swapcase())


def case_insensitive_filesystem(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.mkdir()
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.rmdir()


class CaseAliasGuardTests(unittest.TestCase):
    """第 2、3 层：真实 home、它的祖先、受保护目录的大小写别名同样被拒绝。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="release-alias-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.home = self.tmp / "home"
        self.phe = self.tmp / "phe"
        self.home.mkdir()
        self.phe.mkdir()
        if not case_insensitive_filesystem(self.tmp):
            self.skipTest("文件系统区分大小写，没有大小写别名可测")
        self.real_home = release_deploy.real_home()

    def assert_refused(self, path: Path) -> None:
        """拒绝发生在任何写入之前：退出 2，且写函数一次都没被调用。"""
        self.assertIsNotNone(release_deploy.refusal_reason(path), str(path))
        argv = ["release", "--phe-home", str(self.phe), "--home", str(path)]
        with contextlib.ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch(f"os.{name}", side_effect=AssertionError(f"写函数被调用：{name}")))
                for name in WRITE_FUNCTIONS
            }
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.assertEqual(release_tool.main(argv), 2)
        for name, mock in mocks.items():
            self.assertEqual(mock.call_count, 0, f"os.{name} 被调用了")

    def test_case_alias_of_real_home_is_refused(self):
        self.assert_refused(case_alias(self.real_home))

    def test_case_alias_of_ancestor_of_real_home_is_refused(self):
        self.assert_refused(case_alias(self.real_home.parent))

    def test_case_alias_of_protected_paths_is_refused(self):
        for relative in release_deploy.PROTECTED_SUBPATHS:
            with self.subTest(path=relative):
                self.assert_refused(case_alias(self.real_home / relative))

    def test_case_alias_of_a_temp_directory_under_real_home_is_allowed(self):
        for relative in ("tmp", "Library/Caches/phe-release", "AI/Harness-Grove"):
            with self.subTest(path=relative):
                self.assertIsNone(release_deploy.refusal_reason(case_alias(self.real_home / relative)))

    def test_write_guard_accepts_a_case_alias_of_a_root(self):
        release_deploy.guarded_write_path(case_alias(self.home) / "AGENTS.md", [self.home, self.phe])

    def test_write_guard_rejects_a_case_alias_outside_the_roots(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        with self.assertRaises(release_deploy.GuardError):
            release_deploy.guarded_write_path(case_alias(outside) / "AGENTS.md", [self.home, self.phe])


class FreezeSymlinkTests(ReleaseFixture):
    """冻结前发布目录里出现软链：发布失败，软链目标（外部文件）的权限不变。"""

    def test_freeze_refuses_a_symlink_and_leaves_its_target_untouched(self):
        self.assertEqual(self.import_assets().returncode, 0)
        external = self.tmp / "outside-secret.txt"
        write(external, "outside\n")
        external.chmod(0o600)
        real_sha256 = release_tool.file_sha256

        def replace_with_link(path):
            os.unlink(path)
            os.symlink(external, path)
            return real_sha256(path)

        with (
            patch.object(release_tool, "file_sha256", side_effect=replace_with_link),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = release_tool.main(["release", "--phe-home", str(self.phe), "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("软链", stderr.getvalue())
        self.assertEqual(stat.S_IMODE(os.lstat(external).st_mode), 0o600)
        self.assertFalse((self.phe / "releases" / RELEASE_ID).exists())


class ReleaseFailureTests(ReleaseFixture):
    """复制来源不可用或遍历失败时发布失败，不留可部署的发布，编号照常推进。"""

    def assert_no_release(self, result) -> None:
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertFalse((self.phe / "releases" / RELEASE_ID).exists())
        high_water = self.phe / "releases/high_water.json"
        self.assertEqual(json.loads(high_water.read_text(encoding="utf-8"))["next_release_number"], 2)

    def test_release_fails_when_asset_content_is_gone(self):
        self.assertEqual(self.import_assets().returncode, 0)
        rmtree_force(self.phe / "assets" / self.asset_id(self.skill) / "content")
        self.assert_no_release(self.make_release())

    def test_release_fails_when_a_subdirectory_is_unreadable(self):
        self.assertEqual(self.import_assets().returncode, 0)
        content = self.phe / "assets" / self.asset_id(self.skill) / "content"
        if os.geteuid() == 0:
            self.skipTest("root 无视目录权限，测不出遍历失败")
        content.chmod(0o000)
        self.addCleanup(content.chmod, 0o755)
        self.assert_no_release(self.make_release())


class TempLinkCollisionTests(ReleaseFixture):
    """临时链接名不再固定：同名位置上的已有文件不被删除、内容不变。"""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.import_assets().returncode, 0)
        self.assertEqual(self.make_release().returncode, 0)

    def test_deploy_keeps_a_foreign_file_at_the_old_temp_name(self):
        stale = self.claude_link.parent / f".{self.claude_link.name}.phe-tmp-{os.getpid()}"
        write(stale, "sentinel\n")
        argv = [
            "deploy",
            "--agent",
            "claude",
            "--release",
            RELEASE_ID,
            "--registry",
            str(self.reg),
            "--phe-home",
            str(self.phe),
            "--home",
            str(self.home),
        ]
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = release_tool.main(argv)
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stale.read_text(encoding="utf-8"), "sentinel\n")


class RestoreEntriesTests(unittest.TestCase):
    """回滚容错：一条链接被守卫拒绝时，其余链接照常恢复。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="release-restore-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.home = self.tmp / "home"
        self.phe = self.tmp / "phe"
        self.outside = self.tmp / "outside"
        self.restored = self.tmp / "restored"
        for directory in (self.home, self.phe, self.outside, self.restored):
            directory.mkdir()
        self.sandbox = release_deploy.Sandbox([self.home, self.phe])

    def test_guard_error_on_one_entry_does_not_stop_the_rest(self):
        entries = []
        for directory in (self.home, self.outside, self.phe):
            path = directory / "AGENTS.md"
            os.symlink(self.tmp / "stale", path)
            entries.append(
                {"path": path, "state": "symlink", "old_target": str(self.restored / f"{directory.name}.md")}
            )
        failures = release_deploy.restore_entries(self.sandbox, entries)
        self.assertEqual(len(failures), 1, failures)
        self.assertIn(str(self.outside / "AGENTS.md"), failures[0])
        self.assertEqual(os.readlink(self.home / "AGENTS.md"), str(self.restored / "home.md"))
        self.assertEqual(os.readlink(self.phe / "AGENTS.md"), str(self.restored / "phe.md"))
        self.assertEqual(os.readlink(self.outside / "AGENTS.md"), str(self.tmp / "stale"))


if __name__ == "__main__":
    unittest.main()
