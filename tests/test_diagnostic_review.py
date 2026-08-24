import unittest

from substrate.diagnostic_review import diagnose_failure


class DiagnosticReviewTests(unittest.TestCase):
    def test_path_violation_is_contract_failure(self):
        result = diagnose_failure(failure_class="path_violation", changed_paths=[])
        self.assertEqual(result["diagnosis_class"], "mutation_contract_failure")
        self.assertFalse(result["promotion_authority"])

    def test_repeated_broad_strategy_is_distinguished(self):
        result = diagnose_failure(
            failure_class="unfulfilled",
            changed_paths=["tests/a.py", "body/organs/classifier/run.py"],
            repeated_strategy=True,
            delta=0.0,
        )
        self.assertEqual(result["diagnosis_class"], "repeated_strategy_no_effect")
        self.assertIn("strategy", result["next_experiment_constraint"])

    def test_repeated_zero_delta_takes_precedence(self):
        result = diagnose_failure(
            failure_class="delta_below_threshold",
            changed_paths=["body/organs/classifier/run.py"],
            repeated_strategy=True,
            delta=0.0,
        )
        self.assertEqual(result["diagnosis_class"], "repeated_strategy_no_effect")
        self.assertEqual(result["mechanism_status"], "healthy")
        result = diagnose_failure(
            failure_class="delta_below_threshold",
            changed_paths=["body/organs/classifier/run.py"])
        self.assertEqual(result["mechanism_status"], "healthy")
        self.assertIn("classifier decision rule", result["next_experiment_constraint"])

    def test_mechanism_failure_is_not_model_failure(self):
        result = diagnose_failure(failure_class="provider_error", changed_paths=[])
        self.assertEqual(result["mechanism_status"], "unhealthy")
        self.assertEqual(result["diagnosis_class"], "mechanism_failure")


if __name__ == "__main__":
    unittest.main()
