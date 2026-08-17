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
from meristem import loop  # noqa: E402


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


class TestFaultRecordWrittenOnException(unittest.TestCase):
    """P-029c: when run_cycle catches an exception, a fault record must be
    written so the breaker (P-016) can distinguish mechanism failures from
    judged rejections. Without this, 429/timeout errors count as review
    rejections and park tasks prematurely."""

    def test_exception_produces_fault_record(self):
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
                mock_engine.propose.side_effect = RuntimeError("HTTP Error 429")
                result = run_cycle("test-task", cycle=8888)
            rows = read_jsonl(journal)
            fault_rows = [r for r in rows if r.get("kind") == "fault"
                          and r.get("cycle") == 8888]
            self.assertEqual(len(fault_rows), 1, "must write exactly one fault record")
            self.assertIn("429", fault_rows[0].get("error", ""))
            cycle_rows = [r for r in rows if r.get("kind") == "cycle"
                          and r.get("cycle") == 8888]
            self.assertTrue(len(cycle_rows) >= 1, "cycle record must also exist")

    def test_breaker_excludes_faulted_cycle(self):
        """A cycle with a fault record must not count as a judged rejection."""
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            from meristem import breaker
            for c in (1, 2, 3):
                append_jsonl(journal, {"kind": "cycle", "cycle": c,
                                       "outcome": "rejected", "why": "T"})
            append_jsonl(journal, {"kind": "fault", "cycle": 2, "task": "T",
                                   "error": "429"})
            append_jsonl(journal, {"kind": "fault", "cycle": 3, "task": "T",
                                   "error": "429"})
            orig = breaker.JOURNAL
            try:
                breaker.JOURNAL = journal
                self.assertEqual(breaker.rejections_for("T"), 1)
                self.assertFalse(breaker.should_park("T"))
            finally:
                breaker.JOURNAL = orig


