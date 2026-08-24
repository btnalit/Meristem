import unittest

from substrate.reflection import build_reflection


class ReflectionTests(unittest.TestCase):
    def test_reflection_labels_hypothesis_and_sources(self):
        result = build_reflection(
            {"task_id": "t1", "recent_attempts": [
                {"soil_cycle": 1, "outcome": "UNFULFILLED", "delta": 0.0,
                 "diagnosis_class": "repeated_strategy_no_effect"}
            ], "strategy_memory": {"s1": {"repeated_failure": True}}})
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["source_cycles"])
        self.assertNotIn("prompt", result)

    def test_empty_facts_are_explicit(self):
        result = build_reflection({})
        self.assertEqual(result["hypothesis"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
