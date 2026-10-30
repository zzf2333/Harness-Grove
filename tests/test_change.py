"""tools/change.py 的闭环切片 2 验收：批准 → apply → 逆序 restore，账本与文件一致。

fixture 全在临时目录：temper 副本 + 三个 Agent 入口软链（共享同一 instructions 资产）+ 临时 PHE_HOME。
三条合成改动改同一文件的不同行：P1 改第 2 行，P2 改第 4 行，P3 改第 5 行。P3 的 hunk 以 P1+P2 之后
仍不变的 `delta` 作上下文，所以复用陈旧暂存内容（含 `delta B`）会让 P3 打不上。
"""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_release import (
    INVENTORY,
    REGISTRY,
    ROOT,
    rmtree_force,
    run_cli,
    write,
)

CHANGE = ROOT / "tools/change.py"
BASE = "alpha\nbeta\ngamma\ndelta\nepsilon\n"

PATCH_P1 = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -1,3 +1,3 @@
 alpha
-beta
+beta A
 gamma
"""

PATCH_P2 = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -3,3 +3,3 @@
 gamma
-delta
+delta B
 epsilon
"""

# 第 5 行：上下文含 `delta`（P2 改过的行），陈旧暂存内容会让这条 hunk 失败。
PATCH_P3 = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -3,3 +3,3 @@
 gamma
 delta
-epsilon
+epsilon C
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def execution_states(status: dict) -> list[dict]:
    """status 里每个执行的状态，去掉最新 review：它本来就是随 review 变的一项。"""
    return [{key: value for key, value in item.items() if key != "latest_review"} for item in status["executions"]]


