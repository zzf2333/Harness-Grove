"""R3b 产品入口（MCP 条目）验收：两个合成 Host 配置、仓外 PHE_HOME、无关 cwd。

托管 `demo` 与未托管 `unmanaged` 同文件共存；未托管条目的值里有合成标记串，用来证明它不进预览、计划、
journal、发布或账本。所有文件都是手写合成内容，不读真实 Host 配置。
"""

import difflib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from personalize_fixture import ROOT, run_cli, sha256, write
from test_release import rmtree_force

# 按文件路径载入 tools/mcp_config.py：不把 tools/ 放进 sys.path，也不在源码树里写 __pycache__。
sys.dont_write_bytecode = True
_SPEC = importlib.util.spec_from_file_location("mcp_config", ROOT / "tools/mcp_config.py")
assert _SPEC is not None and _SPEC.loader is not None
mcp_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mcp_config)

MARKER = "SYNTHETIC-UNMANAGED-MARKER-7f3a"

# 4 空格缩进、`mcpServers` 前后都有外围键：托管跨度之外的字节必须原样保留。
PI_USER = (
    "{\n"
    '    "autoEnableCodemode": false,\n'
    '    "mcpServers": {\n'
    '        "demo": {\n'
    '            "command": "npx",\n'
    '            "args": ["-y", "demo-mcp"],\n'
    '            "env": {"DEMO_KEY": "${DEMO_KEY}"}\n'
    "        },\n"
    '        "unmanaged": {\n'
    '            "command": "legacy-server",\n'
    '            "env": {"LEGACY_TOKEN": "' + MARKER + '"}\n'
    "        }\n"
    "    },\n"
    '    "theme": "synthetic"\n'
    "}\n"
)

# 2 空格缩进、没有外围键：与上面不同的排版，验证渲染按文件自己的缩进步长对齐。
CLAUDE_PROJECT = (
    "{\n"
    '  "mcpServers": {\n'
    '    "demo": {\n'
    '      "command": "npx",\n'
    '      "args": ["-y", "demo-mcp"],\n'
    '      "env": {"DEMO_KEY": "${DEMO_KEY}"}\n'
    "    },\n"
    '    "unmanaged": {\n'
    '      "command": "legacy-server",\n'
    '      "env": {"LEGACY_TOKEN": "' + MARKER + '"}\n'
    "    }\n"
    "  }\n"
    "}\n"
)

REPORT_TEXT = "# 合成报告\n\n本报告是测试夹具里宿主写的示例，不是自动诊断。\n"


