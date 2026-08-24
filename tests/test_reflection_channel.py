import unittest

from substrate.reflection import build_reflection


class ReflectionTests(unittest.TestCase):
    def test_zero_delta_requires_primary_probe_change(self):
        result = build_reflection({"recent_attempts": [
            {"soil_cycle": 35, "reason": "delta_below_threshold",
             "delta": 0.0, "attempt_id": "att-1"}
        ], "source_attempt_ids": ["att-1"]})
        self.assertEqual(result["hypothesis"], "candidate_is_valid_but_primary_probe_delta_is_zero")
        self.assertIn("primary-probe improvement", result["next_strategy"])

        result = build_reflection({"recent_attempts": [
            {"soil_cycle": 34, "reason": "syntax_failure", "attempt_id": "att-1"}
        ], "source_attempt_ids": ["att-1"], "source_ledger_tail_hash": "hash"})
        self.assertEqual(result["hypothesis"], "candidate_syntax_failure_prevented_measurement")
        self.assertIn("valid Python", result["next_strategy"])

        result = build_reflection(
            {"task_id": "t1", "recent_attempts": [
                {"soil_cycle": 1, "outcome": "UNFULFILLED", "delta": 0.0,
                 "diagnosis_class": "repeated_strategy_no_effect"}
            ], "strategy_memory": {"s1": {"repeated_failure": True}}})
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["source_cycles"])
        self.assertNotIn("prompt", result)

    def test_preflight_improvement_takes_reflection_priority(self):
        result = build_reflection({"recent_attempts": [
            {"soil_cycle": 43, "reason": "preflight_rejected_after_improvement",
             "delta": 20.0, "attempt_id": "att-43"}
        ], "source_attempt_ids": ["att-43"]})
        self.assertEqual(result["hypothesis"], "candidate_meets_primary_and_holdout_but_autonomous_promotion_is_gated")
        self.assertIn("autonomous H1", result["next_strategy"])

        result = build_reflection({})
        self.assertEqual(result["hypothesis"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
