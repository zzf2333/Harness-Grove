"""R2 旧版（base `5000e23`）的应用/恢复计划在新版本上继续可用。

`legacy_r2/` 里的计划与批准由 5000e23 的产品入口真实生成（字段集合、结构与绑定顺序都是旧版原样），
见该目录的 README。用例按当前临时目录还原 `@ROOT@` 与 `@JOURNAL@`，按替换后的字节重新绑定
`plan.change.sha256` / 批准里的 `plan_sha256`；不修改已批准计划的字段，也不从新版计划删字段模拟。
旧计划只带 `current_sha256` / `expected_sha256` / 入口链接与恢复内容 sha；新版本按这些旧字段执行，
新增的账本字段（`asset_kind` / `before_files` / `after_files` / `restored_files`）从已核对的内容推导。
"""

import json
import os
import unittest
from pathlib import Path

from personalize_fixture import BASE, REPORT_TEXT, PersonalizeFixture, sha256, write

LEGACY = Path(__file__).resolve().parent / "legacy_r2"


def template(name: str) -> str:
    return (LEGACY / name).read_text(encoding="utf-8")


class LegacyPlanFixture(PersonalizeFixture):
    """旧版计划 + 批准；计划里的路径换成当前临时目录，绑定哈希按实际字节重算。"""

    def setUp(self):
        super().setUp()
        self.prepare()
        self.assertEqual(self.report.read_text(encoding="utf-8"), REPORT_TEXT)

    def legacy_plan(self) -> tuple[Path, Path]:
        write(self.change, template("change.json").replace("@ROOT@", str(self.tmp)))
        plan = json.loads(template("plan.json").replace("@ROOT@", str(self.tmp)))
        plan["change"]["sha256"] = sha256(self.change.read_bytes())
        plan_path = self.out / "legacy-plan.json"
        write(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        approval = json.loads(template("approval.json").replace("@ROOT@", str(self.tmp)))
        approval["plan_sha256"] = sha256(plan_path.read_bytes())
        approval_path = self.out / "legacy-approval.json"
        write(approval_path, json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
        return plan_path, approval_path

    def legacy_restore_plan(self, execution: str) -> tuple[Path, Path]:
        fact = next(fact for fact in self.ledger() if fact["kind"] == "apply" and fact["execution_id"] == execution)
        plan = json.loads(template("restore-x0001.json").replace("@ROOT@", str(self.tmp)))
        plan["journals"] = fact["journals"]
        plan_path = self.out / "legacy-restore.json"
        write(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        approval = json.loads(template("restore-approval.json").replace("@ROOT@", str(self.tmp)))
        approval["plan_sha256"] = sha256(plan_path.read_bytes())
        approval_path = self.out / "legacy-restore-approval.json"
        write(approval_path, json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
        return plan_path, approval_path

    def assert_applied(self) -> dict:
        plan_path, approval_path = self.legacy_plan()
        result = self.apply_cli(plan_path, "P1", approval_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = json.loads(result.stdout)
        self.assertEqual((applied["execution_id"], applied["release"]), ("x0001", "r0001"))
        return applied


class LegacyApplyTests(LegacyPlanFixture):
    def test_base_generated_plan_applies_with_the_old_hash_contract(self):
        self.assert_applied()
        self.assertIn("先给结论，再复述问题。", self.effective().decode())
        self.assertEqual(self.shared.read_bytes(), BASE.encode())
        row = next(fact for fact in self.ledger() if fact["kind"] == "apply")
        self.assertEqual(row["asset_kind"], "instructions")
        self.assertEqual(set(row["before_files"]), {"AGENTS.md"})
        self.assertEqual(set(row["after_files"]), {"AGENTS.md"})
        self.assertEqual(row["before_sha256"], sha256(BASE.encode()))
        self.assertEqual(row["after_sha256"], sha256(Path(os.readlink(self.entries["pi"])).read_bytes()))

    def test_base_generated_plan_rejects_changed_input(self):
        plan_path, approval_path = self.legacy_plan()
        write(self.shared, BASE + "本地附加规则：只在这台机器上生效。\n")
        result = self.apply_cli(plan_path, "P1", approval_path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("的内容在预览之后变了", result.stderr)
        self.assertFalse((self.phe / "ledger.jsonl").exists())


class LegacyRestoreTests(LegacyPlanFixture):
    def test_base_generated_restore_plan_restores_after_the_new_apply(self):
        applied = self.assert_applied()
        plan_path, approval_path = self.legacy_restore_plan(applied["execution_id"])
        result = self.restore_cli(plan_path, applied["execution_id"], approval_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = json.loads(result.stdout)
        self.assertEqual(restored["restored_files"], {"AGENTS.md": sha256(BASE.encode())})
        self.assertEqual(restored["restored_sha256"], sha256(BASE.encode()))
        self.assertEqual(self.effective(), BASE.encode())
        self.assertEqual(os.readlink(self.entries["pi"]), str(self.shared))


if __name__ == "__main__":
    unittest.main()