class McpFixture(unittest.TestCase):
    """合成 Pi 用户级 mcp.json 与 Claude 项目级 .mcp.json，加上仓外 PHE_HOME。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="personalize-mcp-test-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.elsewhere = self.tmp / "elsewhere"
        self.out = self.tmp / "out"
        self.phe = self.tmp / "phe"
        self.elsewhere.mkdir()
        self.source = self.tmp / "src"
        shutil.copytree(ROOT / "tools", self.source / "tools")
        self.tool = self.source / "tools/personalize.py"
        self.pi_user = self.home / ".pi/agent/mcp.json"
        self.claude_project = self.project / ".mcp.json"
        write(self.pi_user, PI_USER)
        write(self.claude_project, CLAUDE_PROJECT)
        self.pi_user.chmod(0o644)
        self.claude_project.chmod(0o600)
        self.original = {path: path.read_bytes() for path in (self.pi_user, self.claude_project)}
        self.report = self.out / "report.md"
        write(self.report, REPORT_TEXT)

    # 前置：初始化工作区、登记已存在的条目、准备报告与变更包。

    def cli(self, *args: str, tool: Path | None = None):
        return run_cli(tool or self.tool, *args, cwd=self.elsewhere)

    def init(self) -> None:
        result = self.cli("init", "--phe-home", str(self.phe), tool=self.source / "tools/workspace.py")
        self.assertEqual(result.returncode, 0, result.stderr)

    def adopt(self, *targets: str, expect: int = 0):
        result = self.cli(
            "mcp-adopt",
            "--phe-home",
            str(self.phe),
            "--server",
            "demo",
            *[item for target in targets for item in ("--target", target)],
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def prepare(self) -> None:
        self.init()
        result = self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}")
        self.asset_id = json.loads(result.stdout)["asset_id"]
        self.change = self.package()

    def definition(self) -> Path:
        return self.phe / "assets" / self.asset_id / "content/server.json"

    def patched_definition(self) -> str:
        data = json.loads(self.definition().read_text(encoding="utf-8"))
        data["args"] = data["args"] + ["--verbose"]
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def item(self, item_id: str, after: str) -> dict:
        before = self.definition().read_text(encoding="utf-8")
        patch = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/server.json",
                tofile="b/server.json",
            )
        )
        return {
            "item_id": item_id,
            "kind": "user_preference",
            "evidence": ["合成证据：托管条目要能加参数"],
            "prediction": {
                "why": "合成场景里 demo 需要 --verbose",
                "expected": f"{item_id} 生效；未托管的 legacy-server 条目字节不变",
                "verify": "读两个配置文件；Host 是否加载、能否连上都记 unknown",
            },
            "target": {"asset_id": self.asset_id},
            "patch": patch,
        }

    def package(self, items: list[dict] | None = None) -> Path:
        data = {
            "schema_version": 1,
            "report": {"path": str(self.report), "sha256": sha256(self.report.read_bytes())},
            "items": items or [self.item("P1", self.patched_definition())],
        }
        path = self.out / "change.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve(self, plan: Path, items: list[str], source: str = "user") -> Path:
        data = {
            "schema_version": 1,
            "approval_id": "approval-test",
            "source": source,
            "plan_sha256": sha256(plan.read_bytes()),
            "items": items,
            "note": "测试夹具：合成批准，不代表真实用户同意",
        }
        path = self.out / "approval.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve_restore(self, plan: Path, execution: str) -> Path:
        data = {
            "schema_version": 1,
            "approval_id": f"approval-restore-{execution}",
            "source": "user",
            "execution": execution,
            "plan_sha256": sha256(plan.read_bytes()),
            "note": "测试夹具：合成恢复批准，不代表真实用户同意",
        }
        path = self.out / "approval-restore.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def review(self, execution: str) -> Path:
        record = {
            "verdict": "observe",
            "prediction_met": "unknown",
            "reason": "合成复盘：只看两个文件的托管跨度变了；Host 是否加载未验证",
            "observations": ["托管条目已按定义渲染", "未托管条目字节不变"],
            "next": "按 prediction.verify 核对；没有行为证据前记 unknown",
        }
        path = self.out / "review.json"
        write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        result = self.cli("review", "--phe-home", str(self.phe), "--execution", execution, "--record", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        return path

    # CLI 包装：全部从无关 cwd 调用仓库副本里的产品入口。

    def preview(self, cycle: str = "c1"):
        return self.cli("preview", "--phe-home", str(self.phe), "--change", str(self.change), "--cycle", cycle)

    def plan(self, cycle: str = "c1") -> Path:
        return self.phe / "reports" / cycle / "plan.json"

    def apply(self, item: str = "P1", approval: Path | None = None):
        return self.cli(
            "apply",
            "--phe-home",
            str(self.phe),
            "--plan",
            str(self.plan()),
            "--item",
            item,
            "--approval",
            str(approval or self.approve(self.plan(), [item])),
        )

    def drift(self) -> dict:
        result = self.cli("mcp-drift", "--phe-home", str(self.phe))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def ledger(self) -> list[dict]:
        path = self.phe / "ledger.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def poke_files(self) -> dict:
        return {name: path.read_bytes() for name, path in (("pi", self.pi_user), ("claude", self.claude_project))}


class McpUpdateTest(McpFixture):
    """第一片主路径：更新一个已存在的条目，drift → 批准 → 应用 → 复盘 → 恢复。"""

    def test_adopt_writes_definition_and_bindings_without_touching_host(self):
        self.init()
        result = self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}")
        payload = json.loads(result.stdout)
        self.asset_id = payload["asset_id"]
        self.assertEqual(payload["server"], "demo")
        self.assertEqual([row["state"] for row in payload["targets"]], ["in_sync", "in_sync"])
        self.assertEqual([row["adapter"] for row in payload["targets"]], ["pi", "claude"])
        records = json.loads((self.phe / "registry/assets.json").read_text(encoding="utf-8"))["assets"]
        self.assertEqual(records[0]["baseline"]["kind"], "mcp_entry")
        self.assertEqual(len(records[0]["mcp"]["targets"]), 2)
        definition = json.loads(self.definition().read_text(encoding="utf-8"))
        self.assertEqual(definition["kind"], "phe_mcp_server")
        self.assertEqual(definition["env"], {"DEMO_KEY": "${DEMO_KEY}"})
        self.assertEqual(self.poke_files()["pi"], self.original[self.pi_user])

    def test_preview_shows_each_host_rendering_and_approval_scope(self):
        self.prepare()
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8")
        self.assertIn("为什么改：合成场景里 demo 需要 --verbose", preview)
        self.assertIn("改后期待什么：P1 生效", preview)
        self.assertIn("凭什么判断：读两个配置文件", preview)
        self.assertIn("各 Host 渲染差异", preview)
        self.assertIn(str(self.pi_user), preview)
        self.assertIn(str(self.claude_project), preview)
        self.assertIn('+    "--verbose"', preview)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual(plan["asset"]["server"], "demo")
        self.assertEqual([row["scope"] for row in plan["asset"]["targets"]], ["user", "project"])
        self.assertEqual([row["adapter"] for row in plan["items"][0]["targets"]], ["pi", "claude"])
        self.assertTrue(all(row["drift"] == ["args"] for row in plan["items"][0]["targets"]))

    def test_apply_rewrites_only_the_managed_span_and_restore_returns_original_bytes(self):
        self.prepare()
        self.assertEqual(self.preview().returncode, 0)
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        after = self.poke_files()
        for name, path in (("pi", self.pi_user), ("claude", self.claude_project)):
            text, before = after[name].decode("utf-8"), self.original[path].decode("utf-8")
            self.assertEqual(json.loads(text)["mcpServers"]["demo"]["args"], ["-y", "demo-mcp", "--verbose"])
            # 未托管条目与它后面的字节原样保留：从它的键名起比较整段尾部。
            self.assertEqual(text[text.index('"unmanaged"') :], before[before.index('"unmanaged"') :])
            self.assertEqual(text[: text.index('"demo"')], before[: before.index('"demo"')])
        self.assertEqual(stat.S_IMODE(os.stat(self.claude_project).st_mode), 0o600)
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["in_sync", "in_sync"])
        facts = self.ledger()
        self.assertEqual([fact["kind"] for fact in facts], ["approval", "apply"])
        self.assertEqual(facts[1]["asset_kind"], "mcp")
        self.assertEqual(facts[1]["target"]["server"], "demo")
        journal = json.loads((self.phe / facts[1]["journals"][0]).read_text(encoding="utf-8"))
        self.assertEqual(journal["kind"], "mcp_fragments")
        self.assertEqual(
            sorted(row["config_path"] for row in journal["targets"]),
            sorted(map(str, (self.pi_user, self.claude_project))),
        )
        execution = facts[1]["execution_id"]
        reviewed = self.review(execution)
        self.assertEqual([fact["kind"] for fact in self.ledger()], ["approval", "apply", "review"])
        self.assertEqual(self.ledger()[2]["execution_id"], execution)
        self.assertEqual(self.ledger()[2]["record_sha256"], sha256(reviewed.read_bytes()))
        restored_preview = self.cli(
            "restore-preview", "--phe-home", str(self.phe), "--execution", execution, "--cycle", "c2"
        )
        self.assertEqual(restored_preview.returncode, 0, restored_preview.stderr)
        restore_plan = self.phe / f"reports/c2/restore-{execution}.json"
        restored = self.cli(
            "restore",
            "--phe-home",
            str(self.phe),
            "--execution",
            execution,
            "--plan",
            str(restore_plan),
            "--approval",
            str(self.approve_restore(restore_plan, execution)),
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.poke_files()["pi"], self.original[self.pi_user])
        self.assertEqual(self.poke_files()["claude"], self.original[self.claude_project])
        self.assertEqual(stat.S_IMODE(os.stat(self.pi_user).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(self.claude_project).st_mode), 0o600)

    def test_unmanaged_marker_never_enters_artifacts(self):
        self.prepare()
        self.assertEqual(self.preview().returncode, 0)
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertNotIn(MARKER, applied.stdout + applied.stderr)
        leaked = []
        for path in sorted(self.phe.rglob("*")):
            if path.is_file() and MARKER.encode() in path.read_bytes():
                leaked.append(str(path.relative_to(self.phe)))
        self.assertEqual(leaked, [])
        journal = json.loads((self.phe / self.ledger()[1]["journals"][0]).read_text(encoding="utf-8"))
        self.assertNotIn("LEGACY_TOKEN", json.dumps(journal))

    def test_external_edit_is_reported_and_blocks_restore(self):
        self.prepare()
        self.assertEqual(self.preview().returncode, 0)
        self.assertEqual(self.apply().returncode, 0)
        applied = self.poke_files()
        edited = applied["claude"].decode("utf-8").replace('"--verbose"', '"--quiet"')
        write(self.claude_project, edited)
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["in_sync", "drifted"])
        self.assertEqual(drift["targets"][1]["fields"], ["args"])
        execution = self.ledger()[1]["execution_id"]
        blocked = self.cli("restore-preview", "--phe-home", str(self.phe), "--execution", execution, "--cycle", "c2")
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("与应用后的记录不一致", blocked.stderr)

    def test_apply_refuses_when_a_host_config_changed_after_preview(self):
        self.prepare()
        self.assertEqual(self.preview().returncode, 0)
        edited = self.original[self.pi_user].decode("utf-8").replace('"theme": "synthetic"', '"theme": "edited"')
        write(self.pi_user, edited)
        result = self.apply()
        self.assertEqual(result.returncode, 1)
        self.assertIn("在预览之后变了", result.stderr)
        self.assertEqual(self.poke_files()["claude"], self.original[self.claude_project])
        self.assertEqual([fact["kind"] for fact in self.ledger()], [])


class McpRefusalTest(McpFixture):
    """边界：缺条目不算新增、目标形状不对就拒绝、不支持字段明确报出。"""

    def test_missing_entry_is_reported_and_not_created(self):
        self.init()
        self.asset_id = json.loads(self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}").stdout)["asset_id"]
        without_demo = json.loads(self.original[self.claude_project])
        del without_demo["mcpServers"]["demo"]
        write(self.claude_project, json.dumps(without_demo, ensure_ascii=False, indent=2) + "\n")
        self.change = self.package()
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["in_sync", "missing"])
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual(plan["items"][0]["state"], "not_applicable")
        self.assertIn("没有 mcpServers 条目 demo", plan["items"][0]["reason"])
        blocked = self.apply()
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("当前不适用", blocked.stderr)

    def test_unsupported_target_shapes_are_rejected(self):
        self.init()
        for target in (f"claude={self.home}/.claude/settings.json", f"codex={self.home}/.codex/config.toml"):
            result = self.adopt(target, expect=1)
            self.assertIn("personalize:", result.stderr)
        registry = self.phe / "registry/assets.json"
        self.assertFalse(registry.is_file() and "mcp" in registry.read_text(encoding="utf-8"))

    def test_adapter_rules_and_credential_field_contract(self):
        http = {
            "schema_version": 1,
            "kind": "phe_mcp_server",
            "server": "demo",
            "transport": "http",
            "url": "https://example.invalid/mcp",
            "headers": {"AUTHORIZATION": "${TOKEN}"},
        }
        self.assertEqual(
            mcp_config.render_entry("claude", http),
            {"type": "http", "url": "https://example.invalid/mcp", "headers": {"AUTHORIZATION": "${TOKEN}"}},
        )
        self.assertNotIn("type", mcp_config.render_entry("pi", http))
        with self.assertRaises(ValueError) as disabled:
            mcp_config.render_entry("claude", dict(http, enabled=False))
        self.assertIn("enabled", str(disabled.exception))
        literal = dict(http, headers={"AUTHORIZATION": "Bearer synthetic"})
        with self.assertRaises(ValueError) as literal_error:
            mcp_config.check_definition(literal)
        self.assertIn("headers", str(literal_error.exception))
        text = '{"mcpServers": {"demo": {"command": "npx", "oauth": {"clientId": "${ID}"}}}}'
        self.assertEqual(
            mcp_config.drift("claude", dict(http, transport="stdio", command="npx"), text)["state"], "unsupported"
        )


if __name__ == "__main__":
    unittest.main()
