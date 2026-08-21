#!/usr/bin/env python3
"""Tests for the report command.

The report command reads state/journal.jsonl, state/scoreboard.jsonl,
state/proposals.md, and state/mailbox.md, writes REPORT.md at the repo
root, and prints the path. These tests verify each section of the report
is present and correct.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from meristem import loop  # noqa: E402


def _write_journal(path: pathlib.Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestReportCommand(unittest.TestCase):
    """Verify the report command produces REPORT.md with all required sections."""

    def _setup(self, tmpdir: pathlib.Path, *, journal_entries=None,
               proposals="", mailbox="", scoreboard_entries=None):
        state = tmpdir / "state"
        state.mkdir(parents=True, exist_ok=True)
        journal = state / "journal.jsonl"
        _write_journal(journal, journal_entries or [])
        (state / "proposals.md").write_text(proposals, encoding="utf-8")
        (state / "mailbox.md").write_text(mailbox, encoding="utf-8")
        scoreboard = state / "scoreboard.jsonl"
        _write_journal(scoreboard, scoreboard_entries or [])
        return journal, scoreboard

    def _run_report(self, tmpdir, journal, scoreboard):
        import io
        import contextlib

        mock_closure = MagicMock()
        mock_closure.tokens = 10000

        buf = io.StringIO()
        with patch("meristem.loop.REPO", tmpdir), \
             patch("meristem.loop.JOURNAL", journal), \
             patch("meristem.loop.SCOREBOARD", scoreboard), \
             patch("meristem.loop.deterministic.kernel_loc", return_value=1500), \
             patch("meristem.loop.closure_mod.compute", return_value=mock_closure):
            with contextlib.redirect_stdout(buf):
                rc = loop.main(["report"])
        return rc, buf.getvalue(), tmpdir / "REPORT.md"

    def test_creates_file_and_prints_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(tmpdir)
            rc, output, report_path = self._run_report(tmpdir, journal, scoreboard)
            self.assertEqual(rc, 0)
            self.assertTrue(output.strip().endswith("REPORT.md"))
            self.assertTrue(report_path.exists())

    def test_contains_cycles_since_last_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(tmpdir, journal_entries=[
                {"kind": "cycle", "cycle": 1, "outcome": "candidate", "why": "task A"},
                {"kind": "report", "cycle": 2, "core_pressure": 0.5,
                 "closure_pressure": 0.2},
                {"kind": "cycle", "cycle": 3, "outcome": "rejected", "why": "task B"},
                {"kind": "cycle", "cycle": 4, "outcome": "candidate", "why": "task C"},
            ])
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Cycles since last report", report)
            self.assertIn("Total: 2", report)
            self.assertIn("candidate: 1", report)
            self.assertIn("rejected: 1", report)

    def test_pressures_with_direction_arrows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(tmpdir, journal_entries=[
                {"kind": "report", "cycle": 1, "core_pressure": 0.4,
                 "closure_pressure": 0.3},
            ])
            # kernel_loc=2700 -> 2700/KERNEL_LOC_CAP, up from 0.40. DERIVED,
            # never hardcoded: the cap is designed to be raised through the
            # governance ladder, and pinning 3000 into the arithmetic here
            # made an approved cap case fail the canary on a test that has
            # nothing to do with what it changed (cycle 225, cap 3000->3200).
            # The mocked 2700 must stay above 0.40 * cap or the arrow below
            # flips to flat and the assertion stops meaning "went up".
            # closure tokens=15000 -> 15000/50000 = 0.30 (flat from 0.30)
            import io
            import contextlib

            mock_closure = MagicMock()
            mock_closure.tokens = 15000
            buf = io.StringIO()
            with patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.JOURNAL", journal), \
                 patch("meristem.loop.SCOREBOARD", scoreboard), \
                 patch("meristem.loop.deterministic.kernel_loc", return_value=2700), \
                 patch("meristem.loop.closure_mod.compute", return_value=mock_closure):
                with contextlib.redirect_stdout(buf):
                    loop.main(["report"])
            report = (tmpdir / "REPORT.md").read_text(encoding="utf-8")
            self.assertIn(f"{2700 / loop.deterministic.KERNEL_LOC_CAP:.2f}", report)
            self.assertIn("\u2191", report)  # core went up
            self.assertIn("0.30", report)
            self.assertIn("\u2192", report)  # closure flat

    def test_first_report_uses_flat_arrows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(tmpdir)
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("\u2192", report)

    def test_agr_self_proposed_over_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            # Provenance comes from the append-only auto_promote history, not
            # from proposals.md. A task that is still an OPEN proposal cannot
            # also be an accepted cycle -- winning consumes the proposal -- so
            # the old fixture staged a state the running system never reaches,
            # and a numerator that is structurally always zero looked correct.
            proposals = "- [ ] another proposal\n"
            journal, scoreboard = self._setup(tmpdir, journal_entries=[
                {"kind": "auto_promote", "task": "self-proposed task"},
                {"kind": "cycle", "cycle": 1, "outcome": "candidate",
                 "why": "self-proposed task"},
                {"kind": "cycle", "cycle": 2, "outcome": "candidate",
                 "why": "human task"},
                {"kind": "cycle", "cycle": 3, "outcome": "rejected",
                 "why": "self-proposed task 2"},
            ], proposals=proposals)
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("AGR", report)
            self.assertIn("1/2", report)

    def test_open_proposal_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            proposals = (
                "- [ ] proposal one\n"
                "- [ ] proposal two\n"
                "- [x] done proposal\n"
            )
            journal, scoreboard = self._setup(tmpdir, proposals=proposals)
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Open proposals", report)
            self.assertIn("count: 2", report)

    def test_parked_tasks_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            mailbox = "- PARKED: some task (rejected in cycles: 1, 2, 3)\n"
            journal, scoreboard = self._setup(tmpdir, journal_entries=[
                {"kind": "cycle", "cycle": 1, "outcome": "parked",
                 "why": "some task"},
            ], mailbox=mailbox)
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Parked tasks", report)
            self.assertIn("some task", report)

    def test_mailbox_items_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            mailbox = (
                "- PARKED: task A\n"
                "- PROPOSAL (needs human review): task B\n"
            )
            journal, scoreboard = self._setup(tmpdir, mailbox=mailbox)
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Mailbox", report)
            self.assertIn("task A", report)
            self.assertIn("task B", report)

    def test_report_journals_report_record(self):
        """A 'report' record must be appended to the journal so the next
        report knows which cycles are new."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(tmpdir, journal_entries=[
                {"kind": "cycle", "cycle": 1, "outcome": "candidate",
                 "why": "task A"},
            ])
            rc, _, _ = self._run_report(tmpdir, journal, scoreboard)
            lines = journal.read_text(encoding="utf-8").strip().splitlines()
            report_records = [
                json.loads(l) for l in lines
                if json.loads(l).get("kind") == "report"
            ]
            self.assertEqual(len(report_records), 1)
            self.assertIn("core_pressure", report_records[0])
            self.assertIn("closure_pressure", report_records[0])

    def test_probe_scores_from_scoreboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(
                tmpdir,
                scoreboard_entries=[
                    {"kind": "probe", "cycle": 1, "probe_id": "p1",
                     "score": 85.0, "domain": "d", "probe_kind": "internal"},
                    {"kind": "probe", "cycle": 2, "probe_id": "p1",
                     "score": 90.0, "domain": "d", "probe_kind": "internal"},
                    {"kind": "probe", "cycle": 2, "probe_id": "p2",
                     "score": 50.0, "domain": "d", "probe_kind": "anchor"},
                ],
            )
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Probe scores", report)
            self.assertIn("p1", report)
            self.assertIn("90.00", report)  # latest score for p1
            self.assertIn("p2", report)
            self.assertIn("50.00", report)

    def test_empty_state_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            journal, scoreboard = self._setup(tmpdir)
            rc, _, report_path = self._run_report(tmpdir, journal, scoreboard)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("(none)", report)
            self.assertIn("(empty)", report)
            self.assertIn("0/0", report)  # AGR with no accepted


if __name__ == "__main__":
    unittest.main()
