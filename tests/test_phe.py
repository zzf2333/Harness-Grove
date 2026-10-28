"""PHE 的合成实验与无模型调度验收。"""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("phe", ROOT / "tools/phe.py")
assert SPEC is not None and SPEC.loader is not None
phe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phe)


def proposal(count=5, controls=True):
    p = json.loads((ROOT / "examples/m-1/proposal.template.json").read_text())
    groups = ["base_left", "base_right", "ablated"]
    removals = [[], [], ["real_rule"]]
    if controls:
        groups += ["necessary", "neutral"]
        removals += [["required_control"], ["redundant_control"]]
    p["harness"]["variants"] = {
        g: {"remove": r, "snapshot_sha256": "unused"} for g, r in zip(groups, removals, strict=True)
    }
    p["experiment"]["groups"] = dict.fromkeys(groups, count)
    p["experiment"]["schedule"] = [
        {"slot": k * len(groups) + i + 1, "block": k + 1, "group": g}
        for k in range(count)
        for i, g in enumerate(groups)
    ]
    p["experiment"]["budget"].update(
        {
            "max_target_invocations": len(groups) * count,
            "max_process_seconds_per_invocation": 10,
            "process_kill_grace_seconds": 1,
            "max_oracle_seconds_per_invocation": 10,
            "max_experiment_wall_seconds": 1000,
            "max_estimated_cost_usd_per_invocation": 1,
            "max_estimated_cost_usd_total": 100,
        }
    )
    p["gates"]["version"] = "v1"
    p["gates"]["go_requires"].update(
        {
            "expected_valid_completed_runs": len(groups) * count,
            "required_telemetry_complete_runs": len(groups) * count,
            "aa_token_median_max_relative_gap": 0.25,
            "aa_pooled_tokens_max_relative_mad": 0.3,
        }
    )
    return p


