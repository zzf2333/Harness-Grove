"""R2 产品入口测试共用的合成夹具：合成 Host home、仓外 PHE_HOME、仓库副本与无关 cwd。

全部在临时目录里跑，不读真实 home：三个 Host 入口是指向同一条合成指令的软链，会话文件只有虚构内容。
`PHE_IN_HOME=True` 的子类把 PHE_HOME 放在合成 home 下（受保护形式的合成路径），用来核对产品入口
不继承旧沙箱的真实路径拒绝。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_release import rmtree_force

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TOOL = TOOLS / "personalize.py"
WORKSPACE = TOOLS / "workspace.py"
INVENTORY = TOOLS / "inventory.py"
REGISTRY = TOOLS / "registry.py"
CHANGE = TOOLS / "change.py"

# 合成指令：第 1、3、4 行是约束，第 2 行是待调整的偏好。
BASE = "先给结论。\n先复述问题再回答。\n用户说先别做时，不要越界执行。\n复盘时先读账本。\n"
REPORT_TEXT = "# 合成报告\n\n本报告是测试夹具里宿主写的示例，不是自动诊断。\n"

# 偏好：第 2 行改成“先给结论，再复述问题”，不改其余行。
PATCH_P1 = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -1,3 +1,3 @@
 先给结论。
-先复述问题再回答。
+先给结论，再复述问题。
 用户说先别做时，不要越界执行。
"""

# 假设：第 4 行；上下文用第 3 行（P1 不改它），所以 P1 之后仍能打到当前内容上。
PATCH_P2 = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -3,2 +3,2 @@
 用户说先别做时，不要越界执行。
