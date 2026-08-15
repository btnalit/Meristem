#!/usr/bin/env python3
"""Focused tests for the utility command.

The existing TestUtilityCommand in test_kernel.py uses weak assertions
(assertIn("3", output)) that would pass even with incorrect per-organ
counts, since "3" appears in multiple rows of the table. These tests parse
the output and verify exact per-organ values, closing the class of 'test
passes when the code is wrong' for this command.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from meristem import loop  # noqa: E402


def _parse_utility_output(output: str) -> dict[str, dict]:
    """Parse the utility command's stdout into {organ: {total, success, last_cycle}}."""
    data: dict[str, dict] = {}
    for line in output.strip().splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] not in ("organ", "-----"):
            data[parts[0]] = {
                "total": int(parts[1]),
                "success": int(parts[2]),
                "last_cycle": int(parts[3]),
            }
    return data


class TestUtilityCommandPrecise(unittest.TestCase):
    """The utility command must report exact per-organ values, not just
    have the right numbers appear somewhere in the output."""

    def _write_journal(self, entries: list[dict]) -> pathlib.Path:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        return pathlib.Path(tmp.name)

    def _run_utility(self, journal_path: pathlib.Path) -> str:
        import io
        import contextlib
        from unittest.mock import patch

        buf = io.StringIO()
        with patch("meristem.loop.JOURNAL", journal_path):
            with contextlib.redirect_stdout(buf):
                rc = loop.main(["utility"])
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_exact_per_organ_values(self):
        """Parse output lines and verify total, success, and last_cycle
        for each organ independently — not just that the numbers appear
        somewhere in the table."""
        tmp = self._write_journal([
            {"kind": "organ_call", "caller": "kernel", "callee": "word-count",
             "success": True, "cycle": 1},
            {"kind": "organ_call", "caller": "kernel", "callee": "word-count",
             "success": True, "cycle": 2},
            {"kind": "organ_call", "caller": "kernel", "callee": "text-stats",
             "success": False, "cycle": 3},
            {"kind": "organ_call", "caller": "kernel", "callee": "word-count",
             "success": True, "cycle": 4},
            {"kind": "organ_call", "caller": "kernel", "callee": "text-stats",
             "success": True, "cycle": 5},
        ])
        try:
            data = _parse_utility_output(self._run_utility(tmp))
            self.assertIn("word-count", data)
            self.assertEqual(data["word-count"]["total"], 3)
            self.assertEqual(data["word-count"]["success"], 3)
            self.assertEqual(data["word-count"]["last_cycle"], 4)
            self.assertIn("text-stats", data)
            self.assertEqual(data["text-stats"]["total"], 2)
            self.assertEqual(data["text-stats"]["success"], 1)
            self.assertEqual(data["text-stats"]["last_cycle"], 5)
        finally:
            tmp.unlink(missing_ok=True)

    def test_zero_success_organ(self):
        """An organ called but never succeeding is the strongest pruning
        signal — it must show 0 in the success column, not be omitted."""
        tmp = self._write_journal([
            {"kind": "organ_call", "caller": "kernel", "callee": "broken",
             "success": False, "cycle": 1},
            {"kind": "organ_call", "caller": "kernel", "callee": "broken",
             "success": False, "cycle": 2},
        ])
        try:
            data = _parse_utility_output(self._run_utility(tmp))
            self.assertIn("broken", data)
            self.assertEqual(data["broken"]["total"], 2)
            self.assertEqual(data["broken"]["success"], 0)
            self.assertEqual(data["broken"]["last_cycle"], 2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_empty_journal(self):
        """When no organ calls exist, the command reports it gracefully."""
        tmp = self._write_journal([])
        try:
            output = self._run_utility(tmp)
            self.assertIn("no organ calls", output.lower())
        finally:
            tmp.unlink(missing_ok=True)

    def test_ignores_non_organ_call_rows(self):
        """Only kind=='organ_call' rows must be counted — cycle, usage,
        and fault records must not inflate the totals."""
        tmp = self._write_journal([
            {"kind": "cycle", "cycle": 1, "outcome": "rejected"},
            {"kind": "organ_call", "caller": "kernel", "callee": "x",
             "success": True, "cycle": 1},
            {"kind": "usage", "role": "mutate", "model": "x"},
            {"kind": "fault", "cycle": 2, "error": "boom"},
        ])
        try:
            data = _parse_utility_output(self._run_utility(tmp))
            self.assertEqual(len(data), 1)
            self.assertIn("x", data)
            self.assertEqual(data["x"]["total"], 1)
            self.assertEqual(data["x"]["success"], 1)
            self.assertEqual(data["x"]["last_cycle"], 1)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
