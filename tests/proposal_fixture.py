"""`tools/proposal.py` 测试共用的合成夹具：临时仓库副本、分组 JSON、Host 候选 JSON。

所有输入都是手写合成材料，路径和名字都是虚构的（`/home/alice/...`），不来自任何真实会话。
仓库根由 `tools/proposal.py` 的源码位置推导，所以夹具把 `tools/` 和 `examples/` 复制到临时仓库，
用真实 CLI 跑，从不写本仓库的 `experiments/`。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "examples/m-1/proposal.template.json").read_text(encoding="utf-8"))
EXPERIMENT_ID = "m3a-demo-001"
PROJECT = "/home/alice/proj"
SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"
CLAUDE_PATH = "/home/alice/.claude/projects/-home-alice-proj/aaaa.jsonl"
CODEX_PATH = "/home/alice/.codex/sessions/2026/10/28/rollout-bbbb.jsonl"
GROUP_A = "aaaaaaaaaaaa"
GROUP_B = "bbbbbbbbbbbb"
OLD_GROUP = "dddddddddddd"
RETIRED_ID_A = "retired-demo-001"
RETIRED_ID_B = "retired-demo-002"
EXCERPT_SENTINEL = "SENTINEL-EXCERPT-不应进入提案"
REPO_FILES = ("tools", "examples")


def groups_document():
    return {
        "groups": [
            {
                "correlation_group": GROUP_A,
                "host": "claude",
                "session_id": SESSION_A,
                "project": PROJECT,
                "kind": "user_correction",
                "count": 2,
                "first_ts": "2026-10-28T01:00:00Z",
                "last_ts": "2026-10-28T01:00:05Z",
                "episodes": [
                    {"source_path": CLAUDE_PATH, "line": 3, "ts": "2026-10-28T01:00:00Z", "excerpt": EXCERPT_SENTINEL},
                    {"source_path": CLAUDE_PATH, "line": 9, "ts": "2026-10-28T01:00:05Z", "excerpt": EXCERPT_SENTINEL},
                ],
                "same_project": [],
            },
            {
                "correlation_group": GROUP_B,
                "host": "codex",
                "session_id": SESSION_B,
                "project": PROJECT,
                "kind": "retry",
                "count": 1,
                "first_ts": "2026-10-28T02:00:00Z",
                "last_ts": "2026-10-28T02:00:00Z",
                "episodes": [
                    {"source_path": CODEX_PATH, "line": 4, "ts": "2026-10-28T02:00:00Z", "excerpt": EXCERPT_SENTINEL}
                ],
                "same_project": [],
            },
        ],
        "sources_unavailable": [],
    }


def valid_candidate():
    return {
        "groups": [GROUP_A, GROUP_B],
        "hypothesis": {
            "observation": "同一会话里反复出现同类纠正",
            "change": "把该约束写成一条更短的规则",
            "expected_effect": "同类纠正减少，测试通过数不下降",
        },
        "predictions": [
            {
                "metric": "test_pass_v1",
                "expected_change": "不下降",
                "falsified_when": "处理组比基线低 2 次以上",
            }
        ],
    }


def retired_document():
    """切片 B1 的合成 Retired 检索输入：两条记录都早于示例分组的时间。"""
    return {
        "schema_version": 1,
        "scope": "合成示例：本文件只列出提供者给出的 2 条退役记录，不代表完整历史",
        "entries": [
            {
                "retired_id": RETIRED_ID_A,
                "method": "把格式要求展开成整段长规则",
                "rejected_reason": "规则过长，同类纠正次数没有下降",
                "retired_at": "2026-10-20T00:00:00Z",
                "evidence_groups": [OLD_GROUP],
            },
            {
                "retired_id": RETIRED_ID_B,
                "method": "每次回答前输出检查清单",
                "rejected_reason": "增加墙钟时间，测试通过数没有提升",
                "retired_at": "2026-10-27T00:00:00Z",
                "evidence_groups": [],
            },
        ],
    }


def candidate_with_review(review=None):
    """完整声明：默认把第一条判为 alternative（用 GROUP_A 的新证据），第二条判为 unrelated。"""
    candidate = valid_candidate()
    candidate["retired_review"] = review or {
        "relation": "alternative",
        "reason": "目标相同，机制不同，且引用了退役之后的新证据分组",
        "entries": [
            {
                "retired_id": RETIRED_ID_A,
                "relation": "alternative",
                "reason": "用更短的规则达到同一目标，并引用了退役之后的分组",
                "new_evidence_groups": [GROUP_A],
            },
            {
                "retired_id": RETIRED_ID_B,
                "relation": "unrelated",
                "reason": "对象不同：本候选不涉及输出前检查清单",
            },
        ],
    }
    return candidate


class RepoCopyCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-proposal-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(os.path.realpath(temp.name))
        self.repo = self.tmp / "repo"
        for relative in REPO_FILES:
            shutil.copytree(
                ROOT / relative,
                self.repo / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        self.experiments = self.repo / "experiments"
        self.groups_path = self.tmp / "groups.json"
        self.candidate_path = self.tmp / "candidate.json"
        self.retired_path = self.tmp / "retired.json"
        self.write_groups(groups_document())
        self.write_candidate(valid_candidate())
        self.write_retired(retired_document())

    def write_groups(self, document, name="groups.json"):
        path = self.tmp / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if name == "groups.json":
            self.groups_path = path
        return path

    def write_candidate(self, document, name="candidate.json"):
        path = self.tmp / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if name == "candidate.json":
            self.candidate_path = path
        return path

    def write_retired(self, document, name="retired.json"):
        path = self.tmp / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if name == "retired.json":
            self.retired_path = path
        return path

    def cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-I", str(self.repo / "tools/proposal.py"), *argv],
            cwd=self.tmp,
            capture_output=True,
            text=True,
            check=False,
        )

    def draft(self, experiment_id=EXPERIMENT_ID, groups=None, candidate=None, retired=None):
        argv = [
            "draft",
            "--groups",
            str(groups or self.groups_path),
            "--candidate",
            str(candidate or self.candidate_path),
        ]
        if retired is not None:
            argv.extend(["--retired", str(retired)])
        argv.extend(["--experiment-id", experiment_id])
        return self.cli(*argv)

    def check(self, experiment_id=EXPERIMENT_ID, retired=None):
        argv = ["check", "--experiment-id", experiment_id]
        if retired is not None:
            argv.extend(["--retired", str(retired)])
        return self.cli(*argv)

    def proposal(self, experiment_id=EXPERIMENT_ID):
        return json.loads((self.experiments / experiment_id / "proposal.json").read_text(encoding="utf-8"))

    def edit_proposal(self, mutate, experiment_id=EXPERIMENT_ID):
        path = self.experiments / experiment_id / "proposal.json"
        proposal = json.loads(path.read_text(encoding="utf-8"))
        mutate(proposal)
        path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        return proposal

    def assert_no_artifact(self, experiment_id=EXPERIMENT_ID):
        self.assertFalse((self.experiments / experiment_id).exists())
        self.assertFalse(self.experiments.exists())

    def snapshot(self):
        # 解释器可能写 __pycache__，那不是本工具写出的产物。
        return {
            str(path.relative_to(self.repo)): b"<dir>" if path.is_dir() else path.read_bytes()
            for path in sorted(self.repo.rglob("*"))
            if "__pycache__" not in path.parts and path.suffix != ".pyc"
        }

    def full_snapshot(self):
        """不排除任何文件的仓库副本快照：谁写的 __pycache__/*.pyc 都必须现形。"""
        return {
            str(path.relative_to(self.repo)): b"<dir>" if path.is_dir() else path.read_bytes()
            for path in sorted(self.repo.rglob("*"))
        }