def runs(p) -> list[dict[str, Any]]:
    return [
        dict(
            item,
            exit_code=0,
            process_timed_out=False,
            **{phe.TOK: 80 if item["group"] == "ablated" else 100},
            estimated_cost_usd_v1=0.1,
            test_pass=True,
            contract_pass=item["group"] != "necessary",
            user_files_unchanged=True,
        )
        for item in p["experiment"]["schedule"]
    ]


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.p = proposal()
        self.rs = runs(self.p)

    def result(self, stop=None):
        return phe.evaluate(self.p, self.rs, stop)

    def test_go_and_follow_up_candidate(self):
        r = self.result()
        self.assertEqual(r["probe_decision"], "go")
        self.assertEqual(r["real_rule_signal"], "follow_up_candidate")
        self.assertTrue(all(r["gates"].values()))

    def test_pivot(self):
        self.assertEqual(self.result("pivot:cost_budget_exhausted")["probe_decision"], "pivot")

    def test_invalid_run_precedes_failed_gates(self):
        self.rs.pop()
        self.assertEqual(self.result("invalid_run:config_hash_changed")["probe_decision"], "invalid_run")

    def test_each_v1_gate_can_fail_alone(self):
        cases = ["完整性", "基线能力", "A/A 质量波动", "A/A Token 波动", "必要信息可识别", "冗余控制不误报", "资源"]
        for gate in cases:
            with self.subTest(gate=gate):
                p, rs = copy.deepcopy(self.p), copy.deepcopy(self.rs)
                stop = None
                if gate == "完整性":
                    rs[2]["exit_code"] = None
                elif gate == "基线能力":
                    p["gates"]["go_requires"]["baseline_task_min_passes_each"] = 6
                elif gate == "A/A 质量波动":
                    p["gates"]["go_requires"]["baseline_task_min_passes_each"] = 3
                    p["gates"]["go_requires"]["redundant_max_count_gap_to_each_baseline"] = 2
                    for r in [r for r in rs if r["group"] == "base_left"][:2]:
                        r["test_pass"] = False
                elif gate == "A/A Token 波动":
                    for r in rs:
                        if r["group"] == "base_left":
                            r[phe.TOK] = 200
                elif gate == "必要信息可识别":
                    for r in rs:
                        if r["group"] == "necessary":
                            r["contract_pass"] = True
                elif gate == "冗余控制不误报":
                    for r in [r for r in rs if r["group"] == "neutral"][:2]:
                        r["test_pass"] = r["contract_pass"] = False
                else:
                    stop = "pivot:wall_budget_exhausted"
                result = phe.evaluate(p, rs, stop)
                self.assertEqual([k for k, v in result["gates"].items() if v is False], [gate])
                self.assertEqual(result["probe_decision"], "pivot")

    def test_v2_redundant_noise_blocks_follow_up(self):
        for r in self.rs:
            if r["group"] == "neutral":
                r[phe.TOK] = 70
        self.assertEqual(self.result()["real_rule_signal"], "follow_up_candidate")
        self.p["gates"]["version"] = "v2"
        r = self.result()
        self.assertAlmostEqual(r["N_cost"], 0.3)
        self.assertEqual(r["probe_decision"], "go")
        self.assertEqual(r["real_rule_signal"], "inconclusive")

    def test_three_groups_and_non_default_count(self):
        p = proposal(count=6, controls=False)
        p["gates"]["version"] = "v2"
        result = phe.evaluate(p, runs(p), None)
        self.assertEqual(result["probe_decision"], "go")
        self.assertEqual(result["gates"]["必要信息可识别"], "not_applicable")
        self.assertEqual(result["gates"]["冗余控制不误报"], "not_applicable")
        self.assertEqual(result["N_cost"], 0)
        self.assertEqual(result["T_A"], 100)
        self.assertEqual(result["runs"], 18)

    def test_regression_signal(self):
        for r in [r for r in self.rs if r["group"] == "ablated"][:2]:
            r["test_pass"] = r["contract_pass"] = False
        self.assertEqual(self.result()["real_rule_signal"], "regression_signal")

    def test_duplicate_slot_and_wrong_group_cannot_complete_schedule(self):
        self.rs[-1] = copy.deepcopy(self.rs[-2])
        self.assertFalse(self.result()["gates"]["完整性"])
        self.assertEqual(self.result()["probe_decision"], "pivot")

    def test_missing_telemetry_pivots_without_zero_cost_noise(self):
        self.rs[0][phe.TOK] = None
        r = self.result()
        self.assertEqual(r["probe_decision"], "pivot")
        self.assertIsNone(r["N_cost"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phe-test-")
        self.addCleanup(self.temp.cleanup)
        self.exp = Path(self.temp.name)
        self.p = proposal()
        self.cfg: dict[str, Any] = {"harness": {}}
        for g, v in self.p["harness"]["variants"].items():
            h = self.exp / (g + ".md")
            h.write_text("synthetic harness")
            self.cfg["harness"][g] = str(h)
            v["snapshot_sha256"] = phe.sha(h)

    def freeze(self):
        (self.exp / "config.json").write_text(json.dumps(self.cfg))
        self.p["runner"] = {"config_sha256": phe.sha(self.exp / "config.json")}
        (self.exp / "proposal.json").write_text(json.dumps(self.p))
        digest = phe.sha(self.exp / "proposal.json")
        (self.exp / "proposal.sha256").write_text(digest)
        (self.exp / "approval.json").write_text(json.dumps({"proposal_sha256": digest}))

    def test_schedule_count_mismatch_refuses_before_target(self):
        for kind in ("group", "budget", "schedule"):
            with self.subTest(kind=kind):
                self.p = proposal()
                if kind == "group":
                    self.p["experiment"]["groups"]["base_left"] += 1
                elif kind == "budget":
                    self.p["experiment"]["budget"]["max_target_invocations"] += 1
                else:
                    self.p["experiment"]["schedule"][-1]["group"] = "base_left"
                self.freeze()
                with patch.object(phe, "run_slot") as target:
                    with self.assertRaisesRegex(ValueError, "schedule|次数"):
                        phe.run(self.exp)
                    target.assert_not_called()

    def test_unapproved_or_changed_proposal_refused(self):
        self.freeze()
        (self.exp / "approval.json").write_text('{"proposal_sha256": "wrong"}')
        with self.assertRaisesRegex(ValueError, "approval"):
            phe.run(self.exp)
        self.freeze()
        with (self.exp / "proposal.json").open("a") as f:
            f.write(" ")
        with self.assertRaisesRegex(ValueError, "proposal.sha256"):
            phe.run(self.exp)

    def test_hash_budget_and_previous_stop_admission(self):
        self.freeze()
        with (self.exp / "config.json").open("a") as f:
            f.write(" ")
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(
            json.loads((self.exp / "stop.json").read_text())["stop_reason"], "invalid_run:config_hash_changed"
        )
        (self.exp / "stop.json").unlink()
        self.p["experiment"]["budget"]["max_estimated_cost_usd_total"] = 0.5
        self.freeze()
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(json.loads((self.exp / "stop.json").read_text())["stop_reason"], "pivot:cost_budget_exhausted")
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()

    def test_resume_integrity_failure_overrides_previous_pivot(self):
        self.freeze()
        (self.exp / "stop.json").write_text(json.dumps({"stop_reason": "pivot:telemetry_missing"}))
        with (self.exp / "config.json").open("a") as f:
            f.write(" ")
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(
            json.loads((self.exp / "stop.json").read_text())["stop_reason"], "invalid_run:config_hash_changed"
        )

    def test_receipt_contract_depends_on_required_rule(self):
        work = self.exp / "oracle-work"
        work.mkdir()
        trusted = self.exp / "trusted.txt"
        trusted.write_text("fixture")
        cfg = {
            "oracle_test_src": str(trusted),
            "oracle_test_dest": "trusted.txt",
            "oracle_argv": ["unused"],
            "oracle_cwd": ".",
            "oracle_timeout_s": 1,
            "process_kill_grace_seconds": 1,
            "nonce_sha256": hashlib.sha256(b"correct").hexdigest(),
        }
        with patch.object(phe, "run_with_timeout", return_value=(0, b"", b"", 0, False)):
            for receipt in (None, "wrong", "correct"):
                if receipt is not None:
                    (work / ".phe-calibration-receipt").write_text(receipt)
                for required in (True, False):
                    with self.subTest(receipt=receipt, required=required):
                        result = phe.oracle(work, cfg, receipt_required=required)
                        self.assertEqual(result["contract_pass"], not required or receipt == "correct")
                        self.assertEqual(result["receipt_present"], receipt is not None)
                        self.assertEqual(result["receipt_match"], receipt == "correct")

    def test_run_stops_for_telemetry_cost_and_user_file_changes(self):
        cases = [
            ({phe.TOK: None}, "pivot:telemetry_missing"),
            ({"estimated_cost_usd_v1": 2}, "pivot:per_invocation_cost_exceeded"),
            ({"user_files_unchanged": False}, "invalid_run:user_files_changed"),
        ]
        for changes, reason in cases:
            with self.subTest(reason=reason):
                self.freeze()
                for filename in ("runs.jsonl", "stop.json"):
                    (self.exp / filename).unlink(missing_ok=True)

                def write_record(exp, cfg, item, budget, changes=changes, **kwargs):
                    record = dict(runs(self.p)[0], **changes)
                    (exp / "runs.jsonl").write_text(json.dumps(record) + "\n")

                with patch.object(phe, "run_slot", side_effect=write_record) as target:
                    phe.run(self.exp)
                    target.assert_called_once()
                self.assertEqual(phe.stop_reason(self.exp), reason)

    def test_wall_budget_and_harness_hash_stop_before_target(self):
        self.p["experiment"]["budget"]["max_experiment_wall_seconds"] = 0
        self.freeze()
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(phe.stop_reason(self.exp), "pivot:wall_budget_exhausted")
        (self.exp / "stop.json").unlink()
        Path(self.cfg["harness"]["base_left"]).write_text("changed")
        with patch.object(phe, "run_slot") as target:
            phe.run(self.exp)
            target.assert_not_called()
        self.assertEqual(phe.stop_reason(self.exp), "invalid_run:harness_hash_changed:base_left")

    def test_fake_target_end_to_end(self):
        base = self.exp / "base"
        base.mkdir()
        subprocess.run(["git", "init", "-q", str(base)], check=True)
        (base / "task.txt").write_text("fixture")
        trusted = self.exp / "trusted.txt"
        trusted.write_text("oracle fixture")
        fake = self.exp / "target.py"
        fake.write_text(
            "import json\nfrom pathlib import Path\n"
            'print(json.dumps({"modelUsage": {"fake": {"inputTokens": 100}}, "total_cost_usd": 0.1}))\n'
        )
        self.cfg.update(
            {
                "base_dir": str(base),
                "experiment_id": "synthetic",
                "experiment_dir": str(self.exp),
                "target_argv": [sys.executable, "-I", str(fake)],
                "env": {},
                "oracle_test_src": str(trusted),
                "oracle_test_dest": "oracle.txt",
                "oracle_cwd": ".",
                "oracle_argv": [sys.executable, "-I", "-c", "print('Tests 1 passed')"],
                "nonce_sha256": hashlib.sha256(b"receipt").hexdigest(),
            }
        )
        self.p = proposal(count=1, controls=False)
        del self.p["rules"]["required_control"]
        for g, v in self.p["harness"]["variants"].items():
            v["snapshot_sha256"] = phe.sha(Path(self.cfg["harness"][g]))
        self.freeze()
        command = [sys.executable, "-I", str(ROOT / "tools/phe.py"), "run", str(self.exp)]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        rs = phe.records(self.exp)
        self.assertEqual(len(rs), 3)
        self.assertTrue(all(r[phe.TOK] == 100 and r["contract_pass"] for r in rs))
        self.assertTrue(all(not r["receipt_present"] and not r["receipt_match"] for r in rs))
        self.assertTrue(all(r["user_files_unchanged"] for r in rs))
        self.assertIsNone(json.loads((self.exp / "stop.json").read_text())["stop_reason"])
        for r in rs:
            import shutil

            shutil.rmtree(Path(r["workdir"]).parent)


if __name__ == "__main__":
    unittest.main()
