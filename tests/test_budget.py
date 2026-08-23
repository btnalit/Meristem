"""Tests for substrate/budget.py (S7, v5 spec §5 I1 / §8.1.3 / §10.2 / §13.2).

I1's whole point is the v3.1 postmortem: `campaign_calls = 1000` was an
all-time cumulative counter, and once it hit the cap `check()` raised on
every mutation *and* every reflection -- the loop deadlocked, and the only
actor who could fix the gate was locked out by the gate. These tests target
exactly that shape:

  * a call outside the rolling window must stop counting (the window
    actually rolls, so the lock releases itself as cycles advance);
  * hitting the cap must return a string, never raise -- "reached the cap"
    has to be an ordinary return value on the hot path, not an exception a
    caller must remember to catch.
"""
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate import budget  # noqa: E402
from substrate.soil_state import SoilStateError  # noqa: E402


def _policy(window_cycles=2, calls_per_cycle=100, calls_per_window=3) -> dict:
    return {"budget": {"window_cycles": window_cycles,
                       "calls_per_cycle": calls_per_cycle,
                       "calls_per_window": calls_per_window}}


class RollingWindowTests(unittest.TestCase):
    """I1: the window is rolling, not lifetime-cumulative."""

    def test_call_outside_the_window_stops_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            calls = budget.ModelCallLedger(ledger)
            policy = _policy(window_cycles=2, calls_per_cycle=100, calls_per_window=1)

            # cycle 1: one call recorded -> window cap (1) reached for cycle 1.
            calls.record(cycle=1, role="mutate", slot_id="mutate:glm")
            self.assertIsNotNone(budget.check(ledger, 1, policy=policy),
                                 "window cap should already be hit in cycle 1")

            # cycle 2: still inside the 2-cycle window [1, 2] -> still refused.
            self.assertIsNotNone(budget.check(ledger, 2, policy=policy),
                                 "cycle 1's call is still inside a window_cycles=2 window")

            # cycle 3: window is now [2, 3] -- cycle 1's call has rolled out.
            # This is the deadlock fix in action: nobody cleared or retired
            # anything, the window simply advanced past the old record.
            self.assertIsNone(budget.check(ledger, 3, policy=policy),
                              "a call outside the rolling window must stop counting")

    def test_calls_per_cycle_is_independent_of_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            calls = budget.ModelCallLedger(ledger)
            policy = _policy(window_cycles=5, calls_per_cycle=1, calls_per_window=100)
            calls.record(cycle=1, role="mutate", slot_id="mutate:glm")
            self.assertIsNotNone(budget.check(ledger, 1, policy=policy))
            # A different cycle has its own per-cycle budget.
            self.assertIsNone(budget.check(ledger, 2, policy=policy))


class CapRefusesRatherThanDeadlocksTests(unittest.TestCase):
    """The v3.1 shape: check() must return, never raise, even hammered at cap."""

    def test_check_returns_a_string_at_cap_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = _policy(calls_per_cycle=1, calls_per_window=1)
            budget.ModelCallLedger(ledger).record(cycle=1, role="mutate", slot_id="s")
            result = budget.check(ledger, 1, policy=policy)
            self.assertIsInstance(result, str)

    def test_repeated_calls_at_cap_never_raise(self):
        """This is the direct regression test for the v3.1 deadlock shape:
        `check()` at cap must stay callable forever -- the only actor who
        could fix a stuck gate must never be a caller of this function."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = _policy(calls_per_cycle=1, calls_per_window=1)
            budget.ModelCallLedger(ledger).record(cycle=1, role="mutate", slot_id="s")
            for _ in range(50):
                result = budget.check(ledger, 1, policy=policy)
                self.assertIsNotNone(result)

    def test_next_cycle_is_not_permanently_locked(self):
        """The cap on cycle N must not poison cycle N+1 forever -- unlike
        v3.1's lifetime counter, which never released once tripped."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = _policy(window_cycles=1, calls_per_cycle=1, calls_per_window=1)
            budget.ModelCallLedger(ledger).record(cycle=1, role="mutate", slot_id="s")
            self.assertIsNotNone(budget.check(ledger, 1, policy=policy))
            self.assertIsNone(budget.check(ledger, 2, policy=policy),
                              "window_cycles=1 means cycle 2 must not see cycle 1's call")


