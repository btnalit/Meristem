import json
import tempfile
import unittest
from pathlib import Path

from substrate import pipeline
from substrate.supervisor import _preflight_has_pending_promotion, _preflight_panel


class PreflightPanelTests(unittest.TestCase):
    def test_preflight_panel_can_never_authorize_promotion(self):
        verdict = _preflight_panel("candidate", "diff", object())
        self.assertIsInstance(verdict, pipeline.Verdict)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.authority, "manual")
        self.assertIn("promotion disabled", verdict.reason)
    def test_preflight_does_not_resolve_same_attempt_different_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            rows = [
                {"kind": "promotion_intent", "source": "obs-1",
                 "attempt_id": "att-1", "commit": "candidate"},
                {"kind": "accepted_fitness", "source": "obs-2",
                 "attempt_id": "att-1", "commit": "candidate"},
            ]
            (repo / "state" / "soil-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows))
            self.assertTrue(_preflight_has_pending_promotion(repo))

    def test_preflight_refuses_pending_promotion_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            (repo / "state" / "soil-ledger.jsonl").write_text(
                json.dumps({"kind": "promotion_intent", "event_id": "intent-1"}) + "\n")
            self.assertTrue(_preflight_has_pending_promotion(repo))
            with (repo / "state" / "soil-ledger.jsonl").open("a") as handle:
                handle.write(json.dumps({"kind": "promotion_outcome", "source": "intent-1"}) + "\n")
            (repo / "state" / "soil-ledger.jsonl").write_text(
                json.dumps({"kind": "promotion_intent", "source": "obs-1",
                            "attempt_id": "att-1", "commit": "candidate"}) + "\n")
            self.assertTrue(_preflight_has_pending_promotion(repo))
            with (repo / "state" / "soil-ledger.jsonl").open("a") as handle:
                handle.write(json.dumps({"kind": "promotion_outcome", "source": "obs-1",
                                         "attempt_id": "att-1", "outcome": "ABANDONED"}) + "\n")
            self.assertFalse(_preflight_has_pending_promotion(repo))


if __name__ == "__main__":
    unittest.main()