class TestSubstrateCapEligibility(unittest.TestCase):
    """The soil keeps its own opinion of what may be auto-promoted.

    It must not import the kernel's version: the seed can legally mutate
    loop.py, so a fence imported from there could be relaxed by the very
    party it fences. That means rung 2 has to be taught to BOTH copies --
    changing only the kernel would leave complete cases stranded in
    proposals.md forever, which is a stall dressed as a safety property.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    COMPLETE = ("Raise the cap. Per-file LOC: loop.py 757. Core pressure 0.98, "
                "closure pressure 0.65. Already externalized the report "
                "formatter; insufficient. Proposed new cap 3400. Expected "
                "closure impact: none.")

    def test_complete_cap_case_is_promotable(self):
        sv = self._sv()
        self.assertFalse(sv._is_guarded_proposal(self.COMPLETE),
                         "a complete case must reach the panel")

    def test_unargued_cap_change_is_not_promotable(self):
        sv = self._sv()
        for text in ("raise the cap to 4000", "adjust KERNEL_LOC_CAP",
                     "提高上限到4000"):
            self.assertTrue(sv._is_guarded_proposal(text), text)

    def test_guarded_ground_outranks_a_complete_cap_case(self):
        sv = self._sv()
        self.assertTrue(
            sv._is_guarded_proposal(self.COMPLETE + " Also edit root/panic.py."),
            "six magic phrases must not buy passage past the root of trust")

    def test_soil_grants_the_same_one_path_exemption(self):
        """Both copies must agree, or a complete case strands in proposals.md."""
        sv = self._sv()
        naming_home = self.COMPLETE + " Change meristem/gates/deterministic.py."
        self.assertFalse(sv._is_guarded_proposal(naming_home),
                         "soil must allow the case to name the budget file")
        self.assertEqual(loop.route_proposal(naming_home), "agenda",
                         "kernel and soil must agree on the exemption")
        also_review = naming_home + " And meristem/gates/review.py."
        self.assertTrue(sv._is_guarded_proposal(also_review))
        self.assertEqual(loop.route_proposal(also_review), "mailbox")

    def test_substrate_does_not_import_the_kernel(self):
        """Structural separation, asserted rather than trusted.

        Statements only -- the docstring that EXPLAINS the rule says
        'MUST NOT import from meristem.loop', and a naive substring search
        fails on the prose describing the invariant it checks.
        """
        source = (REPO / "substrate" / "supervisor.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            self.assertFalse(stripped.startswith(("from meristem", "import meristem")),
                             f"substrate imports the kernel: {stripped}")

    def test_the_two_cap_checks_do_not_drift(self):
        """Both copies must recognise the same cap proposals.

        Duplication is deliberate (the seed may mutate loop.py) and therefore
        the copies CAN drift -- a marker the soil lacks is a hole in the very
        check that exists to survive the kernel being wrong. Caught exactly
        that: the kernel gained a bare '上限' marker and the soil did not, so
        '提高上限到4000' read as ordinary work to the last line of defence.
        """
        sv = self._sv()
        for text in ("raise the cap to 4000", "adjust KERNEL_LOC_CAP",
                     "提高上限到4000", "扩容到 3400", "proposed new cap 3400"):
            self.assertTrue(
                sv._is_guarded_proposal(text) or not loop.mentions_cap_change(text),
                f"soil misses a cap proposal the kernel catches: {text}")
            self.assertTrue(
                loop.mentions_cap_change(text),
                f"kernel misses a cap proposal: {text}")


class TestApprovalSeatRearm(unittest.TestCase):
    """A human gate is a prosthetic (6.1) and needs an exit condition that
    does not depend on a human remembering to delete a file. Demotion stays
    automatic and evidence-free -- the ratchet turns freely toward safety --
    but the way back is evidence-backed and automatic too."""

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _run(self, outcomes):
        """Seed a journal with a demotion then `outcomes`; return (locked, sv)."""
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            lock = pathlib.Path(tmp) / "seat.lock"
            append_jsonl(journal, {"kind": "seat_change",
                                   "seat": "proposal_approval",
                                   "from_rung": 2, "to_rung": 1})
            for i, outcome in enumerate(outcomes):
                append_jsonl(journal, {"kind": "cycle", "cycle": i,
                                       "outcome": outcome, "why": f"t{i}"})
            lock.write_text("demoted\n", encoding="utf-8")
            orig_j, orig_l = sv.JOURNAL, sv.SEAT_LOCK
            try:
                sv.JOURNAL, sv.SEAT_LOCK = journal, lock
                with patch.object(sv, "notify"), patch.object(sv, "_journal"):
                    locked = sv._check_demotion()
                return locked, lock.exists()
            finally:
                sv.JOURNAL, sv.SEAT_LOCK = orig_j, orig_l

    def test_three_accepted_cycles_rearm_the_seat(self):
        locked, lock_exists = self._run(["candidate"] * 3)
        self.assertFalse(locked, "seat must re-arm after three accepted cycles")
        self.assertFalse(lock_exists, "lock file must be removed on re-arm")

    def test_a_rejection_in_the_streak_keeps_the_lock(self):
        locked, lock_exists = self._run(["candidate", "rejected", "candidate"])
        self.assertTrue(locked, "a rejection inside the streak must not re-arm")
        self.assertTrue(lock_exists)

    def test_too_few_cycles_keep_the_lock(self):
        locked, _ = self._run(["candidate", "candidate"])
        self.assertTrue(locked, "two accepted cycles are not the streak")

    def test_rearm_gives_a_fresh_three_strike_budget(self):
        """After a re-arm, ONE fresh failure must not re-demote.

        The failures that caused a demotion stay in the journal forever. If
        the demotion scan still counts them, promotes[-3:] after a re-arm is
        [old_fail, old_fail, new_task] and a single new failure re-demotes --
        a 3-strike rule collapsed to 1, oscillating the seat.
        """
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            lock = pathlib.Path(tmp) / "seat.lock"
            for i in range(3):  # the failures that caused the demotion
                append_jsonl(journal, {"kind": "auto_promote", "task": f"old{i}"})
                append_jsonl(journal, {"kind": "cycle", "cycle": i,
                                       "outcome": "rejected", "why": f"old{i}"})
            append_jsonl(journal, {"kind": "seat_change", "seat": "proposal_approval",
                                   "from_rung": 2, "to_rung": 1})
            for i in range(3):  # earned back
                append_jsonl(journal, {"kind": "cycle", "cycle": 10 + i,
                                       "outcome": "candidate", "why": f"good{i}"})
            append_jsonl(journal, {"kind": "seat_change", "seat": "proposal_approval",
                                   "from_rung": 1, "to_rung": 2})
            append_jsonl(journal, {"kind": "auto_promote", "task": "fresh"})
            append_jsonl(journal, {"kind": "cycle", "cycle": 20,
                                   "outcome": "rejected", "why": "fresh"})
            orig_j, orig_l = sv.JOURNAL, sv.SEAT_LOCK
            try:
                sv.JOURNAL, sv.SEAT_LOCK = journal, lock  # lock absent = rung 2
                with patch.object(sv, "notify"), patch.object(sv, "_journal"):
                    self.assertFalse(sv._check_demotion(),
                                     "one failure after a re-arm must not re-demote")
            finally:
                sv.JOURNAL, sv.SEAT_LOCK = orig_j, orig_l

    def test_streak_resets_on_a_later_demotion(self):
        """Cycles earned before a later demotion must not pay for it."""
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            lock = pathlib.Path(tmp) / "seat.lock"
            demote = {"kind": "seat_change", "seat": "proposal_approval",
                      "from_rung": 2, "to_rung": 1}
            append_jsonl(journal, demote)
            for i in range(3):
                append_jsonl(journal, {"kind": "cycle", "cycle": i,
                                       "outcome": "candidate", "why": f"a{i}"})
            append_jsonl(journal, demote)  # demoted again; the streak is spent
            lock.write_text("demoted\n", encoding="utf-8")
            orig_j, orig_l = sv.JOURNAL, sv.SEAT_LOCK
            try:
                sv.JOURNAL, sv.SEAT_LOCK = journal, lock
                with patch.object(sv, "notify"), patch.object(sv, "_journal"):
                    self.assertTrue(sv._check_demotion(),
                                    "the second demotion must start a fresh streak")
            finally:
                sv.JOURNAL, sv.SEAT_LOCK = orig_j, orig_l


if __name__ == "__main__":
    unittest.main()
