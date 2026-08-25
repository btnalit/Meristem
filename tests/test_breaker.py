"""Tests for the loop-level futility breaker (P0-c, substrate/breaker.py)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from substrate import breaker


class FutileStreakTests(unittest.TestCase):
    def test_empty_ledger_returns_zero(self):
        self.assertEqual(breaker.futile_streak([]), 0)

    def test_trailing_consecutive_futile_counts_from_tail(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "propose_failed"},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "worker_error"},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "task_guarded"},
        ]
        self.assertEqual(breaker.futile_streak(rows), 3)

    def test_candidate_resets_streak_even_if_later_rejected(self):
        rows = [
            # Historical futility deeper in the ledger must never count.
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "propose_failed"},
            # A candidate was produced ...
            {"kind": "cycle", "task_id": "t1", "commit": "c1"},
            # ... and later rejected. Rejection is task_state.py's quota
            # concern, not the breaker's -- the streak still stops here.
            {"kind": "promotion_outcome", "task_id": "t1", "outcome": "REJECTED",
             "commit": "c1", "counts_against_task_quota": True},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "worker_error"},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "task_guarded"},
        ]
        self.assertEqual(breaker.futile_streak(rows), 2)

    def test_ignores_rows_without_task_id(self):
        rows = [
            # The soil's own "a beat happened" bookkeeping row (written
            # before task/candidate are known) must not count or reset.
            {"kind": "cycle", "task_id": None, "commit": None},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "propose_failed"},
            {"kind": "cycle", "task_id": None, "commit": None},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "worker_error"},
        ]
        self.assertEqual(breaker.futile_streak(rows), 2)

    def test_ignores_non_cycle_rows(self):
        rows = [
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "propose_failed"},
            {"kind": "candidate_preflight", "task_id": "t1",
             "failure_reason": "syntax_failure"},
            {"kind": "cycle", "task_id": "t1", "commit": None,
             "failure_reason": "worker_error"},
        ]
        self.assertEqual(breaker.futile_streak(rows), 2)

    def test_live_tail_pattern_fixture(self):
        """Hand-copied fixture mirroring the live ledger tail: a guarded
        beat, a provider deferral, then four real candidates -> 0."""
        rows = [
            {"kind": "cycle", "task_id": "t1", "soil_cycle": 51, "commit": None,
             "failure_reason": "task_guarded"},
            {"kind": "cycle", "task_id": "t1", "soil_cycle": 52, "commit": None,
             "failure_reason": "propose_failed"},
            {"kind": "cycle", "task_id": "t1", "soil_cycle": 53, "commit": "c53"},
            {"kind": "cycle", "task_id": "t1", "soil_cycle": 54, "commit": "c54"},
            {"kind": "cycle", "task_id": "t1", "soil_cycle": 55, "commit": "c55"},
            {"kind": "cycle", "task_id": "t1", "soil_cycle": 56, "commit": "c56"},
        ]
        self.assertEqual(breaker.futile_streak(rows), 0)


class CheckTests(unittest.TestCase):
    def test_missing_ledger_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(breaker.check(Path(tmp)), 0)

    def test_reads_state_soil_ledger_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "state").mkdir()
            rows = [
                {"kind": "cycle", "task_id": "t1", "commit": None,
                 "failure_reason": "propose_failed"},
                {"kind": "cycle", "task_id": "t1", "commit": None,
                 "failure_reason": "worker_error"},
            ]
            (repo / "state" / "soil-ledger.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            self.assertEqual(breaker.check(repo), 2)


class BreakerTripsSupervisorTests(unittest.TestCase):
    """`manual_cycle(autonomous=True)` engages the panic latch and returns 4
    once the streak reaches `FUTILE_BEAT_THRESHOLD` -- checked before the
    dangling-rollback refusal, so no other soil scaffolding (task
    declaration, runtime manifest, credentials) needs to exist for this."""

    def test_manual_cycle_trips_breaker_and_engages_latch(self):
        # substrate.supervisor imports pwd/grp at module level and is
        # POSIX-only (see the suite's other soil-process tests); skip
        # rather than fail on a platform without them.
        try:
            from substrate import supervisor
        except ModuleNotFoundError as exc:
            self.skipTest(f"substrate.supervisor unavailable on this platform: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            control = Path(tmp) / "control"
            (repo / "state").mkdir(parents=True)
            rows = [{"kind": "cycle", "task_id": "t1", "commit": None,
                    "failure_reason": "worker_error"}
                    for _ in range(breaker.FUTILE_BEAT_THRESHOLD)]
            (repo / "state" / "soil-ledger.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

            saved_control = os.environ.get("MERISTEM_CONTROL")
            os.environ["MERISTEM_CONTROL"] = str(control)
            try:
                with mock.patch.object(supervisor, "REPO", repo):
                    rc = supervisor.manual_cycle(autonomous=True)
                self.assertEqual(rc, 4)
                latch = control / "PANIC"
                self.assertTrue(latch.exists())
                self.assertIn("breaker", latch.read_text(encoding="utf-8"))
            finally:
                if saved_control is None:
                    os.environ.pop("MERISTEM_CONTROL", None)
                else:
                    os.environ["MERISTEM_CONTROL"] = saved_control

    def test_streak_below_threshold_does_not_trip(self):
        try:
            from substrate import supervisor
        except ModuleNotFoundError as exc:
            self.skipTest(f"substrate.supervisor unavailable on this platform: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            control = Path(tmp) / "control"
            (repo / "state").mkdir(parents=True)
            rows = [{"kind": "cycle", "task_id": "t1", "commit": None,
                    "failure_reason": "worker_error"}
                    for _ in range(breaker.FUTILE_BEAT_THRESHOLD - 1)]
            (repo / "state" / "soil-ledger.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

            saved_control = os.environ.get("MERISTEM_CONTROL")
            os.environ["MERISTEM_CONTROL"] = str(control)
            try:
                with mock.patch.object(supervisor, "REPO", repo):
                    # Below threshold: the breaker never fires, so the flow
                    # falls through to task loading against a repo with no
                    # task declaration, which raises SystemExit -- proof
                    # the breaker itself did not engage the latch, without
                    # needing the rest of the soil scaffolding present.
                    with self.assertRaises(SystemExit):
                        supervisor.manual_cycle(autonomous=True)
                self.assertFalse((control / "PANIC").exists())
            finally:
                if saved_control is None:
                    os.environ.pop("MERISTEM_CONTROL", None)
                else:
                    os.environ["MERISTEM_CONTROL"] = saved_control


if __name__ == "__main__":
    unittest.main()
