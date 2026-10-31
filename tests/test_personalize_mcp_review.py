"""R3b 第一片第 1 轮审查的 CLI 回归：四项 P1 按真实命令序列复现。

每个用例都从无关 cwd 调仓外副本里的产品入口，输入全部是合成 JSON；不读真实 Host 配置与凭证。
"""

import json
import unittest

from personalize_fixture import write
from personalize_mcp_fixture import (
    CLAUDE_PROJECT_STDIO_TYPE,
    MARKER,
    PI_USER_DISABLED,
    McpFixture,
)

# 同文件里 demo 是托管条目、other 未托管但结构可投影、secrets 带合成标记串：
# 改 server 标识的 patch 若被当成普通更新，会改写 other，而 journal 仍记 demo。
PI_WITH_OTHER = (
    "{\n"
    '    "mcpServers": {\n'
    '        "demo": {\n'
    '            "command": "npx",\n'
    '            "args": ["-y", "demo-mcp"]\n'
    "        },\n"
    '        "other": {\n'
    '            "command": "other-server",\n'
    '            "args": ["--other"]\n'
    "        },\n"
    '        "secrets": {\n'
    '            "command": "legacy-server",\n'
    '            "env": {"TOKEN": "' + MARKER + '"}\n'
    "        }\n"
    "    }\n"
    "}\n"
)