class ChangeFixture(unittest.TestCase):
    """沙箱：temper 副本 + 临时 home（三个 Agent 的软链）+ 临时 PHE_HOME + 合成报告与变更包。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="change-test-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.temper = self.tmp / "temper"
        self.home = self.tmp / "home"
        self.phe = self.tmp / "phe"
        self.out = self.tmp / "out"
        self.shared = self.temper / "config/AGENTS.md"
        self.entries = {
            "claude": self.home / ".claude/CLAUDE.md",
            "codex": self.home / ".codex/AGENTS.md",
            "pi": self.home / ".pi/agent/AGENTS.md",
        }
        write(self.shared, BASE)
        for path in self.entries.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self.shared, path)
        self.report = self.out / "report.md"
        write(self.report, "# 合成报告\n\nP1/P2/P3 都是合成改动，不来自真实会话。\n")
        inventory = self.inventory("inventory-1.json")
        self.reg = self.out / "registry.json"
        result = run_cli(
            REGISTRY, "adopt", "--inventory", str(inventory), "--registry", str(self.reg), "--group", str(self.shared)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.asset_id = json.loads(self.reg.read_text(encoding="utf-8"))["assets"][0]["asset_id"]
        self.change = self.package(
            [
                self.item("P1", PATCH_P1, 2),
                self.item("P2", PATCH_P2, 4),
                self.item("P3", PATCH_P3, 5),
            ]
        )

    # 输入工件：变更包与批准记录都由宿主显式准备。

    def inventory(self, name: str) -> Path:
        out = self.out / name
        result = run_cli(INVENTORY, "--home", str(self.home), "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def item(self, item_id: str, patch: str, line: int) -> dict:
        return {
            "item_id": item_id,
            "evidence": [f"{self.shared}:L{line}"],
            "prediction": {"expected": f"{item_id} 生效", "verify": "读三个入口的内容"},
            "target": {"asset_id": self.asset_id},
            "patch": patch,
        }

    def package(self, items: list[dict]) -> Path:
        data = {
            "schema_version": 1,
            "report": {"path": str(self.report), "sha256": sha256(self.report.read_bytes())},
            "items": items,
        }
        path = self.out / "change.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve(self, items: list[str], name: str = "approval.json", change: Path | None = None) -> Path:
        data = {
            "schema_version": 1,
            "approval_id": f"approval-{name}",
            "source": "sandbox_rehearsal",
            "change_sha256": sha256((change or self.change).read_bytes()),
            "items": items,
            "note": "合成演练批准，不是用户批准",
        }
        path = self.out / name
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    # CLI 包装。

    def apply(self, item: str, approval: Path, change: Path | None = None) -> dict:
        result = self.apply_cli(item, approval, change)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def apply_cli(self, item: str, approval: Path, change: Path | None = None) -> subprocess.CompletedProcess:
        return run_cli(
            CHANGE,
            "apply",
            "--change",
            str(change or self.change),
            "--item",
            item,
            "--approval",
            str(approval),
            "--registry",
            str(self.reg),
            "--phe-home",
            str(self.phe),
            "--home",
            str(self.home),
        )

    def restore(self, execution: str) -> dict:
        result = self.restore_cli(execution)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def restore_cli(self, execution: str) -> subprocess.CompletedProcess:
        return run_cli(
            CHANGE,
            "restore",
            "--execution",
            execution,
            "--phe-home",
            str(self.phe),
            "--home",
            str(self.home),
        )

    def status(self) -> dict:
        result = run_cli(CHANGE, "status", "--phe-home", str(self.phe))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def record(self, name: str, verdict: str, prediction_met: str, observations: list[str]) -> Path:
        data = {
            "verdict": verdict,
            "prediction_met": prediction_met,
            "reason": f"合成复盘：{verdict}",
            "observations": observations,
            "next": "按 prediction.verify 在观察窗内统计",
        }
        path = self.out / name
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def review(self, execution: str, record: Path) -> dict:
        result = self.review_cli(execution, record)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def review_cli(self, execution: str, record: Path) -> subprocess.CompletedProcess:
        return run_cli(CHANGE, "review", "--execution", execution, "--record", str(record), "--phe-home", str(self.phe))

    def review_plan(self) -> dict:
        result = run_cli(CHANGE, "review-plan", "--phe-home", str(self.phe))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # 观察面。

    def ledger(self) -> list[dict]:
        path = self.phe / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def effective(self) -> bytes:
        return self.entries["pi"].read_bytes()

    def bindings(self) -> dict:
        path = self.phe / "bindings.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class ChangeSequenceTests(ChangeFixture):
    def test_approved_items_apply_restore_in_reverse_order(self):
        """只批准 P1 → 再批准 P2 → 恢复 P2 → 从当前版本应用 P3 → 逆序恢复回原字节。"""
        # 1. 只有 P1 获批。
        first = self.apply("P1", self.approve(["P1"]))
        self.assertEqual((first["execution_id"], first["release"]), ("x0001", "r0001"))
        self.assertEqual(len(first["journals"]), 3)
        self.assertIn("beta A", self.effective().decode())
        self.assertNotIn("delta B", self.effective().decode())
        for path in self.entries.values():
            self.assertIn("beta A", path.read_bytes().decode())

        # 账本：批准与应用是两条事实，绑定同一变更包版本和获批条目。
        facts = self.ledger()
        self.assertEqual([fact["kind"] for fact in facts], ["approval", "apply"])
        self.assertEqual(facts[0]["source"], "sandbox_rehearsal")
        self.assertEqual(facts[0]["approved_items"], ["P1"])
        self.assertEqual(facts[0]["change_sha256"], sha256(self.change.read_bytes()))
        self.assertEqual(facts[0]["execution_id"], first["execution_id"])
        applied = facts[1]
        self.assertEqual((applied["proposal_id"], applied["release"]), ("P1", "r0001"))
        self.assertEqual(applied["proposal_id"] == applied["execution_id"], False)
        self.assertEqual(applied["before_sha256"], sha256(BASE.encode()))
        self.assertEqual(applied["after_sha256"], sha256(self.effective()))
        self.assertEqual(applied["report"]["sha256"], sha256(self.report.read_bytes()))
        self.assertEqual(applied["target"]["asset_id"], self.asset_id)
        self.assertEqual(sorted(applied["target"]["paths"]), sorted(str(path) for path in self.entries.values()))
        for journal in applied["journals"]:
            self.assertTrue((self.phe / journal).is_file(), journal)

        # status 从事实推导，且与文件一致。
        status = self.status()
        self.assertEqual([item["state"] for item in status["executions"]], ["applied"])
        self.assertEqual([item["execution_id"] for item in status["active"]], ["x0001"])
        self.assertEqual(status["targets"][0]["release"], "r0001")
        self.assertEqual(status["targets"][0]["sha256"], sha256(self.effective()))

        # 2. 再批准并应用 P2。
        second = self.apply("P2", self.approve(["P2"], "approval-p2.json"))
        self.assertEqual((second["execution_id"], second["release"]), ("x0002", "r0002"))
        self.assertIn("delta B", self.effective().decode())
        self.assertIn("beta A", self.effective().decode())

        # 3. 恢复 P2：P1 的改动保留，链接回到 P1 的发布并重新绑定。
        restored = self.restore("x0002")
        self.assertEqual(restored["execution_id"], "x0002")
        self.assertEqual(restored["release"], "r0001")
        self.assertEqual(restored["restored_sha256"], first["after_sha256"])
        self.assertIn("beta A", self.effective().decode())
        self.assertNotIn("delta B", self.effective().decode())
        self.assertEqual(
            {agent: sorted(paths.values()) for agent, paths in self.bindings().items()},
            {
                "claude": ["r0001"],
                "codex": ["r0001"],
                "pi": ["r0001"],
            },
        )
        status = self.status()
        self.assertEqual([item["state"] for item in status["executions"]], ["applied", "restored"])
        self.assertEqual([item["execution_id"] for item in status["active"]], ["x0001"])
        self.assertEqual(status["targets"][0]["release"], "r0001")

        # 4. 从当前生效内容应用 P3：before 是 P1 之后的实际内容，不带上已撤销的 P2。
        third = self.apply("P3", self.approve(["P3"], "approval-p3.json"))
        self.assertEqual((third["execution_id"], third["release"]), ("x0003", "r0003"))
        self.assertEqual(third["before_sha256"], first["after_sha256"])
        self.assertIn("epsilon C", self.effective().decode())
        self.assertIn("beta A", self.effective().decode())
        self.assertNotIn("delta B", self.effective().decode())

        # 5. 逆序恢复 P3、P1，回到原字节。
        self.restore("x0003")
        self.assertIn("beta A", self.effective().decode())
        self.assertNotIn("epsilon C", self.effective().decode())
        last = self.restore("x0001")
        self.assertEqual(last["release"], None)
        for path in self.entries.values():
            self.assertEqual(os.readlink(path), str(self.shared))
            self.assertEqual(path.read_bytes(), BASE.encode())
        self.assertEqual(self.bindings(), {})
        status = self.status()
        self.assertEqual(status["active"], [])
        self.assertEqual(status["targets"][0]["release"], None)
        self.assertEqual(status["targets"][0]["sha256"], sha256(BASE.encode()))
        # 只读来源：temper 副本一个字节都没动。
        self.assertEqual(self.shared.read_bytes(), BASE.encode())

    def test_review_records_history_without_changing_the_execution(self):
        """apply → review(observe) → review(keep)：两条历史都保留，恢复后仍能读到，文件和状态不变。"""
        self.apply("P1", self.approve(["P1"]))
        target = self.entries["pi"]
        before_bytes = target.read_bytes()
        before_link = os.readlink(target)
        before_status = self.status()

        observe = self.record("review-observe.json", "observe", "unknown", [])
        first = self.review("x0001", observe)
        self.assertEqual((first["kind"], first["execution_id"], first["proposal_id"]), ("review", "x0001", "P1"))
        self.assertEqual(first["record_sha256"], sha256(observe.read_bytes()))
        self.assertEqual(first["record_path"], str(observe))
        self.assertEqual(first["record"]["verdict"], "observe")
        # review 不改目标文件、发布链接和执行状态。
        after_status = self.status()
        self.assertEqual(target.read_bytes(), before_bytes)
        self.assertEqual(os.readlink(target), before_link)
        self.assertEqual(execution_states(after_status), execution_states(before_status))
        self.assertEqual(after_status["targets"], before_status["targets"])

        keep = self.record("review-keep.json", "keep", "met", [f"{self.shared}:L2", str(self.report)])
        second = self.review("x0001", keep)
        self.assertEqual([fact["kind"] for fact in self.ledger()], ["approval", "apply", "review", "review"])

        applied = self.ledger()[1]
        plan = self.review_plan()
        self.assertIn("观察窗", plan["note"])
        self.assertEqual(len(plan["executions"]), 1)
        entry = plan["executions"][0]
        self.assertEqual((entry["execution_id"], entry["proposal_id"], entry["state"]), ("x0001", "P1", "applied"))
        self.assertEqual(entry["prediction"], applied["prediction"])
        self.assertEqual(entry["evidence"], applied["evidence"])
        self.assertEqual(entry["report"], applied["report"])
        self.assertEqual(entry["observation_window"], {"from": applied["at"], "to": None})
        self.assertEqual(entry["reviews"], [first, second])
        self.assertEqual(entry["latest_review"], second)
        self.assertEqual(self.status()["executions"][0]["latest_review"], second)

        # 恢复后 review 历史仍在，观察窗收到恢复时刻；review 不阻止恢复，也不重复恢复。
        restored = self.restore("x0001")
        self.assertEqual(restored["release"], None)
        entry = self.review_plan()["executions"][0]
        self.assertEqual(entry["state"], "restored")
        self.assertEqual(entry["observation_window"], {"from": applied["at"], "to": restored["at"]})
        self.assertEqual([row["record"]["verdict"] for row in entry["reviews"]], ["observe", "keep"])
        self.assertEqual(entry["latest_review"], second)
        status = self.status()
        self.assertEqual([item["state"] for item in status["executions"]], ["restored"])
        self.assertEqual(status["executions"][0]["latest_review"], second)
        self.assertEqual(target.read_bytes(), BASE.encode())

    def test_restore_refuses_out_of_order(self):
        """同一资产只支持逆序恢复：P2 还生效时不能先恢复 P1。"""
        self.apply("P1", self.approve(["P1"]))
        self.apply("P2", self.approve(["P2"], "approval-p2.json"))
        before = self.effective()
        result = self.restore_cli("x0001")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("逆序", result.stderr)
        self.assertEqual(self.effective(), before)
        self.assertEqual([fact["kind"] for fact in self.ledger()], ["approval", "apply", "approval", "apply"])

    def test_apply_requires_an_approval_covering_the_item(self):
        """批准记录没覆盖的条目不能应用：不改文件、不追加事实。"""
        self.apply("P1", self.approve(["P1"]))
        before = self.effective()
        facts = len(self.ledger())
        result = self.apply_cli("P2", self.approve(["P1"], "approval-p1-again.json"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("批准", result.stderr)
        self.assertEqual(self.effective(), before)
        self.assertEqual(len(self.ledger()), facts)


if __name__ == "__main__":
    unittest.main()
