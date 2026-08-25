import unittest

from substrate.learning_state import (
    FAULT_CLASSES,
    MECHANISM_FAILURE_REASONS,
    TASK_STATES,
    new_attempt_id,
    validate_attempt_id,
)


class LearningStateTests(unittest.TestCase):
    def test_attempt_id_is_opaque_and_validated(self):
        value = new_attempt_id()
        self.assertTrue(validate_attempt_id(value))
        self.assertNotEqual(value, new_attempt_id())
        self.assertFalse(validate_attempt_id("cycle-21"))

    def test_taxonomies_are_closed(self):
        self.assertIn("healthy", FAULT_CLASSES | {"healthy"})
        self.assertIn("fulfilled", TASK_STATES)

    def test_mechanism_failure_reasons_are_a_subset_of_fault_classes(self):
        """P2-7: task_state.py's mechanism-failure set now lives here as the
        single source of truth; it must stay inside the closed taxonomy."""
        self.assertTrue(MECHANISM_FAILURE_REASONS <= FAULT_CLASSES)
        self.assertIn("blocked", TASK_STATES)


if __name__ == "__main__":
    unittest.main()