class MissingOrInvalidPolicyFailsClosedTests(unittest.TestCase):
    """A budget that can't be read must refuse, not silently allow unlimited
    calls -- an unconfigured gate and a nonexistent gate must not both look
    like 'allowed' (same family as C-65's silent-wrong-default lesson)."""

    def test_missing_budget_table_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            self.assertIsNotNone(budget.check(ledger, 1, policy={}))

    def test_missing_key_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = {"budget": {"window_cycles": 1, "calls_per_cycle": 1}}  # no calls_per_window
            self.assertIsNotNone(budget.check(ledger, 1, policy=policy))

    def test_bool_disguised_as_int_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = {"budget": {"window_cycles": 1, "calls_per_cycle": True,
                                 "calls_per_window": 1}}
            self.assertIsNotNone(budget.check(ledger, 1, policy=policy))

    def test_non_int_cycle_refuses_not_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            result = budget.check(ledger, "not-a-cycle", policy=_policy())
            self.assertIsInstance(result, str)


class ModelCallLedgerReusesSoilStateTests(unittest.TestCase):
    """§8.1.5: budget.py must reuse soil_state's append-only writer, not
    reinvent one -- and inherit its filename-family enforcement for free."""

    def test_bad_filename_is_rejected_by_the_shared_base_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SoilStateError):
                budget.ModelCallLedger(Path(tmp) / "not-a-soil-file.txt")

    def test_record_appends_a_readable_jsonl_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "soil-model-calls.jsonl"
            budget.ModelCallLedger(ledger_path).record(cycle=3, role="mutate", slot_id="mutate:glm")
            rows = budget.ModelCallLedger(ledger_path).read()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["cycle"], 3)
            self.assertEqual(rows[0]["role"], "mutate")
            self.assertEqual(rows[0]["slot_id"], "mutate:glm")
            self.assertIn("ts", rows[0])


class LoadPolicyTests(unittest.TestCase):
    """load_policy() reads soil/model-policy.toml-shaped TOML for real --
    a dict-injection test alone wouldn't catch a key spelled differently in
    the actual TOML file than in this module's code."""

    def test_reads_declared_budget_keys_from_a_real_toml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            toml_path = Path(tmp) / "model-policy.toml"
            toml_path.write_text(textwrap.dedent("""\
                [budget]
                window_cycles = 12
                calls_per_cycle = 12
                calls_per_window = 144
                cycle_usd = 1.0
                window_usd = 25.0
                """), encoding="utf-8")
            policy = budget.load_policy(toml_path)
            self.assertEqual(policy["budget"]["window_cycles"], 12)
            self.assertEqual(policy["budget"]["calls_per_cycle"], 12)
            self.assertEqual(policy["budget"]["calls_per_window"], 144)

    def test_default_path_points_at_the_real_repo_policy_file(self):
        self.assertTrue(budget.DEFAULT_POLICY_PATH.is_file())
        policy = budget.load_policy()
        self.assertIn("budget", policy)
        self.assertIn("window_cycles", policy["budget"])


class RealPolicyNumbersDoNotDeadlockTests(unittest.TestCase):
    """Sanity check against the actual numbers declared in
    soil/model-policy.toml (window_cycles=12, calls_per_cycle=12,
    calls_per_window=144 = window_cycles * calls_per_cycle): filling one
    cycle's worth of calls must not lock out any *other* cycle forever."""

    def test_real_policy_window_releases_after_window_cycles(self):
        policy = budget.load_policy()
        cfg = policy["budget"]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            calls = budget.ModelCallLedger(ledger)
            for _ in range(cfg["calls_per_cycle"]):
                calls.record(cycle=1, role="mutate", slot_id="mutate:glm")
            self.assertIsNotNone(budget.check(ledger, 1, policy=policy))
            far_cycle = 1 + cfg["window_cycles"]
            self.assertIsNone(budget.check(ledger, far_cycle, policy=policy),
                              "cycle 1's calls must have rolled out of the window by now")


if __name__ == "__main__":
    unittest.main()