class McpServerRenameTest(McpFixture):
    """P1-1：改逻辑定义的 server 标识不是本片支持的更新。"""

    def test_server_rename_is_not_applicable_and_never_rewrites_unmanaged_entries(self):
        write(self.pi_user, PI_WITH_OTHER)
        self.init()
        self.asset_id = json.loads(self.adopt(f"pi={self.pi_user}").stdout)["asset_id"]
        self.change = self.package()
        renamed = json.loads(self.definition().read_text(encoding="utf-8"))
        renamed["server"] = "other"
        self.change = self.package([self.item("P1", json.dumps(renamed, ensure_ascii=False, indent=2) + "\n")])
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual(plan["items"][0]["state"], "not_applicable")
        self.assertIn("server 标识", plan["items"][0]["reason"])
        preview = (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8")
        self.assertNotIn(MARKER, preview)
        before = self.poke_files()
        blocked = self.apply()
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("当前不适用", blocked.stderr)
        self.assertEqual(self.poke_files(), before)
        leaked = [str(path) for path in self.phe.rglob("*") if path.is_file() and MARKER.encode() in path.read_bytes()]
        self.assertEqual(leaked, [])


class McpRestoreOrderTest(McpFixture):
    """P1-2：旧恢复计划不能覆盖后续应用，即使文件内容又回到旧值。"""

    def test_stale_restore_plan_is_refused_and_the_newest_restore_still_works(self):
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        self.assertEqual(self.apply(cycle="c1").returncode, 0)
        first = self.ledger()[-1]["execution_id"]
        stale_plan = self.restore_plan(first, "c2")
        stale_approval = self.approve_restore(stale_plan, first)
        # x2：--second；x3：又回到 --verbose（与 x1 应用后的字节相同）。
        for cycle, extra in (("c3", ("--second",)), ("c4", ("--verbose",))):
            self.change = self.package([self.item("P1", self.definition_with_args(*extra))])
            self.assertEqual(self.preview(cycle).returncode, 0)
            applied = self.apply(cycle=cycle)
            self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual([fact["kind"] for fact in self.ledger()], ["approval", "apply"] * 3)
        before = self.poke_files()
        refused = self.restore(first, stale_plan, stale_approval)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("逆序恢复", refused.stderr)
        self.assertEqual(self.poke_files(), before)
        newest = self.ledger()[-1]["execution_id"]
        newest_plan = self.restore_plan(newest, "c5")
        restored = self.restore(newest, newest_plan, self.approve_restore(newest_plan, newest))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        entry = json.loads(self.poke_files()["pi"])["mcpServers"]["demo"]
        self.assertEqual(entry["args"], ["-y", "demo-mcp", "--second"])


class McpPreserveEnabledTest(McpFixture):
    """P1-3：普通更新保留各目标现有的启停状态；改启停不在本片。"""

    pi_text = PI_USER_DISABLED

    def test_plain_update_keeps_pi_disabled_and_claude_without_the_field(self):
        self.init()
        payload = json.loads(self.adopt(f"claude={self.claude_project}", f"pi={self.pi_user}").stdout)
        self.asset_id = payload["asset_id"]
        self.assertEqual([row["state"] for row in payload["targets"]], ["in_sync", "in_sync"])
        self.change = self.package()
        self.assertEqual(self.preview().returncode, 0)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual([row["drift"] for row in plan["items"][0]["targets"]], [["args"], ["args"]])
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        pi_entry = json.loads(self.poke_files()["pi"])["mcpServers"]["demo"]
        claude_entry = json.loads(self.poke_files()["claude"])["mcpServers"]["demo"]
        self.assertIs(pi_entry["enabled"], False)
        self.assertNotIn("enabled", claude_entry)
        self.assertEqual(pi_entry["args"], ["-y", "demo-mcp", "--verbose"])
        execution = self.ledger()[-1]["execution_id"]
        restore_plan = self.restore_plan(execution, "c2")
        restored = self.restore(execution, restore_plan, self.approve_restore(restore_plan, execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.poke_files()["pi"], self.original[self.pi_user])
        self.assertEqual(self.poke_files()["claude"], self.original[self.claude_project])

    def test_adopt_accepts_a_disabled_pi_entry_as_the_first_target(self):
        """Pi 条目带 `enabled: false` 时先登记也能通过：Claude 的 `.mcp.json` 没有启停字段，不因此拒绝登记。

        条目参数一致，但定义声明了启停又没有 `claude-toggle` 目标：Claude 那一行报 `toggle_unbound`，
        不能报成整体同步（普通参数更新仍然可执行）。
        """
        self.init()
        payload = json.loads(self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}").stdout)
        self.asset_id = payload["asset_id"]
        self.assertEqual([row["state"] for row in payload["targets"]], ["in_sync", "toggle_unbound"])
        self.assertEqual(payload["targets"][1]["fields"], ["enabled"])
        definition = json.loads(self.definition().read_text(encoding="utf-8"))
        self.assertIs(definition["enabled"], False)
        self.change = self.package()
        self.assertEqual(self.preview().returncode, 0)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual([row["drift"] for row in plan["items"][0]["targets"]], [["args"], ["args"]])
        self.assertEqual(self.apply().returncode, 0)
        self.assertIs(json.loads(self.poke_files()["pi"])["mcpServers"]["demo"]["enabled"], False)
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["in_sync", "toggle_unbound"])
        self.assertEqual(drift["targets"][1]["fields"], ["enabled"])
        self.assertIn("启停", drift["targets"][1]["state_note"])

    def test_enabled_change_without_a_claude_toggle_target_is_not_applicable(self):
        """改启停要有 Claude 的启停目标：没有就整组不可执行，不退化成只改 Pi、把定义标成一致。"""
        self.init()
        self.asset_id = json.loads(self.adopt(f"pi={self.pi_user}", f"claude={self.claude_project}").stdout)["asset_id"]
        enabled = json.loads(self.definition().read_text(encoding="utf-8"))
        enabled["enabled"] = True
        self.change = self.package([self.item("P1", json.dumps(enabled, ensure_ascii=False, indent=2) + "\n")])
        self.assertEqual(self.preview().returncode, 0)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual(plan["items"][0]["state"], "not_applicable")
        self.assertIn("claude-toggle", plan["items"][0]["reason"])
        self.assertEqual(self.apply().returncode, 1)
        self.assertEqual(self.poke_files()["pi"], self.original[self.pi_user])
        self.assertEqual(self.poke_files()["claude"], self.original[self.claude_project])


class McpExplicitStdioTypeTest(McpFixture):
    """P1-4：Claude 官方的显式 `"type": "stdio"` 要能走完整个闭环。"""

    claude_text = CLAUDE_PROJECT_STDIO_TYPE

    def test_explicit_stdio_type_round_trips_through_the_whole_loop(self):
        self.prepare()
        self.assertEqual(self.preview().returncode, 0)
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["execution_id"], "x0001")
        claude = self.poke_files()["claude"].decode("utf-8")
        before = self.original[self.claude_project].decode("utf-8")
        self.assertEqual(claude[claude.index('"unmanaged"') :], before[before.index('"unmanaged"') :])
        entry = json.loads(claude)["mcpServers"]["demo"]
        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["args"], ["-y", "demo-mcp", "--verbose"])
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["in_sync", "in_sync"])
        execution = self.ledger()[-1]["execution_id"]
        self.review(execution)
        restore_plan = self.restore_plan(execution, "c2")
        restored = self.restore(execution, restore_plan, self.approve_restore(restore_plan, execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.poke_files()["pi"], self.original[self.pi_user])
        self.assertEqual(self.poke_files()["claude"], self.original[self.claude_project])


if __name__ == "__main__":
    unittest.main()
