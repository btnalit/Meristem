import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from substrate.rollback import (
    build_plan, execute_autonomous_rollback, validate_receipt,
    validate_receipt_contract, verify_receipt_state,
)


class RollbackContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_plan(
            task_id="task-new", attempt_id="att-new", from_commit="candidate",
            restore_commit="stable", phase="promoted_bad_candidate",
            reason="probe regression")

    def _receipt(self):
        return {
            "task_id": "task-new", "attempt_id": "att-new", "from_commit": "candidate",
            "phase": "promoted_bad_candidate", "authority": "root_manual",
            "restored_commit": "stable", "generation": "gen-2",
            "soil_cycle": 41, "ledger_tail_hash": "a" * 64,
            "task_state": "open", "status": "rolled_back",
        }

    def test_receipt_requires_identity_and_restored_state(self):
        validate_receipt_contract(self.plan, self._receipt())

    def test_receipt_rejects_wrong_commit(self):
        receipt = self._receipt()
        receipt["restored_commit"] = "candidate"
        with self.assertRaises(ValueError):
            validate_receipt_contract(self.plan, receipt)

    def test_validate_receipt_requires_live_repo(self):
        with self.assertRaises(ValueError):
            validate_receipt(self.plan, self._receipt())

    def test_live_receipt_is_bound_to_head_ledger_generation_and_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in ("root/generations.json", "state/soil-ledger.jsonl",
                        "seed/feedback.json"):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            (repo / "stable.txt").write_text("stable")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "stable"], cwd=repo, check=True)
            stable = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "root/generations.json").write_text(json.dumps({"live": "gen-2"}))
            ledger = repo / "state/soil-ledger.jsonl"
            ledger.write_text("".join(json.dumps(row) + "\n" for row in [
                {"kind": "cycle", "soil_cycle": 41},
                {"kind": "rollback_intent", "task_id": "task-new", "attempt_id": "att-new",
                 "from_commit": "candidate", "restore_commit": stable,
                 "phase": "promoted_bad_candidate", "authority": "root_manual"},
                {"kind": "rollback_committed", "task_id": "task-new", "attempt_id": "att-new",
                 "from_commit": "candidate", "restored_commit": stable,
                 "phase": "promoted_bad_candidate", "authority": "root_manual"},
            ]))
            (repo / "seed/feedback.json").write_text(json.dumps({
                "facts": {"task_states": {"task-new": {"state": "open"}}}
            }))
            plan = build_plan(task_id="task-new", attempt_id="att-new",
                              from_commit="candidate", restore_commit=stable,
                              phase="promoted_bad_candidate", reason="regression")
            receipt = {
                "task_id": "task-new", "attempt_id": "att-new", "from_commit": "candidate",
                "phase": "promoted_bad_candidate", "authority": "root_manual",
                "restored_commit": stable, "generation": "gen-2", "soil_cycle": 41,
                "ledger_tail_hash": hashlib.sha256(ledger.read_bytes()).hexdigest(),
                "task_state": "open", "status": "rolled_back",
            }
            verify_receipt_state(repo, plan, receipt)
            receipt["ledger_tail_hash"] = "b" * 64
            with self.assertRaises(ValueError):
                verify_receipt_state(repo, plan, receipt)

    def test_plan_rejects_unknown_authority(self):
        with self.assertRaises(ValueError):
            from substrate.rollback import RollbackPlan
            RollbackPlan("t", "a", "x", "y", "merged_pre_accept", "x", "seed")

    def test_autonomous_executor_restores_bound_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            (repo / "state.txt").write_text("stable")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "stable"], cwd=repo, check=True)
            stable = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "state.txt").write_text("bad candidate")
            subprocess.run(["git", "commit", "-qam", "bad candidate"], cwd=repo, check=True)
            bad = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            plan = build_plan(task_id="task-new", attempt_id="att-new",
                              from_commit=bad, restore_commit=stable,
                              phase="promoted_bad_candidate", reason="regression",
                              authority="soil-autonomous")
            self.assertEqual(execute_autonomous_rollback(repo, plan), stable)
            self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(), stable)

    def test_autonomous_executor_refuses_moved_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            (repo / "state.txt").write_text("stable")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "stable"], cwd=repo, check=True)
            stable = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "state.txt").write_text("candidate")
            subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
            candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "state.txt").write_text("moved")
            subprocess.run(["git", "commit", "-qam", "moved"], cwd=repo, check=True)
            plan = build_plan(task_id="task-new", attempt_id="att-new",
                              from_commit=candidate, restore_commit=stable,
                              phase="promoted_bad_candidate", reason="regression",
                              authority="soil-autonomous")
            with self.assertRaises(ValueError):
                execute_autonomous_rollback(repo, plan)


if __name__ == "__main__":
    unittest.main()
