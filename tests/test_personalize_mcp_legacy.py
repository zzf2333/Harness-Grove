"""R3b 第一片（base `5a7ba99`）生成的 MCP 恢复计划在新版本上继续执行。

`legacy_mcp_restore/` 里的恢复计划由 `5a7ba99` 的产品入口真实生成（字段集合与结构都是旧版原样，`entries[]`
没有后加的 `span`），见该目录的 README。用例按当前临时目录还原 `@ROOT@`、按账本还原实际 journal，并重新写一份
绑定这份计划字节的合成恢复批准：路径变了计划字节就变了，旧批准对不上（与 R2 旧计划夹具同一口径，不是原字节重放）。
恢复入口的核对顺序不变：恢复批准、PHE_HOME、执行 ID、逆序、journal、两侧完整文件 sha256。
"""

import json
import unittest
from pathlib import Path

from personalize_fixture import sha256, write
from personalize_mcp_fixture import McpFixture

LEGACY = Path(__file__).resolve().parent / "legacy_mcp_restore"


class LegacyMcpRestorePlanTest(McpFixture):
    """旧恢复计划：条目目标按当前绑定 server 定位运行时跨度；内容变化仍拒绝恢复。"""

    def applied(self) -> str:
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        return self.ledger()[-1]["execution_id"]

    def legacy_restore_plan(self, execution: str) -> Path:
        fact = next(fact for fact in self.ledger() if fact["kind"] == "apply" and fact["execution_id"] == execution)
        plan = json.loads((LEGACY / "restore-x0001.json").read_text(encoding="utf-8").replace("@ROOT@", str(self.tmp)))
        plan["journals"] = fact["journals"]
        path = self.out / "legacy-restore.json"
        write(path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        return path

    def test_first_slice_restore_plan_still_restores_both_entries(self):
        execution = self.applied()
        plan = self.legacy_restore_plan(execution)
        self.assertNotIn("span", plan.read_text(encoding="utf-8"))
        restored = self.restore(execution, plan, self.approve_restore(plan, execution))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["plan_sha256"], sha256(plan.read_bytes()))
        self.assertEqual(self.poke_files()["pi"], self.original[self.pi_user])
        self.assertEqual(self.poke_files()["claude"], self.original[self.claude_project])
        self.assertEqual(self.ledger()[-1]["kind"], "restore")

    def test_first_slice_restore_plan_refuses_changed_entry_content(self):
        execution = self.applied()
        plan = self.legacy_restore_plan(execution)
        approval = self.approve_restore(plan, execution)
        edited = self.poke_files()["pi"].decode("utf-8").replace('"--verbose"', '"--edited"')
        write(self.pi_user, edited)
        refused = self.restore(execution, plan, approval)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("在恢复预览之后变了", refused.stderr)
        self.assertEqual(self.poke_files()["pi"], edited.encode("utf-8"))
        self.assertEqual(self.ledger()[-1]["kind"], "apply")


if __name__ == "__main__":
    unittest.main()
