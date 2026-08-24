import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from substrate import pipeline
from substrate.supervisor import (
    _preflight_has_pending_promotion, _preflight_panel, _manifest_recovery_window,
    autonomous_panel,
)


class PreflightPanelTests(unittest.TestCase):
    def test_preflight_panel_can_never_authorize_promotion(self):
        verdict = _preflight_panel("candidate", "diff", object())
        self.assertIsInstance(verdict, pipeline.Verdict)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.authority, "manual")
        self.assertIn("promotion disabled", verdict.reason)
    def test_autonomous_panel_has_no_human_authority(self):
        task = pipeline.Task(
            task_id="task", kind="repair", target="classifier",
            primary_probe="probe-classify-basic", required_target_paths=("body/organs/classifier/",),
            forbidden_paths=("tests/",))
        verdict = autonomous_panel(
            "candidate", "diff --git a/body/organs/classifier/run.py b/body/organs/classifier/run.py\n",
            task)
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.authority, "soil-autonomous")
        rejected = autonomous_panel(
            "candidate", "diff --git a/tests/x.py b/tests/x.py\n", task)
        self.assertFalse(rejected.passed)
        self.assertEqual(rejected.authority, "soil-autonomous")

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

    def test_preflight_does_not_resolve_same_source_wrong_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            rows = [
                {"kind": "promotion_intent", "source": "obs-1",
                 "attempt_id": "att-1", "commit": "candidate-1"},
                {"kind": "accepted_fitness", "source": "obs-1",
                 "attempt_id": "att-1", "commit": "candidate-2"},
            ]
            (repo / "state" / "soil-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows))
            self.assertTrue(_preflight_has_pending_promotion(repo))

    def test_preflight_blocks_accepted_before_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            rows = [
                {"kind": "promotion_intent", "source": "obs-1",
                 "attempt_id": "att-1", "commit": "candidate"},
                {"kind": "accepted_fitness", "source": "obs-1",
                 "attempt_id": "att-1", "commit": "candidate"},
            ]
            (repo / "state" / "soil-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows))
            self.assertTrue(_preflight_has_pending_promotion(repo))

    def test_manifest_recovery_window_requires_only_ledger_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in ("soil/runtime-manifest.json", "state/soil-ledger.jsonl",
                        "seed/feedback.json", "soil/report-facts.json",
                        "soil/frozen-probe-registry.json"):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text("{}\n")
            ledger = repo / "state/soil-ledger.jsonl"
            ledger.write_text(json.dumps({"kind": "promotion_intent", "source": "obs",
                                          "attempt_id": "att", "commit": "c"}) + "\n")
            sha = lambda p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
            manifest = {"ledger_tail_hash": "0" * 64, "projection_hashes": {
                "seed_feedback": sha("seed/feedback.json"),
                "report_facts": sha("soil/report-facts.json"),
                "frozen_probe_registry": sha("soil/frozen-probe-registry.json")}}
            (repo / "soil/runtime-manifest.json").write_text(json.dumps(manifest))
            self.assertTrue(_manifest_recovery_window(repo))
            (repo / "soil/report-facts.json").write_text("tampered\n")
            self.assertFalse(_manifest_recovery_window(repo))

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
                                         "attempt_id": "att-1", "commit": "candidate",
                                         "outcome": "ABANDONED"}) + "\n")
            self.assertFalse(_preflight_has_pending_promotion(repo))


if __name__ == "__main__":
    unittest.main()
