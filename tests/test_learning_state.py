import unittest

from substrate.learning_state import (
    FAULT_CLASSES,
    TASK_STATES,
    classify_attempt,
    new_attempt_id,
    validate_attempt_id,
)


class LearningStateTests(unittest.TestCase):
    def test_attempt_id_is_opaque_and_validated(self):
        value = new_attempt_id()
        self.assertTrue(validate_attempt_id(value))
        self.assertNotEqual(value, new_attempt_id())
        self.assertFalse(validate_attempt_id("cycle-21"))

    def test_fault_taxonomy_separates_mechanism_and_task(self):
        result = classify_attempt(failure_reason="path_violation", provider_status="allowed", candidate=False)
        self.assertEqual(result["mechanism_status"], "healthy")
        self.assertEqual(result["task_status"], "no_candidate")
        self.assertEqual(result["fault_class"], "path_violation")

    def test_provider_failure_is_mechanism_failure(self):
        result = classify_attempt(failure_reason="provider_error", provider_status="refused", candidate=False)
        self.assertEqual(result["mechanism_status"], "provider_error")
        self.assertEqual(result["task_status"], "blocked")

    def test_taxonomies_are_closed(self):
        self.assertIn("healthy", FAULT_CLASSES | {"healthy"})
        self.assertIn("fulfilled", TASK_STATES)


if __name__ == "__main__":
    unittest.main()
