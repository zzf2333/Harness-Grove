"""tools/release.py 的沙箱流程验收：import → release → deploy → 盘点 drift 为 0 → rollback 一致。

fixture 全在临时目录：假的 `.claude`/`.codex`/`.pi` 指向沙箱里的 temper 副本，真实环境只被读一次
`pwd.getpwuid(...)` 用来确认守卫不误伤（见 test_release_guards.py）。
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "tools/inventory.py"
REGISTRY = ROOT / "tools/registry.py"
RELEASE = ROOT / "tools/release.py"

SPEC = importlib.util.spec_from_file_location("release_tool", RELEASE)
assert SPEC is not None and SPEC.loader is not None
release_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_tool)
# release.py 导入时已按 tools/ 路径加载同目录的写入层，这里取同一个实例，守卫异常类型才一致。
release_deploy = sys.modules["release_deploy"]

RELEASE_ID = "r0001"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def link(target: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def run_cli(tool: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(tool), *args], capture_output=True, text=True, check=False)


def tree(root: Path) -> dict[str, str]:
    """目录树快照：目录记 dir，普通文件记内容 hash，软链记目标（不跟链接）。"""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[relative] = f"link:{os.readlink(path)}"
        elif path.is_dir():
            snapshot[relative] = "dir"
        else:
            snapshot[relative] = f"file:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    return snapshot


def make_writable(root: Path) -> None:
    """发布目录是只读的，清理和手工删除前先放开权限。"""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        path.chmod(0o755 if path.is_dir() else 0o644)


def rmtree_force(path: Path) -> None:
    if path.exists():
        make_writable(path)
        shutil.rmtree(path, ignore_errors=True)


def failing_replace(real_replace, fail_on: int):
    """第 fail_on 次调用抛错的 os.replace；之后的调用（自动回滚）照常成功。"""
    calls = {"count": 0}

    def replace(source, target, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == fail_on:
            raise OSError("注入的替换失败")
        return real_replace(source, target, *args, **kwargs)

    return replace


class ReleaseFixture(unittest.TestCase):
    """沙箱：temper 副本 + 临时 home（三个 Agent 的软链或文件）+ 临时 PHE_HOME。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="release-test-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.temper = self.tmp / "temper"
        self.home = self.tmp / "home"
        self.phe = self.tmp / "phe"
        self.out = self.tmp / "out"
        self.shared = self.temper / "config/AGENTS.md"
        self.skill = self.temper / "config/skills/demo"
        self.pi_file = self.home / ".pi/agent/AGENTS.md"
        self.claude_link = self.home / ".claude/CLAUDE.md"
        self.codex_link = self.home / ".codex/AGENTS.md"
        self.skill_link = self.home / ".claude/skills/demo"
        self._build()
        self.inv = self.inventory("inventory-1.json")
        self.reg = self.out / "registry.json"
        result = self.adopt(self.reg, self.inv, "--group", str(self.shared), "--path", str(self.skill_link))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.adopt(self.reg, self.inv, "--path", str(self.pi_file))
        self.assertEqual(result.returncode, 0, result.stderr)

    def _build(self):
        write(self.shared, "# shared\n")
        write(self.skill / "SKILL.md", "# demo skill\n")
        write(self.skill / "run.py", "print('demo')\n")
        # .pi 的指令是普通文件：deploy 不允许用软链覆盖它。
        write(self.pi_file, "# pi instructions\n")
        link(self.shared, self.claude_link)
        link(self.shared, self.codex_link)
        link(self.skill, self.skill_link)

    def inventory(self, name: str) -> Path:
        out = self.out / name
        result = run_cli(INVENTORY, "--home", str(self.home), "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def adopt(self, registry: Path, inventory: Path, *selectors: str) -> subprocess.CompletedProcess:
        return run_cli(REGISTRY, "adopt", "--inventory", str(inventory), "--registry", str(registry), *selectors)

    def drift(self, inventory: Path, registry: Path) -> dict:
        result = run_cli(REGISTRY, "drift", "--inventory", str(inventory), "--registry", str(registry))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def import_assets(self, *extra: str) -> subprocess.CompletedProcess:
        return run_cli(
            RELEASE,
            "import",
            "--registry",
            str(self.reg),
            "--inventory",
            str(self.inv),
            "--phe-home",
            str(self.phe),
            "--home",
            str(self.home),
            *extra,
        )

    def make_release(self) -> subprocess.CompletedProcess:
        return run_cli(RELEASE, "release", "--phe-home", str(self.phe), "--home", str(self.home))

    def deploy(self, agent: str, release_id: str = RELEASE_ID) -> subprocess.CompletedProcess:
        return run_cli(
            RELEASE,
            "deploy",
            "--agent",
            agent,
            "--release",
            release_id,
            "--registry",
            str(self.reg),
            "--phe-home",
            str(self.phe),
            "--home",
            str(self.home),
        )

    def rollback(self, journal: Path) -> subprocess.CompletedProcess:
        return run_cli(
            RELEASE, "rollback", "--journal", str(journal), "--phe-home", str(self.phe), "--home", str(self.home)
        )

    def assets(self, registry: Path) -> list[dict]:
        return json.loads(registry.read_text(encoding="utf-8"))["assets"]

    def asset_id(self, resolved_path: Path) -> str:
        for asset in self.assets(self.reg):
            if asset["resolved_path"] == str(resolved_path):
                return asset["asset_id"]
        raise AssertionError(f"登记表里没有 {resolved_path}")

    def manifest(self, release_id: str = RELEASE_ID) -> dict:
        return json.loads((self.phe / "releases" / release_id / "manifest.json").read_text(encoding="utf-8"))

    def journals(self) -> list[Path]:
        return sorted((self.phe / "journal").iterdir())

    def journal_of(self, result: subprocess.CompletedProcess) -> Path:
        """deploy 成功时把 journal 路径写在最后一行。"""
        journal = Path(result.stdout.strip().rsplit("journal ", 1)[1])
        self.assertTrue(journal.is_file(), journal)
        return journal

    def bindings(self) -> dict:
        path = self.phe / "bindings.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class ImportTests(ReleaseFixture):
    def test_import_copies_registered_assets(self):
        before = tree(self.temper)
        result = self.import_assets()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill_id = self.asset_id(self.skill)
        metadata = json.loads((self.phe / "assets" / skill_id / "asset.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["asset_id"], metadata["kind"], metadata["name"]), (skill_id, "skill", "demo"))
        content = self.phe / "assets" / skill_id / "content"
        self.assertEqual(sorted(path.name for path in content.iterdir()), ["SKILL.md", "run.py"])
        self.assertEqual((content / "SKILL.md").read_text(encoding="utf-8"), "# demo skill\n")
        shared_id = self.asset_id(self.shared)
        metadata = json.loads((self.phe / "assets" / shared_id / "asset.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["kind"], metadata["name"]), ("instructions", "AGENTS.md"))
        copied = self.phe / "assets" / shared_id / "content/AGENTS.md"
        self.assertEqual(copied.read_text(encoding="utf-8"), "# shared\n")
        # 来源只读：temper 副本一个字节都没动。
        self.assertEqual(tree(self.temper), before)

    def test_import_refuses_second_run(self):
        self.assertEqual(self.import_assets().returncode, 0)
        result = self.import_assets()
        self.assertEqual(result.returncode, 1)
        self.assertIn("拒绝覆盖", result.stderr)

    def test_import_refuses_source_outside_sandbox(self):
        registry = json.loads(self.reg.read_text(encoding="utf-8"))
        registry["assets"][0]["resolved_path"] = "/etc/hosts"
        self.reg.write_text(json.dumps(registry), encoding="utf-8")
        result = self.import_assets()
        self.assertEqual(result.returncode, 1)
        self.assertIn("不在沙箱之内", result.stderr)
        self.assertFalse((self.phe / "assets").exists())


class ReleaseTests(ReleaseFixture):
    def test_release_writes_manifest_and_is_read_only(self):
        self.assertEqual(self.import_assets().returncode, 0)
        result = self.make_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(RELEASE_ID, result.stdout)
        skill_id = self.asset_id(self.skill)
        manifest = self.manifest()
        self.assertEqual(manifest["release"], RELEASE_ID)
        self.assertEqual(manifest["assets"][skill_id], {"kind": "skill", "name": "demo", "entry": f"files/{skill_id}"})
        entry = f"files/{skill_id}/SKILL.md"
        self.assertEqual(manifest["files"][entry]["sha256"], hashlib.sha256(b"# demo skill\n").hexdigest())
        self.assertEqual(manifest["files"][entry]["asset_id"], skill_id)
        self.assertEqual(manifest["files"][entry]["size"], len(b"# demo skill\n"))
        release_dir = self.phe / "releases" / RELEASE_ID
        for path in [release_dir, *release_dir.rglob("*")]:
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode) & 0o222, 0, str(path))

    def test_release_numbers_never_reuse_a_deleted_one(self):
        self.assertEqual(self.import_assets().returncode, 0)
        self.assertEqual(self.make_release().returncode, 0)
        self.assertEqual(self.make_release().returncode, 0)
        self.assertTrue((self.phe / "releases/r0002").is_dir())
        rmtree_force(self.phe / "releases/r0002")
        result = self.make_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("r0003", result.stdout)
        self.assertTrue((self.phe / "releases/r0003").is_dir())
        self.assertFalse((self.phe / "releases/r0002").exists())


class DeployTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.import_assets().returncode, 0)
        self.assertEqual(self.make_release().returncode, 0)

    def test_deploy_points_links_into_release(self):
        result = self.deploy("claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        shared_id = self.asset_id(self.shared)
        skill_id = self.asset_id(self.skill)
        release_dir = self.phe / "releases" / RELEASE_ID
        self.assertEqual(os.readlink(self.claude_link), str(release_dir / f"files/{shared_id}/AGENTS.md"))
        self.assertEqual(os.readlink(self.skill_link), str(release_dir / f"files/{skill_id}"))
        # 别的 Agent 的链接一条都不动。
        self.assertEqual(os.readlink(self.codex_link), str(self.shared))
        self.assertEqual(
            self.bindings(), {"claude": {str(self.claude_link): RELEASE_ID, str(self.skill_link): RELEASE_ID}}
        )
        journal = json.loads(self.journals()[0].read_text(encoding="utf-8"))
        self.assertEqual(journal["agent"], "claude")
        self.assertEqual(journal["release"], RELEASE_ID)
        self.assertEqual(
            {entry["path"]: (entry["state"], entry["old_target"]) for entry in journal["entries"]},
            {str(self.claude_link): ("symlink", str(self.shared)), str(self.skill_link): ("symlink", str(self.skill))},
        )

    def test_deploy_then_reinventory_and_adopt_reports_no_drift(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        after = self.inventory("inventory-2.json")
        registry = self.out / "registry-after.json"
        result = self.adopt(
            registry,
            after,
            "--path",
            str(self.claude_link),
            "--path",
            str(self.skill_link),
            "--path",
            str(self.codex_link),
            "--path",
            str(self.pi_file),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.drift(after, registry)["changes"], 0)

    def test_deploy_aborts_on_content_change_without_touching_links(self):
        before = tree(self.home)
        write(self.shared, "# shared v2\n")
        result = self.deploy("claude")
        self.assertEqual(result.returncode, 1)
        self.assertIn("content_changed", result.stderr)
        self.assertIn("ExternalChange", result.stderr)
        self.assertEqual(tree(self.home), before)
        self.assertEqual(self.bindings(), {})
        self.assertFalse((self.phe / "journal").exists())

    def test_deploy_aborts_on_retargeted_link(self):
        before = tree(self.home)
        self.codex_link.unlink()
        link(self.skill / "SKILL.md", self.codex_link)
        result = self.deploy("claude")
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink_target_changed", result.stderr)
        self.assertEqual(tree(self.home), before | {".codex/AGENTS.md": f"link:{self.skill / 'SKILL.md'}"})
        self.assertFalse((self.phe / "bindings.json").exists())

    def test_deploy_refuses_regular_file(self):
        before = tree(self.home)
        result = self.deploy("pi")
        self.assertEqual(result.returncode, 1)
        self.assertIn("拒绝覆盖", result.stderr)
        self.assertEqual(tree(self.home), before)
        self.assertEqual(self.bindings(), {})
        self.assertFalse((self.phe / "journal").exists())

    def test_rollback_restores_previous_state(self):
        before = tree(self.home)
        deployed = self.deploy("claude")
        self.assertEqual(deployed.returncode, 0, deployed.stderr)
        self.assertNotEqual(tree(self.home), before)
        result = self.rollback(self.journal_of(deployed))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree(self.home), before)
        self.assertEqual(self.bindings(), {})
        # 回滚后登记表基线仍然成立，链接也逐条回到 temper 副本。
        self.assertEqual(self.drift(self.inventory("inventory-3.json"), self.reg)["changes"], 0)

    def test_failed_link_swap_rolls_back_changed_links(self):
        """第 N 次 os.replace 失败时按 journal 自动回滚；N=2 时还没改过链接。"""
        before = tree(self.home)
        real_replace = os.replace
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
        for fail_on in (2, 3, 4):
            with self.subTest(fail_on=fail_on):
                with (
                    patch("os.replace", side_effect=failing_replace(real_replace, fail_on)),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    code = release_tool.main(argv)
                self.assertIn("注入的替换失败", stderr.getvalue())
                self.assertEqual(code, 1)
                self.assertEqual(tree(self.home), before)
                self.assertEqual(self.bindings(), {})
                self.assertTrue(self.journals())
                # 失败后仍可正常部署，再把这次成功的部署回滚掉，让下一轮从同一状态开始。
                deployed = self.deploy("claude")
                self.assertEqual(deployed.returncode, 0, deployed.stderr)
                self.assertEqual(self.rollback(self.journal_of(deployed)).returncode, 0)
                self.assertEqual(tree(self.home), before)


if __name__ == "__main__":
    unittest.main()
