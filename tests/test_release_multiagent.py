"""多 Agent 共享资产上的 deploy 验收：PHE 自己的部署不算 ExternalChange，带外改动仍然中止。

fixture 里三个 Agent 的指令指向同一份 AGENTS.md，三个 Agent 的 skill 指向同一份 demo 目录，
登记表按真实路径登记成两个资产。全部路径都在临时目录里。
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from test_release import (
    INVENTORY,
    REGISTRY,
    RELEASE,
    RELEASE_ID,
    rmtree_force,
    run_cli,
    tree,
    write,
)

AGENTS = ("claude", "codex", "pi")


class MultiAgentFixture(unittest.TestCase):
    """沙箱：temper 副本 + 临时 home（三个 Agent 的软链）+ 临时 PHE_HOME。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="release-multi-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.temper = self.tmp / "temper"
        self.home = self.tmp / "home"
        self.phe = self.tmp / "phe"
        self.out = self.tmp / "out"
        self.shared = self.temper / "config/AGENTS.md"
        self.skill = self.temper / "config/skills/demo"
        self.instructions = {
            "claude": self.home / ".claude/CLAUDE.md",
            "codex": self.home / ".codex/AGENTS.md",
            "pi": self.home / ".pi/agent/AGENTS.md",
        }
        self.skills = {
            "claude": self.home / ".claude/skills/demo",
            "codex": self.home / ".agents/skills/demo",
            "pi": self.home / ".pi/agent/skills/demo",
        }
        self._build()
        self.inv = self.inventory("inventory-1.json")
        self.reg = self.out / "registry.json"
        result = self.adopt(self.reg, self.inv, "--group", str(self.shared), "--group", str(self.skill))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.import_assets().returncode, 0)
        self.assertEqual(self.make_release().returncode, 0)

    def _build(self):
        write(self.shared, "# shared\n")
        write(self.skill / "SKILL.md", "# demo skill\n")
        write(self.skill / "run.py", "print('demo')\n")
        for path in [*self.instructions.values(), *self.skills.values()]:
            path.parent.mkdir(parents=True, exist_ok=True)
        for path in self.instructions.values():
            os.symlink(self.shared, path)
        for path in self.skills.values():
            os.symlink(self.skill, path)

    def inventory(self, name: str) -> Path:
        out = self.out / name
        result = run_cli(INVENTORY, "--home", str(self.home), "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def adopt(self, registry: Path, inventory: Path, *selectors: str):
        return run_cli(REGISTRY, "adopt", "--inventory", str(inventory), "--registry", str(registry), *selectors)

    def import_assets(self):
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
        )

    def make_release(self):
        return run_cli(RELEASE, "release", "--phe-home", str(self.phe), "--home", str(self.home))

    def deploy(self, agent: str, release_id: str = RELEASE_ID):
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

    def rollback(self, journal: Path):
        return run_cli(
            RELEASE, "rollback", "--journal", str(journal), "--phe-home", str(self.phe), "--home", str(self.home)
        )

    def journal_of(self, result) -> Path:
        return Path(result.stdout.strip().rsplit("journal ", 1)[1])

    def journals(self) -> list[Path]:
        directory = self.phe / "journal"
        return sorted(directory.iterdir()) if directory.is_dir() else []

    def bindings(self) -> dict:
        path = self.phe / "bindings.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def write_bindings(self, data: dict) -> None:
        (self.phe / "bindings.json").write_text(json.dumps(data), encoding="utf-8")

    def asset_id(self, resolved_path: Path) -> str:
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        for asset in data["assets"]:
            if asset["resolved_path"] == str(resolved_path):
                return asset["asset_id"]
        raise AssertionError(f"登记表里没有 {resolved_path}")

    def manifest(self, release_id: str = RELEASE_ID) -> dict:
        return json.loads((self.phe / "releases" / release_id / "manifest.json").read_text(encoding="utf-8"))

    def release_target(self, asset_id: str, release_id: str = RELEASE_ID) -> Path:
        meta = self.manifest(release_id)["assets"][asset_id]
        target = self.phe / "releases" / release_id / meta["entry"]
        return target / meta["name"] if meta["kind"] == "instructions" else target


