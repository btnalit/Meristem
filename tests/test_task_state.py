import unittest

from substrate.task_state import derive_task_states


class TaskStateTests(unittest.TestCase):
    def test_semantic_failures_park_after_threshold(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 0,
             "commit": "c1"},
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "UNFULFILLED",
             "attempt_id": "a1", "counts_against_task_quota": True},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 0,
             "commit": "c2"},
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "UNFULFILLED",
             "attempt_id": "a2", "counts_against_task_quota": True},
        ]
        states = derive_task_states(rows, threshold=2)
        self.assertEqual(states["t1"]["state"], "parked")
        self.assertEqual(states["t1"]["semantic_failures"], 2)

    def test_mechanism_failure_does_not_park(self):
        rows = [{"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
                 "failure_reason": "provider_error"}]
        states = derive_task_states(rows, threshold=1)
        self.assertEqual(states["t1"]["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
