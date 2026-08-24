import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from substrate.feedback_projection import write_projection


class FeedbackProjectionTests(unittest.TestCase):
    def test_projection_is_bounded_and_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            (repo / "seed").mkdir()
            rows = [
                {"kind": "cycle", "soil_cycle": 1},
                {"kind": "observed_fitness", "event_id": "obs-1",
                 "task_id": "task-1", "soil_cycle": 1,
                 "commit": "abcdef1234567890", "primary_probe": "probe",
                 "records": [{"probe_id": "probe", "before": 40.0,
                              "after": 40.0, "delta": 0.0, "status": "no_regression"}]},
                {"kind": "promotion_outcome", "source": "obs-1",
                 "outcome": "UNFULFILLED", "why": "delta 0 < minimum_delta 20"},
                {"kind": "cycle", "task_id": "task-1", "soil_cycle": 2,
                 "commit": None, "exit_code": 1, "failure_reason": "path_violation"},
            ]
            ledger = repo / "state" / "soil-ledger.jsonl"
            ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            target = write_projection(repo, task_id="task-1")
            doc = json.loads(target.read_text())
            self.assertEqual(doc["source_ledger_tail_hash"],
                             hashlib.sha256(ledger.read_bytes()).hexdigest())
            self.assertEqual(doc["facts"]["last_attempt"]["outcome"], "NO_CANDIDATE")
            self.assertEqual(doc["facts"]["last_attempt"]["reason"], "path_violation")
            self.assertNotIn("records", json.dumps(doc))
            self.assertNotIn("prompt", json.dumps(doc))
    def test_projection_derives_task_state_and_strategy_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            (repo / "seed").mkdir()
            rows = []
            for cycle in (1, 2, 3):
                attempt = f"att-{cycle:032x}"
                commit = f"commit-{cycle}"
                rows.extend([
                    {"kind": "cycle", "task_id": "task-1", "soil_cycle": cycle,
                     "attempt_id": attempt, "commit": commit, "exit_code": 0,
                     "changed_paths": ["body/organs/classifier/run.py"],
                     "strategy_fingerprint": "strat-same"},
                    {"kind": "observed_fitness", "event_id": f"obs-{cycle}",
                     "task_id": "task-1", "attempt_id": attempt,
                     "soil_cycle": cycle, "commit": commit,
                     "primary_probe": "probe", "records": [{"probe_id": "probe",
                     "before": 40.0, "after": 40.0, "delta": 0.0,
                     "status": "no_regression"}]},
                    {"kind": "promotion_outcome", "source": f"obs-{cycle}",
                     "attempt_id": attempt, "outcome": "UNFULFILLED",
                     "why": "delta 0", "counts_against_task_quota": True},
                ])
            ledger = repo / "state" / "soil-ledger.jsonl"
            ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            doc = json.loads(write_projection(repo, task_id="task-1").read_text())
            self.assertEqual(doc["facts"]["task_states"]["task-1"]["state"], "parked")
            self.assertTrue(doc["facts"]["strategy_memory"]["strat-same"]["repeated_failure"])


if __name__ == "__main__":
    unittest.main()