-复盘时先读账本。
+复盘时先读账本，并对照原预测。
"""

PI_SESSION = """\
{"type":"session","id":"{sid}","cwd":"/home/synthetic/proj","timestamp":"2026-10-30T09:00:00.000Z","version":"0"}
{"type":"message","timestamp":"2026-10-30T09:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"以后回答先给结论。"}]}}
{"type":"message","timestamp":"2026-10-30T09:00:02.000Z","message":{"role":"user","content":[{"type":"text","text":"不对，复盘时也要对照原预测。"}]}}
{"type":"message","timestamp":"2026-10-30T09:00:03.000Z","message":{"role":"assistant","content":[{"type":"text","text":"收到。"}]}}
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_cli(tool: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(tool), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )


def snapshot(root: Path) -> dict[str, str]:
    """路径 → 类型与内容指纹；软链只记指向，不跟随。"""
    data = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            data[key] = "link:" + os.readlink(path)
        elif path.is_file():
            data[key] = "file:" + sha256(path.read_bytes())
        else:
            data[key] = "dir"
    return data


class PersonalizeFixture(unittest.TestCase):
    """合成 home（三个共享入口）+ 仓外 PHE_HOME + 仓库副本 + 无关 cwd。"""

    PHE_IN_HOME = False

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="personalize-test-")))
        self.addCleanup(rmtree_force, self.tmp)
        self.home = self.tmp / "home"
        self.shared = self.tmp / "shared" / "AGENTS.md"
        self.phe = (self.home / ".phe") if self.PHE_IN_HOME else (self.tmp / "phe")
        self.out = self.tmp / "out"
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()
        self.source = self.tmp / "src"
        shutil.copytree(TOOLS, self.source / "tools")
        self.tool = self.source / "tools" / "personalize.py"
        self.entries = {
            "claude": self.home / ".claude/CLAUDE.md",
            "codex": self.home / ".codex/AGENTS.md",
            "pi": self.home / ".pi/agent/AGENTS.md",
        }
        write(self.shared, BASE)
        for path in self.entries.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self.shared, path)
        # 合成 pi 会话：两周期各一份，行号就是证据指针里的 L<行号>。
        self.pi_root = self.tmp / "pi-root"
        self.session_c1 = self.pi_root / "sessions/--home-synthetic-proj--/2026-10-30T09-00-00-000Z_c1.jsonl"
        self.session_c2 = self.pi_root / "sessions/--home-synthetic-proj--/2026-10-31T09-00-00-000Z_c2.jsonl"
        write(self.session_c1, PI_SESSION.replace("{sid}", "c1"))
        write(
            self.session_c2,
            PI_SESSION.replace("{sid}", "c2")
            .replace("2026-10-30T09:00:0", "2026-10-31T09:00:0")
            .replace("以后回答先给结论。", "恢复前先给我看预览。")
            .replace("不对，复盘时也要对照原预测。", "不要再顺手改相邻文件。"),
        )
        self.report = self.out / "report.md"
        write(self.report, REPORT_TEXT)

    # 前置：初始化工作区、登记共享指令资产、准备报告与变更包。

    def cli(self, *args: str, tool: Path | None = None) -> subprocess.CompletedProcess:
        return run_cli(tool or self.tool, *args, cwd=self.elsewhere)

    def init(self) -> subprocess.CompletedProcess:
        return self.cli("init", "--phe-home", str(self.phe), tool=self.source / "tools/workspace.py")

    def inventory(self, name: str = "inventory.json") -> Path:
        out = self.phe / "registry" / name
        result = self.cli("--home", str(self.home), "--out", str(out), tool=self.source / "tools/inventory.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def adopt(self) -> None:
        result = self.cli(
            "adopt",
            "--inventory",
            str(self.inventory()),
            "--registry",
            str(self.phe / "registry/assets.json"),
            "--group",
            str(self.shared),
            tool=self.source / "tools/registry.py",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def prepare(self) -> None:
        self.assertEqual(self.init().returncode, 0)
        self.adopt()
        self.asset_id = json.loads((self.phe / "registry/assets.json").read_text(encoding="utf-8"))["assets"][0][
            "asset_id"
        ]
        self.change = self.package(
            [
                self.item("P1", PATCH_P1, 2, "user_preference"),
                self.item("P2", PATCH_P2, 4, "hypothesis"),
            ]
        )

    def item(self, item_id: str, patch: str, line: int, kind: str) -> dict:
        return {
            "item_id": item_id,
            "kind": kind,
            "evidence": [f"{self.session_c1}:L{line}"],
            "prediction": {
                "why": f"合成证据 L{line} 指向的偏好或观察",
                "expected": f"{item_id} 生效；不丢“用户说先别做时不越界”",
                "verify": "读一遍入口内容；收益待验证，没有 Host 加载或行为证据时记 unknown",
            },
            "target": {"asset_id": self.asset_id},
            "patch": patch,
        }

    def package(self, items: list[dict], name: str = "change.json") -> Path:
        data = {
            "schema_version": 1,
            "report": {"path": str(self.report), "sha256": sha256(self.report.read_bytes())},
            "items": items,
        }
        path = self.out / name
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve(self, plan: Path, items: list[str], source: str = "user", name: str = "approval.json") -> Path:
        data = {
            "schema_version": 1,
            "approval_id": f"approval-{name}",
            "source": source,
            "plan_sha256": sha256(plan.read_bytes()),
            "items": items,
            "note": "测试夹具：合成批准，不代表真实用户同意",
        }
        path = self.out / name
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def approve_restore(self, plan: Path, execution: str, source: str = "user") -> Path:
        data = {
            "schema_version": 1,
            "approval_id": f"approval-restore-{execution}",
            "source": source,
            "execution": execution,
            "plan_sha256": sha256(plan.read_bytes()),
            "note": "测试夹具：合成恢复批准，不代表真实用户同意",
        }
        path = self.out / "approval-restore.json"
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def review_record(self, name: str, verdict: str, prediction_met: str, observations: list[str]) -> Path:
        data = {
            "verdict": verdict,
            "prediction_met": prediction_met,
            "reason": f"合成复盘：{verdict}；只在合成 home 应用，Host 是否加载未验证",
            "observations": observations,
            "next": "按 prediction.verify 在观察窗内统计；没有行为证据前记 unknown",
        }
        path = self.out / name
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def cycle_record(self, name: str, cycle: str, lines: list[int], summary: str, source: Path | None = None) -> Path:
        data = {
            "schema_version": 1,
            "cycle": cycle,
            "recorded_at": "2026-10-31T12:00:00+00:00",
            "summary": summary,
            "processed_evidence": [
                {
                    "source_path": str(source or (self.session_c1 if cycle == "c1" else self.session_c2)),
                    "lines": lines,
                    "kind": "user_preference",
                    "from_ts": "2026-10-30T09:00:00+00:00",
                    "to_ts": "2026-10-31T09:00:00+00:00",
                }
            ],
            "next_observations": ["恢复预览与批准是否按计划执行", "下一周期只收新证据"],
        }
        path = self.out / name
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    # CLI 包装：全部从无关 cwd 调用仓库副本里的产品入口。

    def preview(self, cycle: str, change: Path | None = None) -> subprocess.CompletedProcess:
        return self.cli(
            "preview", "--phe-home", str(self.phe), "--change", str(change or self.change), "--cycle", cycle
        )

    def plan_path(self, cycle: str) -> Path:
        return self.phe / "reports" / cycle / "plan.json"

    def apply_cli(self, plan: Path, item: str, approval: Path) -> subprocess.CompletedProcess:
        return self.cli(
            "apply", "--phe-home", str(self.phe), "--plan", str(plan), "--item", item, "--approval", str(approval)
        )

    def restore_preview(self, cycle: str, execution: str) -> subprocess.CompletedProcess:
        return self.cli("restore-preview", "--phe-home", str(self.phe), "--execution", execution, "--cycle", cycle)

    def restore_cli(self, plan: Path, execution: str, approval: Path) -> subprocess.CompletedProcess:
        return self.cli(
            "restore",
            "--phe-home",
            str(self.phe),
            "--execution",
            execution,
            "--plan",
            str(plan),
            "--approval",
            str(approval),
        )

    def ledger(self) -> list[dict]:
        return [json.loads(line) for line in (self.phe / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]

    def effective(self) -> bytes:
        return self.entries["pi"].read_bytes()