class SharedAssetDeployTests(MultiAgentFixture):
    def test_three_agents_deploy_in_sequence(self):
        shared_id = self.asset_id(self.shared)
        skill_id = self.asset_id(self.skill)
        for agent in AGENTS:
            result = self.deploy(agent)
            self.assertEqual(result.returncode, 0, f"{agent}: {result.stderr}")
            self.assertEqual(os.readlink(self.instructions[agent]), str(self.release_target(shared_id)))
            self.assertEqual(os.readlink(self.skills[agent]), str(self.release_target(skill_id)))
            self.assertEqual(
                self.bindings()[agent],
                {str(self.instructions[agent]): RELEASE_ID, str(self.skills[agent]): RELEASE_ID},
            )
        self.assertEqual(sorted(self.bindings()), sorted(AGENTS))
        self.assertEqual(len(self.journals()), 3)

    def test_registry_drift_still_reports_own_deployments(self):
        """drift 保持纯粹：单独跑 registry drift 仍把 PHE 自己部署的链接报成 symlink_target_changed。"""
        self.assertEqual(self.deploy("claude").returncode, 0)
        result = run_cli(
            REGISTRY, "drift", "--inventory", str(self.inventory("inventory-2.json")), "--registry", str(self.reg)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        changed = {(change["type"], change["path"]) for change in report["external_changes"]}
        self.assertEqual(
            changed,
            {
                ("symlink_target_changed", str(self.instructions["claude"])),
                ("symlink_target_changed", str(self.skills["claude"])),
            },
        )

    def test_deploy_aborts_when_a_bound_link_points_elsewhere(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        elsewhere = self.temper / "config/other.md"
        write(elsewhere, "# other\n")
        self.instructions["claude"].unlink()
        os.symlink(elsewhere, self.instructions["claude"])
        before = tree(self.home)
        result = self.deploy("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink_target_changed", result.stderr)
        self.assertIn(str(self.instructions["claude"]), result.stderr)
        self.assertEqual(tree(self.home), before)
        self.assertEqual(sorted(self.bindings()), ["claude"])
        self.assertEqual(len(self.journals()), 1)

    def test_deploy_aborts_when_bindings_point_to_a_missing_release(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        bindings = self.bindings()
        for path in bindings["claude"]:
            bindings["claude"][path] = "r0099"
        self.write_bindings(bindings)
        before = tree(self.home)
        result = self.deploy("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink_target_changed", result.stderr)
        self.assertIn(str(self.instructions["claude"]), result.stderr)
        self.assertEqual(tree(self.home), before)
        self.assertEqual(len(self.journals()), 1)

    def test_deploy_after_rollback_still_succeeds(self):
        deployed = self.deploy("claude")
        self.assertEqual(deployed.returncode, 0, deployed.stderr)
        self.assertEqual(self.rollback(self.journal_of(deployed)).returncode, 0)
        result = self.deploy("codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        shared_id = self.asset_id(self.shared)
        self.assertEqual(os.readlink(self.instructions["codex"]), str(self.release_target(shared_id)))
        self.assertEqual(sorted(self.bindings()), ["codex"])


class BoundReleaseVerificationTests(MultiAgentFixture):
    """放行前按 manifest 校验绑定发布：内容被改写、换成软链、多出文件、编号非法都不放行。"""

    def release_asset_dir(self, asset_id: str) -> Path:
        return self.phe / "releases" / RELEASE_ID / "files" / asset_id

    def snapshot(self) -> dict:
        """中止前后要比对的三样：home 链接树、bindings、journal 数量。"""
        return {"links": tree(self.home), "bindings": self.bindings(), "journals": len(self.journals())}

    def assert_aborts_unchanged(self, agent: str, before: dict) -> str:
        result = self.deploy(agent)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(tree(self.home), before["links"])
        self.assertEqual(self.bindings(), before["bindings"])
        self.assertEqual(len(self.journals()), before["journals"])
        self.assertIn("symlink_target_changed", result.stderr)
        return result.stderr

    def test_deploy_aborts_when_bound_release_file_was_rewritten(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        target = self.release_target(self.asset_id(self.shared))
        target.chmod(0o644)
        target.write_text("# rewritten\n", encoding="utf-8")
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn(str(self.instructions["claude"]), stderr)
        self.assertIn("verify_failed", stderr)

    def test_deploy_aborts_when_bound_release_asset_dir_is_a_symlink(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        asset_dir = self.release_asset_dir(self.asset_id(self.skill))
        # 软链指向的目录内容与 manifest 完全一致：只有 lstat 拒绝软链才能挡住这条漂移。
        elsewhere = self.tmp / "elsewhere/demo"
        write(elsewhere / "SKILL.md", "# demo skill\n")
        write(elsewhere / "run.py", "print('demo')\n")
        asset_dir.parent.chmod(0o755)
        rmtree_force(asset_dir)
        os.symlink(elsewhere, asset_dir)
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn(str(self.skills["claude"]), stderr)
        self.assertIn("软链", stderr)

    def test_deploy_aborts_when_bound_release_dir_is_a_symlink(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        release_dir = self.phe / "releases" / RELEASE_ID
        # 副本内容与 manifest 完全一致：只有 lstat 拒绝发布目录是软链才能挡住这条漂移。
        copy = self.tmp / "copied-release"
        shutil.copytree(release_dir, copy, symlinks=True)
        (self.phe / "releases").chmod(0o755)
        rmtree_force(release_dir)
        os.symlink(copy, release_dir)
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn("软链", stderr)

    def test_deploy_aborts_when_bound_release_manifest_is_a_symlink(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        manifest = self.phe / "releases" / RELEASE_ID / "manifest.json"
        copy = self.tmp / "copied-manifest.json"
        copy.write_bytes(manifest.read_bytes())
        manifest.parent.chmod(0o755)
        manifest.unlink()
        os.symlink(copy, manifest)
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn("软链", stderr)

    def test_deploy_aborts_when_bound_release_asset_has_an_extra_file(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        asset_dir = self.release_asset_dir(self.asset_id(self.skill))
        asset_dir.chmod(0o755)
        (asset_dir / "extra.md").write_text("# extra\n", encoding="utf-8")
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn("文件集合", stderr)

    def test_deploy_aborts_when_release_files_dir_is_a_symlink(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        files_dir = self.phe / "releases" / RELEASE_ID / "files"
        # 只换中间这一级：资产目录和文件本身都不是软链，内容也与 manifest 一致。
        copy = self.tmp / "copied-files"
        shutil.copytree(files_dir, copy, symlinks=True)
        files_dir.parent.chmod(0o755)
        rmtree_force(files_dir)
        os.symlink(copy, files_dir)
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn("软链", stderr)

    def test_deploy_aborts_when_manifest_name_escapes_the_release(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        asset = self.asset_id(self.shared)
        outside = self.tmp / "outside/AGENTS.md"
        write(outside, "# outside\n")
        release_dir = self.phe / "releases" / RELEASE_ID
        manifest_path = release_dir / "manifest.json"
        data = self.manifest()
        meta = data["assets"][asset]
        meta["name"] = os.path.relpath(outside, release_dir / meta["entry"])
        release_dir.chmod(0o755)
        manifest_path.chmod(0o644)
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        # claude 的链接精确指向按新 name 拼出的路径，文件集合与 sha256 仍与 manifest 一致。
        link = self.instructions["claude"]
        link.unlink()
        os.symlink(release_dir / meta["entry"] / meta["name"], link)
        stderr = self.assert_aborts_unchanged("codex", self.snapshot())
        self.assertIn("verify_failed", stderr)

    def test_deploy_aborts_when_bindings_release_id_is_not_a_release_name(self):
        self.assertEqual(self.deploy("claude").returncode, 0)
        for bad in ("../x", str(self.tmp / "elsewhere")):
            with self.subTest(release_id=bad):
                bindings = json.loads(json.dumps(self.bindings()))
                for path in bindings["claude"]:
                    bindings["claude"][path] = bad
                self.write_bindings(bindings)
                stderr = self.assert_aborts_unchanged("codex", self.snapshot())
                self.assertIn("发布编号不合法", stderr)

    def test_verify_runs_once_per_release_and_asset(self):
        """同一 release+asset 在一次预检里只校验一次：两条同资产漂移只打一条 verify_failed。"""
        self.assertEqual(self.deploy("claude").returncode, 0)
        self.assertEqual(self.deploy("codex").returncode, 0)
        target = self.release_target(self.asset_id(self.shared))
        target.chmod(0o644)
        target.write_text("# rewritten\n", encoding="utf-8")
        stderr = self.assert_aborts_unchanged("pi", self.snapshot())
        self.assertIn(str(self.instructions["claude"]), stderr)
        self.assertIn(str(self.instructions["codex"]), stderr)
        self.assertEqual(stderr.count("verify_failed"), 1)


if __name__ == "__main__":
    unittest.main()
