"""Runner 异常退出时的完整性优先级：proposal/config 被改写或删除必须判 invalid_run。"""

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_phe

phe = test_phe.phe


class CrashIntegrityTests(unittest.TestCase):
    # 由复用的 RunTests.setUp 设置。
    exp: Path
    p: dict[str, Any]
    cfg: dict[str, Any]

    setUp = test_phe.RunTests.setUp
    freeze = test_phe.RunTests.freeze

    def test_runner_crash_after_proposal_or_config_tamper_is_invalid_run(self):
        cases = (
            ("proposal-rewrite", "proposal.json", False, "invalid_run:proposal_hash_changed"),
            ("proposal-delete", "proposal.json", True, "invalid_run:proposal_hash_changed"),
            ("config-delete", "config.json", True, "invalid_run:config_hash_changed"),
        )
        for name, target_file, delete, expected in cases:
            for exit_path in ("raise", "no_record"):
                with self.subTest(case=name, exit=exit_path):
                    self.freeze()
                    (self.exp / "stop.json").unlink(missing_ok=True)
                    path = self.exp / target_file

                    def tamper(*_args, path=path, delete=delete, exit_path=exit_path, **_kwargs):
                        if delete:
                            path.unlink()
                        else:
                            with path.open("a") as f:
                                f.write(" ")
                        if exit_path == "raise":
                            raise OSError("simulated runner failure")

                    with patch.object(phe, "run_slot", side_effect=tamper):
                        phe.run(self.exp)
                    self.assertEqual(json.loads((self.exp / "stop.json").read_text())["stop_reason"], expected)


if __name__ == "__main__":
    unittest.main()
