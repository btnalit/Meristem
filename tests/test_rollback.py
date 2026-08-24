import unittest

from substrate.rollback import build_plan, validate_receipt


class RollbackContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_plan(
            task_id="task-new", attempt_id="att-new", from_commit="candidate",
            restore_commit="stable", phase="promoted_bad_candidate",
            reason="probe regression")

    def test_receipt_requires_identity_and_restored_state(self):
        receipt = {
            "task_id": "task-new", "attempt_id": "att-new",
            "restored_commit": "stable", "generation": "gen-2",
            "soil_cycle": 41, "ledger_tail_hash": "abc123",
            "task_state": "open", "status": "rolled_back",
        }
        validate_receipt(self.plan, receipt)

    def test_receipt_rejects_wrong_commit(self):
        receipt = {
            "task_id": "task-new", "attempt_id": "att-new",
            "restored_commit": "candidate", "generation": "gen-2",
            "soil_cycle": 41, "ledger_tail_hash": "abc123",
            "task_state": "open", "status": "rolled_back",
        }
        with self.assertRaises(ValueError):
            validate_receipt(self.plan, receipt)

    def test_plan_rejects_non_manual_authority(self):
        with self.assertRaises(ValueError):
            from substrate.rollback import RollbackPlan
            RollbackPlan("t", "a", "x", "y", "merged_pre_accept", "x", "seed")


if __name__ == "__main__":
    unittest.main()
