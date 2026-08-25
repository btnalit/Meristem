import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from substrate.rollback import (
    build_plan, execute_autonomous_rollback, find_dangling_rollback_intents,
    validate_receipt, validate_receipt_contract, verify_receipt_state,
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
                 "phase": "promoted_bad_candidate", "authority": "root_manual",
                 "generation": "gen-2", "soil_cycle": 41},
                {"kind": "rollback_committed", "task_id": "task-new", "attempt_id": "att-new",
                 "from_commit": "candidate", "restored_commit": stable,
                 "phase": "promoted_bad_candidate", "authority": "root_manual",
                 "generation": "gen-2", "soil_cycle": 41},
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
            self.assertEqual(
                execute_autonomous_rollback(repo, plan, generation="gen-2", soil_cycle=41),
                stable)
            self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(), stable)
            rows = [json.loads(line) for line in
                    (repo / "state/soil-ledger.jsonl").read_text().splitlines() if line.strip()]
            self.assertEqual([row["kind"] for row in rows],
                             ["rollback_intent", "rollback_committed"])
            for row in rows:
                self.assertEqual(row["generation"], "gen-2")
                self.assertEqual(row["soil_cycle"], 41)
                self.assertIn("ts", row)
                self.assertIn("event_id", row)
            self.assertEqual(find_dangling_rollback_intents(repo), [])

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
                execute_autonomous_rollback(repo, plan, generation="gen-2", soil_cycle=41)


class DanglingRollbackIntentTests(unittest.TestCase):
    """P1-3: a `rollback_intent` with no matching `rollback_committed` means
    the process died between the `git reset --hard` and the receipt being
    written -- the repository's state relative to the ledger is then
    ambiguous until root_manual reconciliation (docs/MERISTEM-LAYER0-ROLLBACK.md)."""

    def test_no_ledger_reports_nothing_dangling(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_dangling_rollback_intents(Path(tmp)), [])

    def test_simulated_crash_between_intent_and_committed_is_detected_then_clears(self):
        from substrate.soil_state import Ledger

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ledger = Ledger(repo / "state" / "soil-ledger.jsonl")
            # Mirrors exactly what execute_autonomous_rollback's first
            # ledger.append() writes -- simulating a crash right after it,
            # before `git reset --hard` even runs (the widest possible
            # dangling window).
            ledger.append({"kind": "rollback_intent", "task_id": "task-x",
                           "attempt_id": "att-x", "from_commit": "bad",
                           "restore_commit": "stable", "phase": "promoted_bad_candidate",
                           "authority": "soil-autonomous", "generation": "gen-0",
                           "soil_cycle": 5})

            dangling = find_dangling_rollback_intents(repo)
            self.assertEqual(len(dangling), 1)
            self.assertEqual(dangling[0]["task_id"], "task-x")
            self.assertEqual(dangling[0]["attempt_id"], "att-x")

            # Recovery: the missing rollback_committed is appended (the
            # root_manual reconciliation path the refusal message names).
            ledger.append({"kind": "rollback_committed", "task_id": "task-x",
                           "attempt_id": "att-x", "from_commit": "bad",
                           "restored_commit": "stable", "phase": "promoted_bad_candidate",
                           "authority": "soil-autonomous", "generation": "gen-0",
                           "soil_cycle": 5})
            self.assertEqual(find_dangling_rollback_intents(repo), [])

    def test_unrelated_intent_does_not_mask_a_dangling_one(self):
        from substrate.soil_state import Ledger

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ledger = Ledger(repo / "state" / "soil-ledger.jsonl")
            ledger.append({"kind": "rollback_intent", "task_id": "task-resolved",
                           "attempt_id": "att-1", "from_commit": "bad1",
                           "restore_commit": "stable1", "phase": "candidate_unmerged",
                           "authority": "soil-autonomous", "generation": "gen-0",
                           "soil_cycle": 1})
            ledger.append({"kind": "rollback_committed", "task_id": "task-resolved",
                           "attempt_id": "att-1", "from_commit": "bad1",
                           "restored_commit": "stable1", "phase": "candidate_unmerged",
                           "authority": "soil-autonomous", "generation": "gen-0",
                           "soil_cycle": 1})
            ledger.append({"kind": "rollback_intent", "task_id": "task-dangling",
                           "attempt_id": "att-2", "from_commit": "bad2",
                           "restore_commit": "stable2", "phase": "candidate_unmerged",
                           "authority": "soil-autonomous", "generation": "gen-0",
                           "soil_cycle": 2})
            dangling = find_dangling_rollback_intents(repo)
            self.assertEqual([d["task_id"] for d in dangling], ["task-dangling"])


if __name__ == "__main__":
    unittest.main()
