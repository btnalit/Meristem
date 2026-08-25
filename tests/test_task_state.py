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

    def test_panel_rejection_counts_as_semantic_failure(self):
        rows = [{"kind": "cycle", "task_id": "t1", "attempt_id": "a1"},
                {"kind": "promotion_outcome", "task_id": "t1", "outcome": "REJECTED",
                 "attempt_id": "a1", "counts_against_task_quota": True}]
        states = derive_task_states(rows, threshold=2)
        self.assertEqual(states["t1"]["semantic_failures"], 1)
        self.assertEqual(states["t1"]["state"], "unfulfilled")

    def test_preflight_gate_is_not_semantic_failure(self):
        rows = [{"kind": "cycle", "task_id": "t1", "attempt_id": "a1"},
                {"kind": "promotion_outcome", "task_id": "t1", "outcome": "PREFLIGHT_GATED",
                 "attempt_id": "a1", "counts_against_task_quota": False}]
        states = derive_task_states(rows, threshold=1)
        self.assertEqual(states["t1"]["semantic_failures"], 0)
        self.assertEqual(states["t1"]["promotion_gated_attempts"], 1)
        self.assertEqual(states["t1"]["state"], "promotion_gated")

        rows = [{"kind": "candidate_preflight", "task_id": "t1",
                 "attempt_id": "a1", "failure_reason": "syntax_failure"}]
        states = derive_task_states(rows, threshold=2)
        self.assertEqual(states["t1"]["semantic_failures"], 1)
        self.assertEqual(states["t1"]["mechanism_failures"], 0)
        self.assertEqual(states["t1"]["state"], "unfulfilled")

    def test_mechanism_failure_does_not_park(self):
        rows = [{"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
                 "failure_reason": "provider_error"}]
        states = derive_task_states(rows, threshold=1)
        self.assertEqual(states["t1"]["state"], "blocked")

    def test_blocked_decays_to_open_on_next_healthy_cycle(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
             "failure_reason": "provider_error"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 0,
             "commit": "c2"},
        ]
        states = derive_task_states(rows, threshold=3)
        self.assertEqual(states["t1"]["state"], "open")
        self.assertEqual(states["t1"]["mechanism_failures"], 1)

    def test_blocked_decays_to_unfulfilled_when_semantic_failures_already_present(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 0,
             "commit": "c1"},
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "UNFULFILLED",
             "attempt_id": "a1", "counts_against_task_quota": True},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 1,
             "failure_reason": "gateway_error"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a3", "exit_code": 0,
             "commit": "c3"},
        ]
        states = derive_task_states(rows, threshold=3)
        self.assertEqual(states["t1"]["state"], "unfulfilled")
        self.assertEqual(states["t1"]["semantic_failures"], 1)
        self.assertEqual(states["t1"]["mechanism_failures"], 1)

    def test_normal_quota_path_resumes_after_recovery_and_still_parks(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
             "failure_reason": "rate_limited"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 0,
             "commit": "c2"},
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "UNFULFILLED",
             "attempt_id": "a2", "counts_against_task_quota": True},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a3", "exit_code": 0,
             "commit": "c3"},
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "UNFULFILLED",
             "attempt_id": "a3", "counts_against_task_quota": True},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a4", "exit_code": 0,
             "commit": "c4"},
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "UNFULFILLED",
             "attempt_id": "a4", "counts_against_task_quota": True},
        ]
        states = derive_task_states(rows, threshold=3)
        self.assertEqual(states["t1"]["state"], "parked")
        self.assertEqual(states["t1"]["semantic_failures"], 3)
        self.assertEqual(states["t1"]["mechanism_failures"], 1)

    def test_path_violation_parks_at_threshold_without_touching_semantic_failures(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
             "failure_reason": "path_violation"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 1,
             "failure_reason": "path_violation"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a3", "exit_code": 1,
             "failure_reason": "path_violation"},
        ]
        states = derive_task_states(rows, threshold=3)
        self.assertEqual(states["t1"]["state"], "parked")
        self.assertEqual(states["t1"]["contract_failures"], 3)
        self.assertEqual(states["t1"]["semantic_failures"], 0)
        self.assertEqual(states["t1"]["mechanism_failures"], 0)

    def test_path_violation_below_threshold_then_healthy_cycle_not_parked(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
             "failure_reason": "path_violation"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 1,
             "failure_reason": "path_violation"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a3", "exit_code": 0,
             "commit": "c3"},
        ]
        states = derive_task_states(rows, threshold=3)
        self.assertEqual(states["t1"]["state"], "unfulfilled")
        self.assertNotEqual(states["t1"]["state"], "parked")
        self.assertEqual(states["t1"]["contract_failures"], 2)

    def test_propose_failed_never_increments_any_quota_counter(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a1", "exit_code": 1,
             "failure_reason": "propose_failed"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a2", "exit_code": 1,
             "failure_reason": "propose_failed"},
            {"kind": "cycle", "task_id": "t1", "attempt_id": "a3", "exit_code": 1,
             "failure_reason": "propose_failed"},
        ]
        states = derive_task_states(rows, threshold=3)
        self.assertEqual(states["t1"]["semantic_failures"], 0)
        self.assertEqual(states["t1"]["mechanism_failures"], 0)
        self.assertEqual(states["t1"]["contract_failures"], 0)
        self.assertEqual(states["t1"]["state"], "open")

    def test_guard_set_does_not_contain_blocked(self):
        # substrate.supervisor imports pwd/grp at module level and is
        # POSIX-only (see the suite's other soil-process tests); skip rather
        # than fail on a platform without them.
        try:
            from substrate.supervisor import _GUARDED_TASK_STATES
        except ModuleNotFoundError as exc:
            self.skipTest(f"substrate.supervisor unavailable on this platform: {exc}")
        self.assertNotIn("blocked", _GUARDED_TASK_STATES)
        self.assertEqual(_GUARDED_TASK_STATES, {"parked", "fulfilled", "promotion_gated"})


if __name__ == "__main__":
    unittest.main()
