"""tools/inventory.py 的字段契约、软链标记、分组与密钥不外泄验收。"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/inventory.py"

BASE_FIELDS = {
    "agent",
    "scope",
    "kind",
    "path",
    "is_symlink",
    "symlink_target",
    "resolved_path",
    "broken_link",
    "sha256",
    "size",
    "mtime",
    "status",
    "load_chain",
}
# 只在特定情况下出现的字段：密钥文件的顶层键名、访问失败的错误类型。
OPTIONAL_FIELDS = {"top_level_keys", "access_error"}
KINDS = {"instructions", "skill", "hook", "mcp", "settings", "rules", "memory", "extension", "prompt", "plugin"}

SENTINEL_JSON = "sk-json-7f3a1c9e"
SENTINEL_ENV = "tok-env-2b8d4f60"
SENTINEL_TOML = "toml-5e1a9c33"
SENTINEL_MCP = "mcp-9d2c7b41"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True, check=False)


def record_at(data: dict, path: Path, agent: str) -> dict:
    matches = [r for r in data["records"] if r["path"] == str(path) and r["agent"] == agent]
    assert len(matches) == 1, f"期望 1 条记录，实际 {len(matches)}：{agent} {path}"
    return matches[0]


class InventoryTests(unittest.TestCase):
    def setUp(self):
        # 先取真实路径，macOS 的 /var 软链会让路径断言变复杂。
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="inventory-test-")))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.other_project = self.tmp / "other-project"
        self.shared = self.tmp / "shared" / "AGENTS.md"
        self._build_fixture()

        self.out_dir = self.tmp / "out"
        self.out_dir.mkdir()
        self.out = self.out_dir / "local.json"
        before = tree(self.tmp)
        result = run_cli(
            "--home",
            str(self.home),
            "--project",
            str(self.project),
            "--project",
            str(self.other_project),
            "--out",
            str(self.out),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.created = tree(self.tmp) - before
        self.text = self.out.read_text(encoding="utf-8")
        self.data = json.loads(self.text)

    def _build_fixture(self):
        write(self.shared, "# shared\n")
        write(
            self.home / ".claude/settings.json", json.dumps({"apiKey": SENTINEL_JSON, "env": {"TOKEN": SENTINEL_ENV}})
        )
        write(self.home / ".codex/config.toml", f'model = "gpt"\n[secrets]\nkey = "{SENTINEL_TOML}"\n')
        write(self.home / ".claude/skills/demo/SKILL.md", "# demo\n")
        write(self.home / ".pi/agent/keep.txt", "")
        # 两条软链指向同一真实文件；一条断链；一条指向同目录的相对软链。
        os.symlink(self.shared, self.home / ".claude/CLAUDE.md")
        os.symlink(self.shared, self.home / ".codex/AGENTS.md")
        os.symlink(self.tmp / "shared/missing.md", self.home / ".pi/agent/AGENTS.md")
        os.symlink(Path("demo"), self.home / ".claude/skills/demo-alias")

        write(self.project / "AGENTS.md", "# project\n")
        write(self.project / ".mcp.json", json.dumps({"mcpServers": {"devtools": {"token": SENTINEL_MCP}}}))
        write(self.project / ".claude/settings.json", json.dumps({"permissions": {"allow": [SENTINEL_MCP]}}))
        write(self.project / ".agents/skills/review/SKILL.md", "# review\n")
        write(self.other_project / "AGENTS.md", "# other project\n")

    def test_top_level_shape_and_field_contract(self):
        self.assertEqual(self.data["home"], str(self.home))
        self.assertEqual(self.data["projects"], [str(self.project), str(self.other_project)])
        self.assertIn("未按官方文档逐项核实", self.data["path_table_note"])
        self.assertTrue(self.data["generated_at"].endswith("+00:00"))
        self.assertTrue(self.data["records"])
        for rec in self.data["records"]:
            with self.subTest(path=rec["path"], kind=rec["kind"]):
                self.assertLessEqual(BASE_FIELDS, set(rec))
                self.assertLessEqual(set(rec) - BASE_FIELDS, OPTIONAL_FIELDS)
                self.assertIn(rec["agent"], ("claude", "codex", "pi"))
                self.assertIn(rec["scope"], ("user", "project"))
                self.assertIn(rec["kind"], KINDS)
                self.assertEqual(rec["status"], "unmanaged")
                self.assertEqual(rec["load_chain"], "unverified")

    def test_regular_file_hash_and_shared_records(self):
        shared = record_at(self.data, self.project / "AGENTS.md", "pi")
        self.assertEqual(shared["sha256"], hashlib.sha256(b"# project\n").hexdigest())
        self.assertEqual(shared["size"], len(b"# project\n"))
        self.assertFalse(shared["is_symlink"])
        self.assertIsNone(shared["symlink_target"])
        self.assertFalse(shared["broken_link"])
        self.assertNotIn("top_level_keys", shared)
        # 同一项目 AGENTS.md 被三个 Agent 各记一条。
        agents = sorted(r["agent"] for r in self.data["records"] if r["path"] == str(self.project / "AGENTS.md"))
        self.assertEqual(agents, ["claude", "codex", "pi"])
        self.assertEqual(record_at(self.data, self.other_project / "AGENTS.md", "codex")["kind"], "instructions")

    def test_symlink_and_broken_link_marked(self):
        link = record_at(self.data, self.home / ".claude/CLAUDE.md", "claude")
        self.assertTrue(link["is_symlink"])
        self.assertEqual(link["symlink_target"], str(self.shared))
        self.assertEqual(link["resolved_path"], str(self.shared))
        self.assertFalse(link["broken_link"])
        self.assertEqual(link["sha256"], hashlib.sha256(b"# shared\n").hexdigest())

        broken = record_at(self.data, self.home / ".pi/agent/AGENTS.md", "pi")
        self.assertTrue(broken["is_symlink"])
        self.assertTrue(broken["broken_link"])
        self.assertEqual(broken["symlink_target"], str(self.tmp / "shared/missing.md"))
        self.assertIsNone(broken["resolved_path"])
        self.assertIsNone(broken["sha256"])
        self.assertIsNone(broken["size"])

    def test_skill_records_hash_skill_md(self):
        skill = record_at(self.data, self.home / ".claude/skills/demo", "claude")
        self.assertEqual(skill["kind"], "skill")
        self.assertEqual(skill["sha256"], hashlib.sha256(b"# demo\n").hexdigest())
        alias = record_at(self.data, self.home / ".claude/skills/demo-alias", "claude")
        self.assertEqual(alias["symlink_target"], "demo")
        self.assertEqual(alias["resolved_path"], str(self.home / ".claude/skills/demo"))
        self.assertEqual(alias["sha256"], skill["sha256"])
        self.assertEqual(record_at(self.data, self.project / ".agents/skills/review", "codex")["kind"], "skill")

    def test_groups_are_shared_real_paths(self):
        groups = {g["resolved_path"]: g for g in self.data["groups"]}
        shared_group = groups[str(self.shared)]
        self.assertEqual(shared_group["record_count"], 2)
        self.assertEqual(shared_group["agents"], ["claude", "codex"])
        self.assertEqual(
            shared_group["paths"], sorted([str(self.home / ".claude/CLAUDE.md"), str(self.home / ".codex/AGENTS.md")])
        )
        project_group = groups[str(self.project / "AGENTS.md")]
        self.assertEqual(project_group["record_count"], 3)
        self.assertEqual(project_group["agents"], ["claude", "codex", "pi"])
        skill_group = groups[str(self.home / ".claude/skills/demo")]
        self.assertEqual(skill_group["record_count"], 2)
        for group in self.data["groups"]:
            self.assertGreaterEqual(group["record_count"], 2)
            self.assertGreaterEqual(len(group["agents"]), 1)

    def test_secret_values_never_appear(self):
        for secret in (SENTINEL_JSON, SENTINEL_ENV, SENTINEL_TOML, SENTINEL_MCP):
            self.assertNotIn(secret, self.text)
        settings = record_at(self.data, self.home / ".claude/settings.json", "claude")
        self.assertEqual(settings["top_level_keys"], ["apiKey", "env"])
        self.assertEqual(
            settings["sha256"], hashlib.sha256((self.home / ".claude/settings.json").read_bytes()).hexdigest()
        )
        config = record_at(self.data, self.home / ".codex/config.toml", "codex")
        self.assertEqual(config["top_level_keys"], ["model", "secrets"])
        self.assertEqual(record_at(self.data, self.project / ".mcp.json", "claude")["top_level_keys"], ["mcpServers"])
        project_settings = record_at(self.data, self.project / ".claude/settings.json", "claude")
        self.assertEqual(project_settings["top_level_keys"], ["permissions"])

    def test_writes_only_out_file(self):
        self.assertEqual(self.created, {str(Path("out") / "local.json")})

    def test_stdout_without_out_writes_nothing(self):
        before = tree(self.tmp)
        result = run_cli("--home", str(self.home), "--project", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["records"][0]["status"], "unmanaged")
        self.assertEqual(tree(self.tmp), before)


class DirectoryCandidateTests(unittest.TestCase):
    """候选目录自身也记一条：有效软链、断链、软链环都保留，断链和失败不展开。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="inventory-dir-")))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.shared_skills = self.tmp / "shared-skills"
        write(self.shared_skills / "demo/SKILL.md", "# demo\n")
        write(self.project / ".claude/rules/rule.md", "# rule\n")
        (self.home / ".claude/projects/demo").mkdir(parents=True)
        (self.home / ".agents").mkdir(parents=True)
        os.symlink(self.shared_skills, self.home / ".claude/skills")
        os.symlink(self.tmp / "missing-skills", self.home / ".agents/skills")
        # 指向自己的软链：解析必然报错，盘点不能崩。
        os.symlink("memory", self.home / ".claude/projects/demo/memory")

        self.out = self.tmp / "out/local.json"
        result = run_cli("--home", str(self.home), "--project", str(self.project), "--out", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.data = json.loads(self.out.read_text(encoding="utf-8"))

    def test_valid_directory_symlink_has_own_record(self):
        own = record_at(self.data, self.home / ".claude/skills", "claude")
        self.assertEqual(own["kind"], "skill")
        self.assertTrue(own["is_symlink"])
        self.assertEqual(own["symlink_target"], str(self.shared_skills))
        self.assertEqual(own["resolved_path"], str(self.shared_skills))
        self.assertFalse(own["broken_link"])
        self.assertIsNone(own["sha256"])
        self.assertIsNone(own["size"])
        child = record_at(self.data, self.home / ".claude/skills/demo", "claude")
        self.assertEqual(child["sha256"], hashlib.sha256(b"# demo\n").hexdigest())

    def test_broken_directory_symlink_recorded_without_expansion(self):
        own = record_at(self.data, self.home / ".agents/skills", "codex")
        self.assertEqual(own["kind"], "skill")
        self.assertTrue(own["is_symlink"])
        self.assertTrue(own["broken_link"])
        self.assertEqual(own["symlink_target"], str(self.tmp / "missing-skills"))
        self.assertIsNone(own["resolved_path"])
        self.assertIsNone(own["sha256"])
        self.assertIsNone(own["size"])

    def test_directory_symlink_loop_recorded_as_access_error(self):
        own = record_at(self.data, self.home / ".claude/projects/demo/memory", "claude")
        self.assertEqual(own["kind"], "memory")
        self.assertTrue(own["is_symlink"])
        self.assertEqual(own["symlink_target"], "memory")
        self.assertFalse(own["broken_link"])
        self.assertIsNone(own["resolved_path"])
        self.assertEqual(own["access_error"], "OSError")

    def test_plain_directory_candidate_has_own_record(self):
        own = record_at(self.data, self.project / ".claude/rules", "claude")
        self.assertEqual(own["kind"], "rules")
        self.assertFalse(own["is_symlink"])
        self.assertIsNone(own["sha256"])
        self.assertIsNone(own["size"])
        entry = record_at(self.data, self.project / ".claude/rules/rule.md", "claude")
        self.assertEqual(entry["size"], len(b"# rule\n"))


class PermissionDeniedTests(unittest.TestCase):
    """无权限路径保留候选记录并标 access_error，不误判断链，也不中断后续盘点。"""

    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行无法用 chmod 构造权限错误")
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="inventory-perm-")))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.locked = self.home / ".claude"
        self.locked_target = self.tmp / "locked-target"
        self.locked_rules = self.project / ".claude/rules"
        write(self.locked / "CLAUDE.md", "# locked\n")
        write(self.locked / "settings.json", "{}")
        write(self.locked_target / "AGENTS.md", "# locked target\n")
        write(self.home / ".codex/AGENTS.md", "# codex\n")
        write(self.locked_rules / "rule.md", "# rule\n")
        (self.home / ".pi/agent").mkdir(parents=True)
        os.symlink(self.locked_target / "AGENTS.md", self.home / ".pi/agent/AGENTS.md")
        # LIFO：先恢复权限，rmtree 才能删掉这几层。
        self.addCleanup(os.chmod, self.locked, 0o700)
        self.addCleanup(os.chmod, self.locked_target, 0o700)
        self.addCleanup(os.chmod, self.locked_rules, 0o700)
        os.chmod(self.locked, 0o000)
        os.chmod(self.locked_target, 0o000)
        os.chmod(self.locked_rules, 0o000)

        self.out = self.tmp / "out/local.json"
        result = run_cli("--home", str(self.home), "--project", str(self.project), "--out", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.data = json.loads(self.out.read_text(encoding="utf-8"))

    def test_unreadable_file_candidate_keeps_access_error(self):
        record = record_at(self.data, self.locked / "CLAUDE.md", "claude")
        self.assertEqual(record["access_error"], "PermissionError")
        self.assertFalse(record["is_symlink"])
        self.assertFalse(record["broken_link"])
        self.assertIsNone(record["resolved_path"])
        self.assertIsNone(record["sha256"])
        self.assertIsNone(record["size"])

    def test_unreadable_directory_candidate_recorded_once(self):
        own = record_at(self.data, self.locked / "skills", "claude")
        self.assertEqual(own["kind"], "skill")
        self.assertEqual(own["access_error"], "PermissionError")
        # 无权限时只记候选自身那一条，不展开也不丢候选。
        for record in self.data["records"]:
            if record["path"].startswith(str(self.locked) + os.sep):
                self.assertEqual(record.get("access_error"), "PermissionError", record["path"])

    def test_unreadable_symlink_target_is_not_broken_link(self):
        record = record_at(self.data, self.home / ".pi/agent/AGENTS.md", "pi")
        self.assertTrue(record["is_symlink"])
        self.assertEqual(record["symlink_target"], str(self.locked_target / "AGENTS.md"))
        self.assertFalse(record["broken_link"])
        self.assertIsNone(record["resolved_path"])
        self.assertEqual(record["access_error"], "PermissionError")

    def test_unreadable_candidate_directory_flags_access_error(self):
        own = record_at(self.data, self.locked_rules, "claude")
        self.assertEqual(own["kind"], "rules")
        self.assertEqual(own["access_error"], "PermissionError")
        self.assertEqual(own["resolved_path"], str(self.locked_rules))
        self.assertFalse(own["broken_link"])
        # 展开失败时不产生子项记录。
        self.assertEqual([r for r in self.data["records"] if r["path"] == str(self.locked_rules / "rule.md")], [])

    def test_candidates_after_failure_still_reported(self):
        codex = record_at(self.data, self.home / ".codex/AGENTS.md", "codex")
        self.assertEqual(codex["sha256"], hashlib.sha256(b"# codex\n").hexdigest())


if __name__ == "__main__":
    unittest.main()
