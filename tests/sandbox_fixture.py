"""沙箱测试共用的合成实验夹具：3 组 × 1 次，Target 由各测试提供。"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import test_phe

phe = test_phe.phe


def sandbox_section(**overrides) -> dict[str, Any]:
    section: dict[str, Any] = {"mechanism": "macos-sandbox-exec"}
    section.update(overrides)
    return section


class RunFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-sandbox-run-")
        self.addCleanup(temp.cleanup)
        self.exp = Path(temp.name)
        self.base = self.exp / "base"
        self.base.mkdir()
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        (self.base / "task.txt").write_text("fixture")
        self.p = test_phe.proposal(count=1, controls=False)
        del self.p["rules"]["required_control"]
        self.cfg: dict[str, Any] = {"harness": {}}
        for g, v in self.p["harness"]["variants"].items():
            h = self.exp / (g + ".md")
            h.write_text("synthetic harness")
            self.cfg["harness"][g] = str(h)
            v["snapshot_sha256"] = phe.sha(h)
        self.addCleanup(self.clean_workdirs)

    def clean_workdirs(self):
        for record in phe.records(self.exp):
            shutil.rmtree(Path(record["workdir"]).parent, ignore_errors=True)

    def common_config(self):
        trusted = self.exp / "trusted.txt"
        trusted.write_text("oracle fixture")
        self.cfg.update(
            {
                "base_dir": str(self.base),
                "experiment_id": "synthetic",
                "experiment_dir": str(self.exp),
                "env": {},
                "oracle_test_src": str(trusted),
                "oracle_test_dest": "oracle.txt",
                "oracle_cwd": ".",
                "oracle_argv": [sys.executable, "-I", "-c", "print('Tests 1 passed')"],
                "nonce_sha256": "unused",
            }
        )

    def write_proposal(self):
        (self.exp / "proposal.json").write_text(json.dumps(self.p))
        digest = phe.sha(self.exp / "proposal.json")
        (self.exp / "proposal.sha256").write_text(digest)
        (self.exp / "approval.json").write_text(json.dumps({"proposal_sha256": digest}))

    def freeze(self):
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        runner = {"config_sha256": phe.sha(self.exp / "config.json")}
        if self.cfg.get("sandbox") is not None:
            # 新 sandbox 实验冻结实际加载的 runner 模块 Hash。
            runner["runner_files_sha256"] = phe.runner_file_hashes()
        self.p["runner"] = runner
        self.write_proposal()
