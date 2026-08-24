import unittest

from substrate.strategy_memory import strategy_fingerprint, summarize_strategies


class StrategyMemoryTests(unittest.TestCase):
    def test_fingerprint_is_stable_for_path_order(self):
        self.assertEqual(
            strategy_fingerprint(["tests/a.py", "body/organs/classifier/run.py"]),
            strategy_fingerprint(["body/organs/classifier/run.py", "tests/a.py"]),
        )

    def test_fingerprint_distinguishes_diff_shape(self):
        paths = ["tests/a.py", "body/organs/classifier/run.py"]
        first = {"files": 2, "families": [{"family": "body/organs/classifier", "files": 1, "added": 4, "deleted": 1}], "patch_sha256": "a"}
        second = {"files": 2, "families": [{"family": "body/organs/classifier", "files": 1, "added": 9, "deleted": 2}], "patch_sha256": "b"}
        self.assertNotEqual(strategy_fingerprint(paths, first), strategy_fingerprint(paths, second))

        rows = [
            {"task_id": "t1", "strategy_fingerprint": "s1", "delta": 0.0,
             "outcome": "UNFULFILLED"},
            {"task_id": "t1", "strategy_fingerprint": "s1", "delta": 0.0,
             "outcome": "UNFULFILLED"},
            {"task_id": "t1", "strategy_fingerprint": "s2", "delta": 20.0,
             "outcome": "FULFILLED"},
        ]
        result = summarize_strategies(rows, task_id="t1")
        self.assertEqual(result["s1"]["attempts"], 2)
        self.assertTrue(result["s1"]["repeated_failure"])
        self.assertEqual(result["s2"]["best_delta"], 20.0)

    def test_summary_does_not_include_mutation_body(self):
        result = summarize_strategies(
            [{"task_id": "t1", "strategy_fingerprint": "s1", "delta": 0.0,
              "response": "secret body"}], task_id="t1")
        self.assertNotIn("response", result["s1"])


if __name__ == "__main__":
    unittest.main()
