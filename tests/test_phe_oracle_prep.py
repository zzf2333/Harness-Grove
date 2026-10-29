"""oracle 准备阶段的可信测试覆盖：目的路径必须留在验收副本内，拒绝要留证据。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import test_phe
from sandbox_fixture import RunFixture

phe = test_phe.phe

# Target 把验收副本的目的文件留成指向副本外的外链，并记下本次工作目录供测试清理。
ORACLE_LINK_TARGET = """\
import json, sys
from pathlib import Path

Path(sys.argv[1]).write_text(str(Path.cwd().parent))
Path("oracle.txt").symlink_to(sys.argv[2])
print(json.dumps({"modelUsage": {"stub": {"inputTokens": 100}}, "total_cost_usd": 0.1}))
"""


class DestTests(unittest.TestCase):
    """准备阶段在沙箱外覆盖目的文件，所以不论是否声明 sandbox 都要先核对真实写入位置。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="phe-oracle-prep-")
        self.addCleanup(temp.cleanup)
        self.exp = Path(temp.name)
        self.denied = self.exp / "denied"
        self.denied.mkdir()
        self.victim = self.denied / "victim.txt"
        self.victim.write_text("ORIGINAL")
        self.work = self.exp / "oracle-work"
        self.work.mkdir()
        (self.work / "trusted.txt").symlink_to(self.victim)
        (self.work / "linkdir").symlink_to(self.denied, target_is_directory=True)
        self.trusted = self.exp / "trusted.src"
        self.trusted.write_text("TRUSTED TEST")
        self.art = self.exp / "art"
        self.art.mkdir()
        self.cfg = {
            "oracle_test_src": str(self.trusted),
            "oracle_test_dest": "trusted.txt",
            "oracle_argv": [sys.executable, "-I", "-c", "print('Tests 1 passed')"],
            "oracle_cwd": ".",
            "oracle_timeout_s": 1,
            "process_kill_grace_seconds": 1,
        }

    def test_dest_must_resolve_inside_the_copy(self):
        cases = ("trusted.txt", "linkdir/victim.txt", "../denied/victim.txt", str(self.victim))
        for dest in cases:
            with self.subTest(dest=dest):
                self.cfg["oracle_test_dest"] = dest
                with self.assertRaisesRegex(ValueError, "oracle 目的路径"):
                    phe.oracle(self.work, self.cfg, self.art, receipt_required=False)
                self.assertEqual(self.victim.read_text(), "ORIGINAL")
                self.assertIn(dest, (self.art / "oracle-prep-error.txt").read_text())


class RecordTests(RunFixture):
    def test_target_symlink_dest_is_refused_and_recorded(self):
        self.common_config()
        self.denied = self.exp / "denied"
        self.denied.mkdir()
        self.victim = self.denied / "victim.txt"
        self.victim.write_text("ORIGINAL")
        workdir_record = self.exp / "workdir.txt"
        stub = self.exp / "target_stub.py"
        stub.write_text(ORACLE_LINK_TARGET)
        self.cfg["target_argv"] = [sys.executable, "-I", "-B", str(stub), str(workdir_record), str(self.victim)]
        self.freeze()
        phe.run(self.exp)
        self.assertEqual(
            json.loads((self.exp / "stop.json").read_text())["stop_reason"], "pivot:runner_crashed_no_record"
        )
        self.assertEqual(self.victim.read_text(), "ORIGINAL")
        error = (self.exp / "artifacts" / "slot-01" / "oracle-prep-error.txt").read_text()
        self.assertIn("oracle 目的路径逃出验收副本", error)
        shutil.rmtree(Path(workdir_record.read_text()), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
