#!/usr/bin/env python3
"""Cross-layer contract tests. Lock the invariants that P-029/P-029b fixed."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from meristem import append_jsonl, read_jsonl  # noqa: E402
from meristem import ledger, llm  # noqa: E402
from meristem.loop import run_cycle, CycleResult  # noqa: E402


class TestEngineFaultRecordsReason(unittest.TestCase):
    """P-029b: when propose/apply/git raises inside run_cycle, the journal
    cycle entry must have a non-empty reason so failure_history() can feed
    it back to the next retry."""

    def test_engine_exception_yields_nonempty_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            with patch("meristem.loop.JOURNAL", journal), \
                 patch("meristem.loop.golden_fixtures", return_value=[]), \
                 patch("meristem.loop.make_worktree", return_value=("b", pathlib.Path(tmp))), \
                 patch("meristem.loop.engine_mod") as mock_engine, \
                 patch("meristem.loop.llm_mod") as mock_llm, \
                 patch("meristem.loop.drop_worktree"):
                mock_llm.attempts_log = []
                mock_llm.load_models.return_value = {}
                mock_engine.propose.side_effect = RuntimeError("model timeout")
                result = run_cycle("test-task", cycle=9999)
            self.assertEqual(result.outcome, "rejected")
            self.assertIn("model timeout", result.reason)
            rows = read_jsonl(journal)
            cycle_rows = [r for r in rows if r.get("kind") == "cycle"]
            self.assertTrue(len(cycle_rows) >= 1)
            self.assertTrue(cycle_rows[-1].get("reason"), "journal reason must not be empty")


class TestDrainDeduplicatesBilling(unittest.TestCase):
    """P-029: one completion must produce exactly one usage row after drain."""

    def test_single_completion_single_usage_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            fake_completion = types.SimpleNamespace(
                slot="mutate:glm", model="glm-5.2",
                prompt_tokens=100, completion_tokens=50,
                reasoning_tokens=0,
            )
            llm.attempts_log.clear()
            llm.attempts_log.append({
                "role": "mutate", "completion": fake_completion, "ok": True,
            })
            with patch("meristem.ledger.JOURNAL", journal):
                cost = ledger.drain_attempts(cycle=9999, models={})
            rows = read_jsonl(journal)
            usage_rows = [r for r in rows if r.get("kind") == "usage" and r.get("cycle") == 9999]
            self.assertEqual(len(usage_rows), 1, "exactly one usage row per drain")
            self.assertEqual(len(llm.attempts_log), 0, "attempts_log cleared after drain")


class TestPressureReflectedToday(unittest.TestCase):
    """P-029 fix #5: _pressure_reflected_today must detect a kernel-written
    reflect --pressure record (which uses kernel append_jsonl, not substrate
    _journal). The ts field must be present and UTC-comparable."""

    def test_kernel_reflect_pressure_detected(self):
        import datetime
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            append_jsonl(journal, {
                "kind": "cycle", "cycle": 999,
                "outcome": "reflected",
                "why": "reflect --pressure",
                "reason": "reflection, not a mutation",
            })
            rows = read_jsonl(journal)
            self.assertIn("ts", rows[0], "kernel append_jsonl must add ts field")
            today_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            self.assertTrue(rows[0]["ts"].startswith(today_utc),
                            "ts must be UTC and match today's UTC date")
            orig = sv.JOURNAL
            try:
                sv.JOURNAL = journal
                self.assertTrue(sv._pressure_reflected_today())
            finally:
                sv.JOURNAL = orig


if __name__ == "__main__":
    unittest.main()
