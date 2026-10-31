"""R3b 启停切片验收：Pi 条目 `enabled` 与 Claude 项目 `disabledMcpServers` 的启停闭环。

每个用例都从无关 cwd 调仓外副本里的产品入口，输入全部是合成 JSON；`.claude.json` 里放信任、审批、另一个
项目桶与 user 作用域条目，用来证明只动声明项目键的 `disabledMcpServers` 里绑定的那个名字。
"""

import json
import unittest

from personalize_fixture import write
from personalize_mcp_fixture import MARKER
from personalize_mcp_toggle_fixture import McpToggleFixture


class McpToggleLoopTest(McpToggleFixture):
    """主路径：禁用 → 复盘 → 再启用 → 逆序恢复原字节与权限位。"""

    def test_disable_enable_review_and_reverse_restore_round_trip(self):
        self.set_up_project()
        # 登记时逻辑定义里没有 enabled：这一项标 not_managed，不假装在管
        self.assertNotIn("enabled", json.loads(self.definition().read_text(encoding="utf-8")))
        self.assertEqual([row["state"] for row in self.drift()["targets"]], ["in_sync", "in_sync", "not_managed"])

        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        item = plan["items"][0]
        self.assertEqual(item["state"], "applicable", item.get("reason"))
        self.assertEqual([row["adapter"] for row in item["targets"]], ["pi", "claude", "claude-toggle"])
        self.assertEqual(item["targets"][2]["project_key"], self.project_key)
        self.assertEqual(item["targets"][2]["state"], "applicable")
        self.assertIn(str(self.claude_home), plan["host_writes"])
        preview = (self.phe / "reports/c1/preview.md").read_text(encoding="utf-8")
        self.assertIn("项目键", preview)
        self.assertIn(self.project_key, preview)
        for needle in (MARKER, "legacy-disabled", "approval-unmanaged", "other-server", "global-demo"):
            self.assertNotIn(needle, preview)
            self.assertNotIn(needle, self.plan().read_text(encoding="utf-8"))

        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        # 两个 Host 一致：Pi 条目显式 enabled: false；Claude 把名字写进声明项目键的启停列表
        pi_entry = json.loads(self.pi_user.read_text(encoding="utf-8"))["mcpServers"]["demo"]
        self.assertIs(pi_entry["enabled"], False)
        self.assertNotIn("enabled", json.loads(self.claude_project.read_text(encoding="utf-8"))["mcpServers"]["demo"])
        home = self.poke_home()
        self.assertEqual(home["projects"][self.project_key]["disabledMcpServers"], ["demo"])
        # 未托管：另一个项目桶、审批、信任、user 作用域条目原样
        original_home = json.loads(self.home_original)
        self.assertEqual(home["projects"][self.other_key], original_home["projects"][self.other_key])
        self.assertIs(self.project_bucket()["hasTrustDialogAccepted"], True)
        self.assertEqual(self.project_bucket()["disabledMcpjsonServers"], ["approval-unmanaged"])
        self.assertEqual(home["mcpServers"], original_home["mcpServers"])
        self.assertEqual(self.home_mode(), 0o600)
        self.assertEqual([row["state"] for row in self.drift()["targets"]], ["in_sync"] * 3)
        self.assertEqual(self.unmanaged_strings_in_phe(), [])

        execution = self.ledger()[-1]["execution_id"]
        journal = json.loads((self.phe / self.ledger()[-1]["journals"][0]).read_text(encoding="utf-8"))
        toggle = next(row for row in journal["targets"] if row["adapter"] == "claude-toggle")
        self.assertEqual(toggle["server"], "demo")
        self.assertEqual(toggle["project_key"], self.project_key)
        self.assertEqual(toggle["before_fragment"], "")
        self.assertIn('"disabledMcpServers": ["demo"]', toggle["after_fragment"])
        self.review(execution)

        # 再启用：Pi 去掉 enabled，Claude 从列表里删掉这个名字
        self.change = self.package([self.item("P2", self.definition_with(enabled=True))])
        self.assertEqual(self.preview("c2").returncode, 0)
        second = self.apply("P2", self.approve(self.plan("c2"), ["P2"]), cycle="c2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("enabled", json.loads(self.pi_user.read_text(encoding="utf-8"))["mcpServers"]["demo"])
        self.assertEqual(self.project_bucket()["disabledMcpServers"], [])
        self.assertEqual(self.unmanaged_strings_in_phe(), [])

        # 逆序恢复：先撤销启用，再撤销禁用；两次都核对字节与权限位
        enable_execution = self.ledger()[-1]["execution_id"]
        enable_plan = self.restore_plan(enable_execution, "c3")
        restored = self.restore(enable_execution, enable_plan, self.approve_restore(enable_plan, enable_execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["demo"])
        disable_plan = self.restore_plan(execution, "c4")
        restored = self.restore(execution, disable_plan, self.approve_restore(disable_plan, execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        # 「缺列表」是正常起点：恢复后声明项目桶里又没有了这个列表，整份文件回到原始字节
        self.assertNotIn("disabledMcpServers", self.project_bucket())
        self.assertEqual(self.claude_home.read_bytes(), self.home_original)
        self.assertEqual(self.pi_user.read_bytes(), self.original[self.pi_user])
        self.assertEqual(self.claude_project.read_bytes(), self.original[self.claude_project])
        self.assertEqual(self.home_mode(), 0o600)


class McpToggleFirstElementTest(McpToggleFixture):
    """删启停数组首项只删这个名字和它后面的逗号：下一项前的换行与缩进保留。"""

    project_toggle = '["demo",\n      "legacy-disabled"]'

    def test_removing_the_first_name_keeps_the_next_entry_layout_and_restores_bytes(self):
        self.set_up_project()
        self.change = self.package([self.item("P1", self.definition_with(enabled=True))])
        self.assertEqual(self.preview().returncode, 0)
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        expected = self.claude_home_text().replace('["demo",\n      "legacy-disabled"]', '[\n      "legacy-disabled"]')
        self.assertEqual(self.claude_home.read_text(encoding="utf-8"), expected)
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["legacy-disabled"])
        execution = self.ledger()[-1]["execution_id"]
        restore_plan = self.restore_plan(execution, "c2")
        restored = self.restore(execution, restore_plan, self.approve_restore(restore_plan, execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.claude_home.read_bytes(), self.home_original)
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["demo", "legacy-disabled"])


class McpToggleExistingEntriesTest(McpToggleFixture):
    """已有禁用项：别的名字保留，另一个项目键里的同名条目不动，已禁用时禁用补丁不写字节。"""

    project_toggle = '["legacy-disabled", "demo"]'

    def test_existing_entries_are_preserved_and_redundant_disable_writes_nothing(self):
        self.set_up_project()
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        before = self.claude_home.read_bytes()
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(self.claude_home.read_bytes(), before)
        self.assertIs(json.loads(self.pi_user.read_text(encoding="utf-8"))["mcpServers"]["demo"]["enabled"], False)
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["legacy-disabled", "demo"])

        # 启用：只删 demo，另一个名字保留；另一个项目键里的 demo 不动（演练批准走同一套写入范围）
        self.change = self.package([self.item("P2", self.definition_with(enabled=True))])
        self.assertEqual(self.preview("c2").returncode, 0)
        rehearsal = self.approve(self.plan("c2"), ["P2"], source="sandbox_rehearsal")
        second = self.apply("P2", rehearsal, cycle="c2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["source"], "sandbox_rehearsal")
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["legacy-disabled"])
        self.assertEqual(
            self.poke_home()["projects"][self.other_key]["disabledMcpServers"], ["demo", "legacy-disabled"]
        )
        self.assertEqual(self.unmanaged_strings_in_phe(), [])

        # 逆序恢复两次都回到原始字节
        enable_execution = self.ledger()[-1]["execution_id"]
        enable_plan = self.restore_plan(enable_execution, "c3")
        self.assertEqual(
            self.restore(enable_execution, enable_plan, self.approve_restore(enable_plan, enable_execution)).returncode,
            0,
        )
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["legacy-disabled", "demo"])
        disable_execution = next(fact["execution_id"] for fact in self.ledger() if fact["kind"] == "apply")
        disable_plan = self.restore_plan(disable_execution, "c4")
        self.assertEqual(
            self.restore(
                disable_execution, disable_plan, self.approve_restore(disable_plan, disable_execution)
            ).returncode,
            0,
        )
        self.assertEqual(self.claude_home.read_bytes(), self.home_original)
        self.assertEqual(self.pi_user.read_bytes(), self.original[self.pi_user])


class McpTogglePlainUpdateTest(McpToggleFixture):
    """普通参数更新不改启停：定义没有 `enabled`、或目标被手动开关过，都不读缺字段、也不改回目标字节。"""

    def test_plain_args_update_without_enabled_in_the_definition_keeps_all_three_targets(self):
        self.set_up_project()
        self.assertNotIn("enabled", json.loads(self.definition().read_text(encoding="utf-8")))
        before_home = self.claude_home.read_bytes()
        self.assertEqual(self.preview().returncode, 0)
        item = json.loads(self.plan().read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(item["state"], "applicable", item.get("reason"))
        toggle = next(row for row in item["targets"] if row["adapter"] == "claude-toggle")
        self.assertEqual(toggle["drift"], [])
        self.assertEqual(toggle["current_file_sha256"], toggle["expected_file_sha256"])
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(self.claude_home.read_bytes(), before_home)
        pi_entry = json.loads(self.pi_user.read_text(encoding="utf-8"))["mcpServers"]["demo"]
        self.assertEqual(pi_entry["args"], ["-y", "demo-mcp", "--verbose"])
        self.assertNotIn("enabled", pi_entry)
        self.assertEqual([row["state"] for row in self.drift()["targets"]], ["in_sync", "in_sync", "not_managed"])
        execution = self.ledger()[-1]["execution_id"]
        restore_plan = self.restore_plan(execution, "c2")
        restored = self.restore(execution, restore_plan, self.approve_restore(restore_plan, execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.claude_home.read_bytes(), self.home_original)
        self.assertEqual(self.pi_user.read_bytes(), self.original[self.pi_user])
        self.assertEqual(self.claude_project.read_bytes(), self.original[self.claude_project])

    def test_plain_args_update_keeps_a_hand_switched_toggle(self):
        self.set_up_project()
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["demo"])
        # 用户在 Claude 里手动启用：普通操作，不弹任何提示，之后的参数更新也不该把它改回去。
        home = self.poke_home()
        home["projects"][self.project_key]["disabledMcpServers"] = []
        write(self.claude_home, json.dumps(home, ensure_ascii=False, indent=2) + "\n")
        switched = self.claude_home.read_bytes()
        self.change = self.package([self.item("P2", self.definition_with_args("--second"))])
        self.assertEqual(self.preview("c2").returncode, 0)
        second = self.apply("P2", self.approve(self.plan("c2"), ["P2"]), cycle="c2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.claude_home.read_bytes(), switched)
        pi_entry = json.loads(self.pi_user.read_text(encoding="utf-8"))["mcpServers"]["demo"]
        self.assertEqual(pi_entry["args"], ["-y", "demo-mcp", "--second"])
        self.assertIs(pi_entry["enabled"], False)
        # 手动开关与定义不一致是 mcp-drift 的事：普通更新只改参数，不顺带纠正。
        self.assertEqual([row["state"] for row in self.drift()["targets"]], ["in_sync", "in_sync", "drifted"])


class McpToggleRestoreSpanTest(McpToggleFixture):
    """启停恢复只认生成计划时记下的跨度：缺了就拒绝，不按名字猜。"""

    def test_toggle_restore_plan_without_its_span_is_refused(self):
        self.set_up_project()
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        self.assertEqual(self.apply().returncode, 0)
        execution = self.ledger()[-1]["execution_id"]
        plan_path = self.restore_plan(execution, "c2")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        toggle = next(entry for entry in plan["entries"] if entry["adapter"] == "claude-toggle")
        toggle.pop("span")
        write(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        refused = self.restore(execution, plan_path, self.approve_restore(plan_path, execution))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("启停跨度", refused.stderr)
        self.assertEqual(self.project_bucket()["disabledMcpServers"], ["demo"])


class McpToggleInvalidationTest(McpToggleFixture):
    """预览之后正常编辑 `~/.claude.json`（哪怕是别的项目键）就要重新预览。"""

    def test_editing_claude_home_after_preview_invalidates_the_approval(self):
        self.set_up_project()
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        approval = self.approve(self.plan(), ["P1"])
        edited = self.poke_home()
        edited["projects"][self.other_key]["disabledMcpServers"].append("edited-later")
        write(self.claude_home, json.dumps(edited, ensure_ascii=False, indent=2) + "\n")
        blocked = self.apply(approval=approval)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("在预览之后变了", blocked.stderr)
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.pi_user.read_bytes(), self.original[self.pi_user])


class McpToggleScopeTest(McpToggleFixture):
    """多目标计划不能跳过 Claude：没有启停目标时整组不可执行；Pi 单目标可用；项目键与 Pi 覆盖边界。"""

    def test_claude_entry_without_a_toggle_target_blocks_the_whole_group(self):
        self.set_up_project(toggle=False)
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        item = json.loads(self.plan().read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(item["state"], "not_applicable")
        self.assertIn("claude-toggle", item["reason"])
        self.assertIn("disabledMcpServers", item["reason"])
        blocked = self.apply()
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("当前不适用", blocked.stderr)
        self.assertEqual(self.claude_home.read_bytes(), self.home_original)
        self.assertEqual(self.pi_user.read_bytes(), self.original[self.pi_user])
        self.assertEqual(self.ledger(), [])

    def test_pi_only_asset_can_change_enabled(self):
        self.init()
        self.asset_id = json.loads(self.adopt(f"pi={self.pi_user}").stdout)["asset_id"]
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        self.assertEqual(json.loads(self.plan().read_text(encoding="utf-8"))["items"][0]["state"], "applicable")
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIs(json.loads(self.pi_user.read_text(encoding="utf-8"))["mcpServers"]["demo"]["enabled"], False)
        self.change = self.package([self.item("P2", self.definition_with(enabled=True))])
        self.assertEqual(self.preview("c2").returncode, 0)
        second = self.apply("P2", self.approve(self.plan("c2"), ["P2"]), cycle="c2")
        self.assertEqual(second.returncode, 0, second.stderr)
        # 启用后条目回到投影前的字段集（启停字段消失）；条目按托管渲染重排，所以比 JSON 不等字节
        self.assertEqual(json.loads(self.pi_user.read_text(encoding="utf-8")), json.loads(self.original[self.pi_user]))
        self.assertEqual(self.pi_user.stat().st_mode & 0o777, 0o644)

    def test_unknown_project_key_is_refused_at_adopt(self):
        self.init()
        result = self.adopt_with_toggle(project_key=str(self.tmp / "missing-project"), expect=1)
        self.assertIn("项目键", result.stderr)
        self.assertFalse((self.phe / "registry/assets.json").is_file())

    def test_pi_project_enabled_only_override_is_unsupported(self):
        override = self.project / ".pi/mcp.json"
        write(override, '{\n  "mcpServers": {\n    "demo": {\n      "enabled": false\n    }\n  }\n}\n')
        self.init()
        result = self.adopt(f"pi={override}", expect=1)
        self.assertIn("只有 enabled 的项目级覆盖", result.stderr)


class McpToggleDriftTest(McpToggleFixture):
    """外部手改按目标分别报：Pi 条目看 enabled 字段，Claude 看声明项目键里的启停列表。"""

    def test_external_edits_are_reported_per_target(self):
        self.set_up_project()
        self.change = self.package([self.item("P1", self.definition_with(enabled=False))])
        self.assertEqual(self.preview().returncode, 0)
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual([row["state"] for row in self.drift()["targets"]], ["in_sync"] * 3)

        home = self.poke_home()
        home["projects"][self.project_key]["disabledMcpServers"] = []
        write(self.claude_home, json.dumps(home, ensure_ascii=False, indent=2) + "\n")
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["in_sync", "in_sync", "drifted"])
        self.assertEqual(drift["targets"][2]["fields"], ["enabled"])

        pi = json.loads(self.pi_user.read_text(encoding="utf-8"))
        pi["mcpServers"]["demo"].pop("enabled")
        write(self.pi_user, json.dumps(pi, ensure_ascii=False, indent=2) + "\n")
        drift = self.drift()
        self.assertEqual([row["state"] for row in drift["targets"]], ["drifted", "in_sync", "drifted"])
        self.assertEqual(drift["targets"][0]["fields"], ["enabled"])


if __name__ == "__main__":
    unittest.main()
