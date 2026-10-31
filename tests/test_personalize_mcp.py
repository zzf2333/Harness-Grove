"""R3b 产品入口（MCP 条目）验收：主路径更新与边界拒绝。

合成 Host 配置与 PHE_HOME 见 `personalize_mcp_fixture`：托管 `demo` 与未托管 `unmanaged` 同文件共存，
未托管条目的值里有合成标记串，用来证明它不进预览、计划、journal、发布或账本。
"""

import json
import os
import stat
import unittest

from personalize_fixture import sha256, write
from personalize_mcp_fixture import (
    MARKER,
    McpFixture,
    mcp_config,
)


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
        restore_plan = self.restore_plan(execution, "c2")
        restored = self.restore(execution, restore_plan, self.approve_restore(restore_plan, execution))
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
        self.assertEqual(
            mcp_config.definition_from_entry("claude", {"type": "stdio", "command": "npx"}, "demo")["transport"],
            "stdio",
        )
        with self.assertRaises(ValueError):
            mcp_config.definition_from_entry("claude", {"type": "sse", "command": "npx"}, "demo")


if __name__ == "__main__":
    unittest.main()
