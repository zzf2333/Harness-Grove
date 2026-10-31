"""R2 两周期延续与产品/沙箱两套写入政策的对照。

两周期用合成会话（虚构内容）：第一周期收证据、应用、复盘、记录；第二周期只读回旧预测与结论，只收上一
周期没处理过的证据。写入政策对照在隔离测试里把合成 home 换成“真实 home”身份，旧入口仍拒写，产品入口
按计划写；不读本机真实 home。
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from personalize_fixture import TOOL, PersonalizeFixture, snapshot, write


def load_product_module():
    spec = importlib.util.spec_from_file_location("personalize_product", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TwoCycleTests(PersonalizeFixture):
    def setUp(self):
        super().setUp()
        self.prepare()

    def cycle(self, command: str, *args: str) -> dict:
        result = self.cli(command, "--phe-home", str(self.phe), *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}

    def test_second_cycle_reads_back_predictions_and_only_adds_new_evidence(self):
        """摘要 → 预览 → 批准 → 应用 → 复盘 → 周期记录 → 第二周期读回预测并只收新证据。"""
        # 第一周期：摘要写在 PHE_HOME 的 episodes/<cycle>/，旧 history CLI 的写入政策不变。
        result = self.cli(
            "digest", "--phe-home", str(self.phe), "--cycle", "c1", "--host", "pi", "--root", str(self.pi_root)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        index = json.loads((self.phe / "episodes/c1/digest/pi-index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["sessions_included"], 2)
        self.assertTrue((self.phe / "episodes/c1/digest/pi-1.md").is_file())

        self.assertEqual(self.preview("c1").returncode, 0)
        applied = self.apply_cli(self.plan_path("c1"), "P1", self.approve(self.plan_path("c1"), ["P1"], "user"))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        record = self.review_record("review-x1.json", "observe", "unknown", [f"{self.session_c1}:L2"])
        reviewed = self.cli("review", "--phe-home", str(self.phe), "--execution", "x0001", "--record", str(record))
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)

        self.cycle(
            "cycle-record",
            "--cycle",
            "c1",
            "--record",
            str(self.cycle_record("cycle-c1.json", "c1", [2, 3], "第一周期：只改了偏好一条，收益未知")),
        )
        stored = json.loads((self.phe / "episodes/c1/cycle.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["summary"], "第一周期：只改了偏好一条，收益未知")

        # 第二周期：先读回上一周期的证据范围、预测与结论。
        plan = self.cli("cycle-plan", "--phe-home", str(self.phe), "--cycle", "c2")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        data = json.loads(plan.stdout)
        self.assertIn("不是已处理证据水位", data["note"])
        self.assertEqual(data["previous_cycles"][0]["cycle"], "c1")
        self.assertEqual(data["previous_cycles"][0]["processed_evidence"][0]["lines"], [2, 3])
        self.assertEqual(data["executions"][0]["prediction"]["why"], "合成证据 L2 指向的偏好或观察")
        self.assertEqual(data["executions"][0]["latest_review"]["record"]["prediction_met"], "unknown")

        # 重复计入上一周期的证据被拒；新证据可以记录。
        repeated = self.cli(
            "cycle-record",
            "--phe-home",
            str(self.phe),
            "--cycle",
            "c2",
            "--record",
            str(self.cycle_record("cycle-c2-repeat.json", "c2", [2, 3], "重复旧证据", self.session_c1)),
        )
        self.assertEqual(repeated.returncode, 1)
        self.assertIn("已经在上一周期处理过", repeated.stderr)
        self.cycle(
            "cycle-record",
            "--cycle",
            "c2",
            "--record",
            str(self.cycle_record("cycle-c2.json", "c2", [5, 6], "第二周期：只新增证据，旧预测仍可读")),
        )
        result = self.cli(
            "digest",
            "--phe-home",
            str(self.phe),
            "--cycle",
            "c2",
            "--host",
            "pi",
            "--root",
            str(self.pi_root),
            "--since",
            "2026-10-31",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.phe / "episodes/c2/digest/pi-1.md").is_file())

        data = json.loads(self.cli("cycle-plan", "--phe-home", str(self.phe), "--cycle", "c3").stdout)
        self.assertEqual([cycle["cycle"] for cycle in data["previous_cycles"]], ["c1", "c2"])
        self.assertEqual(data["previous_cycles"][0]["processed_evidence"][0]["lines"], [2, 3])
        self.assertEqual(data["previous_cycles"][1]["processed_evidence"][0]["lines"], [5, 6])
        self.assertEqual(data["executions"][0]["latest_review"]["record"]["verdict"], "observe")
        self.assertEqual([fact["kind"] for fact in self.ledger()], ["approval", "apply", "review", "cycle", "cycle"])

    def test_cycle_record_requires_the_documented_fields(self):
        record = self.cycle_record("cycle-bad.json", "c1", [2], "缺 kind")
        data = json.loads(record.read_text(encoding="utf-8"))
        del data["processed_evidence"][0]["kind"]
        write(record, json.dumps(data, ensure_ascii=False))
        result = self.cli("cycle-record", "--phe-home", str(self.phe), "--cycle", "c1", "--record", str(record))
        self.assertEqual(result.returncode, 1)
        self.assertIn("kind", result.stderr)


class ProductScopeTests(PersonalizeFixture):
    """把合成 home 当成“真实 home”：旧沙箱入口仍拒写，产品入口按计划写。"""

    PHE_IN_HOME = True

    def test_product_entry_does_not_inherit_the_sandbox_policy(self):
        self.prepare()
        self.assertEqual(self.preview("c1").returncode, 0)
        plan = self.plan_path("c1")
        approval = self.approve(plan, ["P1"], "user")
        registry = self.phe / "registry/assets.json"
        product = load_product_module()
        release_deploy = sys.modules["release_deploy"]
        change_tool = sys.modules["change"]
        before = snapshot(self.home)

        with patch.object(release_deploy, "real_home", return_value=self.home):
            # 旧沙箱政策原样保留：合成 home 现在是“真实 home”，入口路径与 PHE_HOME 都被拒。
            for guarded in (self.home, self.home / ".pi", self.phe):
                with self.subTest(path=str(guarded)), self.assertRaises(release_deploy.GuardError):
                    release_deploy.sandbox_roots(guarded, self.tmp / "phe")
            self.assertIsNotNone(release_deploy.refusal_reason(self.home / ".pi/agent/AGENTS.md"))
            with contextlib.redirect_stderr(io.StringIO()):
                code = change_tool.main(
                    [
                        "apply",
                        "--change",
                        str(self.change),
                        "--item",
                        "P1",
                        "--approval",
                        str(approval),
                        "--registry",
                        str(registry),
                        "--phe-home",
                        str(self.phe),
                        "--home",
                        str(self.home),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(snapshot(self.home), before)

            # 同一个“真实 home”身份下，产品入口按批准的计划写入口与 PHE_HOME。
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    product.main(
                        ["preview", "--phe-home", str(self.phe), "--change", str(self.change), "--cycle", "c1"]
                    ),
                    0,
                )
            approval = self.approve(plan, ["P1"], "user")
            with contextlib.redirect_stdout(io.StringIO()) as captured, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    product.main(
                        [
                            "apply",
                            "--phe-home",
                            str(self.phe),
                            "--plan",
                            str(plan),
                            "--item",
                            "P1",
                            "--approval",
                            str(approval),
                        ]
                    ),
                    0,
                )
            self.assertIn("先给结论，再复述问题。", self.effective().decode())
            for path in self.entries.values():
                self.assertTrue(Path(os.readlink(path)).is_relative_to(self.home / ".phe"))
            self.assertEqual(json.loads(captured.getvalue())["source"], "user")

            # 计划之外的路径仍然被拒：写入范围不是整个 home。
            scope = product.planning.ApprovedScope(self.phe, list(self.entries.values()))
            scope.check(self.entries["pi"])
            with self.assertRaises(release_deploy.GuardError):
                scope.check(self.home / ".claude/other.md")


if __name__ == "__main__":
    unittest.main()
