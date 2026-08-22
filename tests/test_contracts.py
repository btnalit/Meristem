#!/usr/bin/env python3
"""Cross-layer contract tests. Lock the invariants that P-029/P-029b fixed."""

from __future__ import annotations

import json
import os
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


class TestBeatFirewall(unittest.TestCase):
    """The soil's own exceptions must not be more fatal than the seed's.

    A seed failure was always handled: counted, escalated to rollback after
    three. A soil exception -- from core_pressure, _auto_promote,
    _commit_state or promote -- skipped all of that and killed all fourteen
    beats. Three separate nights died that way before the class was named.
    The firewall adds no new machinery; it routes soil exceptions into the
    machinery that already existed.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _run_beats(self, beats, promote_side_effect, journal):
        sv = self._sv()
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        orig = sv.JOURNAL
        try:
            sv.JOURNAL = journal
            with patch.object(sv, "panic") as pan, \
                 patch.object(sv, "core_pressure", return_value=0.5), \
                 patch.object(sv, "pending_task", return_value=True), \
                 patch.object(sv, "_commit_state"), \
                 patch.object(sv, "subprocess") as sp, \
                 patch.object(sv, "resolve", return_value="c" * 40), \
                 patch.object(sv, "promote", side_effect=promote_side_effect), \
                 patch.object(sv, "rollback", return_value=99) as rb, \
                 patch.object(sv, "notify"), \
                 patch.object(sv, "_pressure_reflected_today", return_value=True):
                pan.engaged.return_value = False
                sp.run.return_value = ok
                rc = sv.heartbeat(beats, dry=True)
            return rc, rb
        finally:
            sv.JOURNAL = orig

    def test_publish_runs_every_beat_not_only_on_promotion(self):
        """publish() was reachable from promote() and nowhere else.

        Promotions are sparse -- cycle 375 was the last one before this was
        written -- so every soil commit and every beat's bookkeeping sat on
        main unpushed for as long as the seed went without landing a change.
        Seven commits had accumulated when it was noticed, and clearing them
        took a human running git push, which is the one thing this system is
        not supposed to need.
        """
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            sv = self._sv()
            with patch.object(sv, "publish") as pub:
                rc, _ = self._run_beats(3, [None, None, None], journal)
        self.assertEqual(rc, 0)
        self.assertEqual(pub.call_count, 3, "one publish per beat, promotion or not")

    def test_one_exception_costs_a_beat_not_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            calls = [RuntimeError("git exploded"), None, None]
            rc, rb = self._run_beats(3, calls, journal)
            self.assertEqual(rc, 0, "the run must finish all three beats")
            rb.assert_not_called()
            rows = [r for r in read_jsonl(journal)
                    if r.get("kind") == "beat_exception"]
            self.assertEqual(len(rows), 1)
            self.assertIn("git exploded", rows[0]["reason"])
            self.assertIn("Traceback", rows[0]["traceback"])

    def test_three_consecutive_exceptions_reach_rollback(self):
        """The budget is the same one subprocess failures spend."""
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            boom = [RuntimeError("a"), RuntimeError("b"), RuntimeError("c")]
            rc, rb = self._run_beats(5, boom, journal)
            self.assertEqual(rc, 99, "must return rollback's value")
            rb.assert_called_once()
            self.assertIn("consecutive", rb.call_args[0][0])

    def test_panic_still_stops_everything(self):
        """The latch is checked outside the firewall and must not be caught."""
        sv = self._sv()
        with patch.object(sv, "panic") as pan, \
             patch.object(sv, "_pressure_reflected_today", return_value=True):
            pan.engaged.return_value = True
            self.assertEqual(sv.heartbeat(5, dry=True), 3)

    def test_keyboard_interrupt_is_not_swallowed(self):
        """except Exception, never BaseException."""
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            with self.assertRaises(KeyboardInterrupt):
                self._run_beats(3, [KeyboardInterrupt()], journal)


class TestPublishIsNeverFatal(unittest.TestCase):
    """publish()'s own docstring: "failing to publish must not undo a
    promotion that already succeeded." Only the returncode branch honoured
    that. A push that ran past its 120s timeout raised TimeoutExpired
    through publish -> promote -> heartbeat, the beat exited 1, and the
    keeper stopped a fourteen-beat run -- over an announcement, after the
    promotion had already landed.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _publish_with(self, side_effect=None, returncode=0, stderr=""):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            orig = sv.JOURNAL
            kwargs = ({"side_effect": side_effect} if side_effect else
                      {"return_value": types.SimpleNamespace(
                          returncode=returncode, stdout="", stderr=stderr)})
            try:
                sv.JOURNAL = journal
                with patch.dict(os.environ, {"MERISTEM_PUBLISH": "1"}), \
                     patch.object(sv.subprocess, "run", **kwargs), \
                     patch.object(sv, "resolve", return_value="a" * 40):
                    sv.publish()          # must not raise
            finally:
                sv.JOURNAL = orig
            return read_jsonl(journal)

    def test_timeout_does_not_propagate(self):
        import subprocess as sp
        rows = self._publish_with(
            side_effect=sp.TimeoutExpired(cmd="git push", timeout=120))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "publish_failed")
        self.assertIn("TimeoutExpired", rows[0]["reason"])

    def test_exec_failure_does_not_propagate(self):
        rows = self._publish_with(side_effect=OSError("git missing"))
        self.assertEqual(rows[0]["kind"], "publish_failed")

    def test_nonzero_exit_is_journalled_not_just_printed(self):
        rows = self._publish_with(returncode=1, stderr="non-fast-forward")
        self.assertEqual(rows[0]["kind"], "publish_failed")
        self.assertIn("non-fast-forward", rows[0]["reason"])

    def test_success_journals_nothing(self):
        self.assertEqual(self._publish_with(returncode=0), [])


class TestReviewChecklistNamesItsEnforcement(unittest.TestCase):
    """Cycle 363 refused a correct diagnosis on a misread of one line.

    "Does it grant the mutation engine access it did not have" says access and
    means two different things with two different enforcement points. A
    reviewer applied it to a proposal that asked to SEE body/organs/*.json and
    refused it for making organ self-promotion possible -- which write access,
    never withheld by _validate_paths, had already made possible and which
    refusing that proposal did nothing about.

    The checklist is the reviewers' only reference. If the names of the real
    enforcement points drift out of it, or a rewrite drops the distinction,
    the panel goes back to guessing from a word.
    """

    def test_checklist_names_where_write_authority_is_enforced(self):
        text = (REPO / "control" / "checklists.md").read_text(encoding="utf-8")
        for name in ("EXCLUDED_DIRS", "EXCLUDED_PREFIXES", "guard_lifecycle"):
            self.assertIn(name, text,
                          f"reviewers cannot check {name} without being told it exists")
        self.assertIn("Read and write are separate", text)

    def test_checklist_states_the_lifecycle_rule(self):
        text = (REPO / "control" / "checklists.md").read_text(encoding="utf-8")
        self.assertIn("one stage per promotion", text)


class TestStaleCandidateIsNamedForWhatItIs(unittest.TestCase):
    """Cycle 384 changed two files under body/organs/failure-aggregator, was
    approved 2/2, and was refused with "touches protected paths:
    ['substrate/supervisor.py']".

    It had never opened that file. main gained three commits while the cycle
    was in flight, HEAD..candidate rendered them as reversals, and
    guard_protected could not tell a reversal from an edit. The candidate was
    unpromotable either way -- the merge is --ff-only -- but the reason was
    false, and a false reason does not stop at the log: it reaches the mailbox,
    the webhook, and the next mutation prompt through failure_history().
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _promote(self, merge_base, head, journal):
        sv = self._sv()
        calls = []

        def _git(*args, **kwargs):
            calls.append(args)
            if args[0] == "merge-base":
                return merge_base
            if args[0] == "rev-parse":
                return head
            return "cycle 9: do a thing"

        orig = sv.JOURNAL
        try:
            sv.JOURNAL = journal
            with patch.object(sv, "panic") as pan, \
                 patch.object(sv, "resolve", return_value="c" * 40), \
                 patch.object(sv, "guard_protected", return_value=[]) as gp, \
                 patch.object(sv, "guard_lifecycle", return_value=[]), \
                 patch.object(sv, "canary", return_value=(True, "")), \
                 patch.object(sv, "publish"), \
                 patch.object(sv, "record_scoreboard", return_value=16), \
                 patch.object(sv, "git", _git), \
                 patch.object(sv, "notify"):
                pan.engaged.return_value = False
                rc = sv.promote()
        finally:
            sv.JOURNAL = orig
        return rc, calls, gp

    def test_a_stale_candidate_is_refused_by_its_own_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            rc, calls, gp = self._promote("older", "newer", journal)
            self.assertEqual(rc, 7)
            self.assertEqual(gp.call_count, 0,
                             "every gate below compares against HEAD and is"
                             " meaningless once HEAD has moved")
            rows = read_jsonl(journal)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "canary_reject",
                             "any other kind marks the task done forever (P-026)")
            self.assertIn("fast-forward", rows[0]["reason"])
            self.assertNotIn("protected", rows[0]["reason"],
                             "the old wording blamed a file the seed never opened")
            self.assertTrue(any(a[:2] == ("update-ref", "-d") for a in calls),
                            "an uncleared ref wedges every later beat")

    def test_promote_never_touches_production_state_from_a_test(self):
        """P-080 added record_scoreboard() to the end of promote(), and the
        test above drives promote() all the way through. It ran the real
        sixteen-probe set and appended sixteen rows to the PRODUCTION
        scoreboard, attributed to commit "cccccccccccc".

        Same family as the two vault incidents: a test reaching live state
        because the code under test resolves its paths from REPO. Nothing
        about the values was wrong -- probe_scores really did measure the tree
        -- but a register that records a commit which does not exist is a
        register telling a small lie, and it got there from a test run.

        Every side effect promote() performs must be patched by anything that
        drives it to completion.
        """
        sv = self._sv()
        import inspect
        body = inspect.getsource(sv.promote)
        effects = [name for name in ("record_scoreboard", "publish", "_journal")
                   if name + "(" in body]
        self.assertIn("record_scoreboard", effects,
                      "if this call is gone the test above needs its patch removed")
        source = inspect.getsource(type(self))
        for name in effects:
            if name == "_journal":
                continue  # journalling is redirected by JOURNAL, not by a patch
            self.assertIn(f'patch.object(sv, "{name}"', source,
                          f"promote() calls {name}() and no test here patches it")

    def test_a_current_candidate_still_reaches_the_other_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            rc, _, gp = self._promote("same", "same", journal)
            self.assertEqual(rc, 0)
            self.assertEqual(gp.call_count, 1)


class TestScoreboardIsRefreshedOnPromotion(unittest.TestCase):
    """A cycle samples three probes of sixteen and records only those.

    Everything downstream reads the result as current: probes.baseline() for
    the regression check, and the report's probe_scores for the seed's own
    view of its measuring sticks. probe-journal-query-basic sat at 100.00 in
    that memory while scoring 0.00 on main.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def test_the_whole_frozen_set_is_written_after_promotion(self):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            board = pathlib.Path(tmp) / "scoreboard.jsonl"
            orig = sv.SCOREBOARD
            try:
                sv.SCOREBOARD = board
                with patch.object(sv, "probe_scores",
                                  return_value={"p-a": 100.0, "p-b": 0.0,
                                                "p-d": None}):
                    n = sv.record_scoreboard("c" * 40)
            finally:
                sv.SCOREBOARD = orig
            rows = read_jsonl(board)
        self.assertEqual(n, 2, "a declarative probe records no score at all")
        self.assertEqual({r["probe_id"]: r["score"] for r in rows},
                         {"p-a": 100.0, "p-b": 0.0},
                         "a probe that failed must be recorded as failing, not omitted")
        self.assertTrue(all(r["kind"] == "probe" for r in rows),
                        "baseline() only reads rows whose kind is probe")
        self.assertTrue(all(r["commit"] == "c" * 12 for r in rows),
                        "a score with no commit cannot be attributed to a tree")

    def test_a_failed_measurement_writes_nothing_rather_than_zeroes(self):
        """Recording an unmeasurable set as all-zero would invent a regression
        on every probe at once -- the overclaim of P-078 with the sign flipped."""
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            board = pathlib.Path(tmp) / "scoreboard.jsonl"
            orig = sv.SCOREBOARD
            try:
                sv.SCOREBOARD = board
                with patch.object(sv, "probe_scores", return_value={}):
                    n = sv.record_scoreboard("c" * 40)
            finally:
                sv.SCOREBOARD = orig
            self.assertEqual(n, 0)
            self.assertFalse(board.exists() and board.read_text(encoding="utf-8").strip())


class TestFrozenSetGuardsPromotion(unittest.TestCase):
    """probes.py has said "the full frozen set runs before promotion" since P0.

    full=True had no caller anywhere in the tree. A cycle draws three probes of
    sixteen at random, so thirteen frozen probes went unexamined every beat,
    and probe-journal-query-basic has been scoring 0 on main where the
    scoreboard remembers 100 without a single cycle noticing.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _canary(self, before, after):
        sv = self._sv()
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        scores = iter([before, after])
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            orig = sv.JOURNAL
            try:
                sv.JOURNAL = journal
                with patch.object(sv, "git"), \
                     patch.object(sv, "subprocess") as sp, \
                     patch.object(sv, "probe_scores", side_effect=lambda t: next(scores)):
                    sp.run.return_value = ok
                    result = sv.canary("c" * 40)
            finally:
                sv.JOURNAL = orig
            rows = read_jsonl(journal)
        return result, rows

    def test_a_declarative_probe_is_not_broken(self):
        """mailbox-ack-basic and mailbox-ack-expiry carry a statement and no
        rubric. P-066 admits that form to the vault with a 0 baseline on
        purpose. Reporting them as "already failing on HEAD" says the seed
        broke something, when the truth is that nobody has written the ruler
        yet -- the P-078 overclaim, one layer over."""
        (ok, _), rows = self._canary({"p-decl": None, "p-real": 0.0},
                                     {"p-decl": None, "p-real": 0.0})
        self.assertTrue(ok)
        rec = [r for r in rows if r.get("kind") == "probe_broken"][0]
        self.assertEqual(rec["probes"], ["p-real"])
        self.assertEqual(rec["unmeasurable"], ["p-decl"])

    def test_a_declarative_probe_cannot_trigger_a_regression(self):
        (ok, why), _ = self._canary({"p-decl": None}, {"p-decl": None})
        self.assertTrue(ok, why)

    def test_a_new_regression_refuses_the_candidate(self):
        (ok, why), _ = self._canary({"p-a": 100.0}, {"p-a": 0.0})
        self.assertFalse(ok)
        self.assertIn("frozen set regressed", why)
        self.assertIn("p-a", why)

    def test_a_probe_already_broken_on_head_does_not_refuse(self):
        """Otherwise every candidate is punished for damage it did not do, and
        the one task that could repair it never gets to run."""
        (ok, why), rows = self._canary({"p-a": 0.0}, {"p-a": 0.0})
        self.assertTrue(ok, why)
        broken = [r for r in rows if r.get("kind") == "probe_broken"]
        self.assertEqual(len(broken), 1, "a pre-existing failure must still be visible")
        self.assertEqual(broken[0]["probes"], ["p-a"])

    def test_an_improvement_is_never_a_regression(self):
        (ok, _), _ = self._canary({"p-a": 0.0}, {"p-a": 100.0})
        self.assertTrue(ok)

    def test_a_failed_measurement_is_not_read_as_no_regression(self):
        (ok, _), rows = self._canary({}, {})
        self.assertTrue(ok, "an unmeasurable set must not block promotion")
        self.assertTrue([r for r in rows if r.get("kind") == "probe_unmeasured"],
                        "but silence about it would be the overclaim P-078 removed")


class TestOrganLifecycleCannotSkip(unittest.TestCase):
    """germline.advance() refuses to skip a stage and has no callers.

    Every lifecycle change this system has ever made was a mutation rewriting
    organ.json, where nothing checked step order at all. Cycle 383 did it
    correctly, candidate -> calibrate. Nothing would have stopped it writing
    "active" in the same commit and landing an organ that never met a probe.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _git(self, changed, trees):
        def _fake(*args, **kwargs):
            if args[0] == "diff":
                return "\n".join(changed)
            if args[0] == "show":
                ref, _, path = args[1].partition(":")
                return trees.get(ref, {}).get(path, "")
            return ""
        return _fake

    REL = "body/organs/feasibility-check/organ.json"

    def _check(self, old, new, present=True):
        sv = self._sv()
        trees = {"cand": {self.REL: json.dumps({"lifecycle": new})}}
        if present:
            trees["HEAD"] = {self.REL: json.dumps({"lifecycle": old})}
        with patch.object(sv, "git", self._git([self.REL], trees)):
            return sv.guard_lifecycle("HEAD", "cand")

    def test_one_stage_forward_is_allowed(self):
        """Cycle 383's real promotion. The gate must not break it."""
        self.assertEqual(self._check("candidate", "calibrate"), [])

    def test_skipping_a_stage_is_refused(self):
        problems = self._check("candidate", "active")
        self.assertEqual(len(problems), 1)
        self.assertIn("skips the lifecycle", problems[0])

    def test_moving_backward_is_refused(self):
        problems = self._check("active", "calibrate")
        self.assertEqual(len(problems), 1)
        self.assertIn("skips the lifecycle", problems[0])

    def test_an_unchanged_lifecycle_is_not_a_transition(self):
        """organ.json changes for many reasons -- adding probe ids, for one,
        which is most of what cycle 383's task was about."""
        self.assertEqual(self._check("calibrate", "calibrate"), [])

    def test_a_new_organ_must_enter_at_candidate(self):
        self.assertEqual(self._check(None, "candidate", present=False), [])
        problems = self._check(None, "active", present=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("enters at 'candidate'", problems[0])

    def test_the_refusal_journals_and_clears_the_ref(self):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            calls = []
            orig = sv.JOURNAL
            try:
                sv.JOURNAL = journal
                with patch.object(sv, "panic") as pan, \
                     patch.object(sv, "resolve", return_value="c" * 40), \
                     patch.object(sv, "guard_protected", return_value=[]), \
                     patch.object(sv, "guard_lifecycle",
                                  return_value=["organ.json: skips"]), \
                     patch.object(sv, "canary") as can, \
                     patch.object(sv, "git",
                                  side_effect=lambda *a, **k: calls.append(a)
                                  or "cycle 9: do a thing"), \
                     patch.object(sv, "notify"):
                    pan.engaged.return_value = False
                    rc = sv.promote()
            finally:
                sv.JOURNAL = orig
            self.assertEqual(rc, 6)
            self.assertEqual(can.call_count, 0,
                             "the canary boot is expensive; refuse before it")
            rows = read_jsonl(journal)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "canary_reject",
                             "any other kind marks the task done forever (P-026)")
            self.assertEqual(rows[0]["why"], "do a thing", "the task must reopen")
            self.assertTrue(any(a[:2] == ("update-ref", "-d") for a in calls),
                            "an uncleared ref wedges every later beat")


class TestProtectedRefusalIsNotSilent(unittest.TestCase):
    """A candidate touching guarded ground used to wedge the loop forever.

    promote() printed to stderr, returned 4, and left CANDIDATE_REF standing.
    Every later beat re-resolved the same candidate and refused it the same
    way, with nothing in the journal saying why the loop was stuck.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def test_refusal_journals_and_clears_the_ref(self):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            calls = []
            orig = sv.JOURNAL
            try:
                sv.JOURNAL = journal
                with patch.object(sv, "panic") as pan, \
                     patch.object(sv, "resolve", return_value="deadbeef" * 5), \
                     patch.object(sv, "guard_protected",
                                  return_value=["root/panic.py"]), \
                     patch.object(sv, "git",
                                  side_effect=lambda *a, **k: calls.append(a)
                                  or "cycle 9: do a thing"), \
                     patch.object(sv, "notify"):
                    pan.engaged.return_value = False
                    rc = sv.promote()
            finally:
                sv.JOURNAL = orig

            self.assertEqual(rc, 4)
            rows = read_jsonl(journal)
            self.assertEqual(len(rows), 1, "the refusal must be journalled")
            self.assertIn("protected paths", rows[0]["reason"])
            self.assertEqual(rows[0]["why"], "do a thing",
                             "the task text must survive so it can be retried")
            self.assertTrue(
                any(a[:2] == ("update-ref", "-d") for a in calls),
                "the candidate ref must be cleared or the wedge repeats")

    def test_it_is_recorded_as_a_canary_reject_so_the_task_reopens(self):
        """done_tasks() is (candidates - canary_rejects) | promoted.

        The cycle record for this task already says outcome=candidate. Any
        OTHER record kind would leave it inside `candidates` with nothing
        subtracting it, so the task would read as DONE forever and the work
        would vanish -- P-026's exact failure, re-entered by a new door.
        """
        from meristem import journal as jr
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            append_jsonl(j, {"kind": "cycle", "cycle": 9, "outcome": "candidate",
                             "why": "do a thing"})
            self.assertIn("do a thing", jr.done_tasks(j),
                          "precondition: a bare candidate reads as done")
            append_jsonl(j, {"kind": "canary_reject", "commit": "abc",
                             "why": "do a thing",
                             "reason": "REFUSED: touches protected paths"})
            self.assertNotIn("do a thing", jr.done_tasks(j),
                             "the refusal must reopen the task for retry")


class TestPressureProbeLeavesATrace(unittest.TestCase):
    """0.0 is the dangerous way to fail: unknown pressure reads as NO
    pressure, suppressing the mandate exactly when the kernel is at its cap.
    The value is unchanged for now; the silence is not."""

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def test_failed_status_is_journalled(self):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            fake = types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
            orig = sv.JOURNAL
            try:
                sv.JOURNAL = journal
                with patch.object(sv.subprocess, "run", return_value=fake):
                    self.assertEqual(sv.core_pressure(), 0.0)
            finally:
                sv.JOURNAL = orig
            rows = read_jsonl(journal)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "probe_fault",
                             "distinct kind so it cannot pollute breaker faults")
            self.assertIn("boom", rows[0]["reason"])

    def test_missing_pressure_line_is_journalled(self):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            journal = pathlib.Path(tmp) / "journal.jsonl"
            fake = types.SimpleNamespace(returncode=0, stdout="nothing here\n",
                                         stderr="")
            orig = sv.JOURNAL
            try:
                sv.JOURNAL = journal
                with patch.object(sv.subprocess, "run", return_value=fake):
                    sv.core_pressure()
            finally:
                sv.JOURNAL = orig
            rows = read_jsonl(journal)
            self.assertEqual(len(rows), 1)
            self.assertIn("no 'core pressure' line", rows[0]["reason"])


class TestSoilCommitsItsBookkeeping(unittest.TestCase):
    """reflect and _auto_promote write tracked files in the MAIN worktree.

    Nothing committed them, so `git merge --ff-only <candidate>` refused with
    'local changes would be overwritten' and the beat died with an approved
    candidate stranded. It killed a 14-beat run at beat 5 and the keeper's
    run at beat 1 -- the loop could not survive its own autonomy path.
    """

    def _sv(self):
        sys.path.insert(0, str(REPO / "substrate"))
        import supervisor as sv  # noqa: E402
        return sv

    def _repo(self, tmp):
        import subprocess as sp
        d = pathlib.Path(tmp)
        sp.run(["git", "init", "-q", str(d)], check=True)
        for name in ("user.email", "user.name"):
            sp.run(["git", "-C", str(d), "config", name, "t@t"], check=True)
        (d / "control").mkdir()
        (d / "state").mkdir()
        (d / "control" / "agenda.md").write_text("# Agenda\n", encoding="utf-8")
        (d / "state" / "proposals.md").write_text("# Proposals\n", encoding="utf-8")
        sp.run(["git", "-C", str(d), "add", "-A"], check=True)
        sp.run(["git", "-C", str(d), "commit", "-qm", "init"], check=True)
        return d

    def test_dirty_state_is_committed(self):
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            d = self._repo(tmp)
            (d / "control" / "agenda.md").write_text("# Agenda\n- [ ] t\n",
                                                     encoding="utf-8")
            orig = sv.REPO
            try:
                sv.REPO = d
                sv._commit_state("test")
                import subprocess as sp
                dirty = sp.run(["git", "-C", str(d), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
                self.assertEqual(dirty, "", "tree must be clean after commit")
                log = sp.run(["git", "-C", str(d), "log", "--oneline", "-1"],
                             capture_output=True, text=True).stdout
                self.assertIn("soil: test", log)
            finally:
                sv.REPO = orig

    def test_clean_tree_makes_no_commit(self):
        """An empty commit every beat would bury the biography in noise."""
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            d = self._repo(tmp)
            import subprocess as sp
            before = sp.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
            orig = sv.REPO
            try:
                sv.REPO = d
                sv._commit_state("noop")
            finally:
                sv.REPO = orig
            after = sp.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
            self.assertEqual(before, after)

    def test_missing_register_files_do_not_break_it(self):
        """STATE_FILES names registers a young repo may not have yet."""
        sv = self._sv()
        with tempfile.TemporaryDirectory() as tmp:
            d = self._repo(tmp)  # has no gaps.md / patterns.md / backlog.md
            (d / "state" / "proposals.md").write_text("x\n", encoding="utf-8")
            orig = sv.REPO
            try:
                sv.REPO = d
                sv._commit_state("partial")
            finally:
                sv.REPO = orig

    def test_commit_happens_before_the_cycle_not_after(self):
        """Ordering IS the fix: a commit after the cycle branched moves HEAD
        out from under the candidate, and the ff-merge fails on ancestry
        instead of on a dirty tree."""
        source = (REPO / "substrate" / "supervisor.py").read_text(encoding="utf-8")
        body = source[source.index("def heartbeat("):]
        commit_at = body.index("_commit_state(f\"beat")
        spawn_at = body.index("subprocess.run([sys.executable, \"-m\", \"meristem.loop\", *argv]")
        promote_at = body.index("promote()", spawn_at)
        self.assertLess(commit_at, spawn_at, "must commit before spawning work")
        self.assertLess(spawn_at, promote_at, "promote comes after the work")


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
        self.assertFalse(sv._is_guarded_proposal(also_review),
                         "the gates seat is rung 2: the panel judges gate changes")
        self.assertEqual(loop.route_proposal(also_review), "agenda")
        soil = naming_home + " And substrate/supervisor.py."
        self.assertTrue(sv._is_guarded_proposal(soil),
                        "the soil is a capability boundary, not a seat, and does not move")
        self.assertEqual(loop.route_proposal(soil), "mailbox")

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


class TestProbePromotion(unittest.TestCase):
    """The staged-proposal -> vault path (P-062).

    control/probe-protocol.md documents it; until 2026-08-20 only the seed's
    half existed. Eight proposals were staged and two probes were live, both
    placed by hand at birth, so no probe the seed ever wrote had scored.
    """

    def _stage(self, root, pid, body, statement="check the thing"):
        d = root / "state" / "probe-proposals" / pid
        (d / "rubric").mkdir(parents=True)
        (d / "statement").mkdir(parents=True)
        (d / "probe.json").write_text(
            json.dumps({"id": pid, "capability_domain": "test"}), encoding="utf-8")
        (d / "statement" / "task.md").write_text(statement, encoding="utf-8")
        (d / "rubric" / "check.py").write_text(body, encoding="utf-8")
        return d

    def _run(self, root):
        """Paths go in as ARGUMENTS.

        The first version of this helper monkeypatched sup.VAULT and friends,
        which is the only way to test a function that reads module globals --
        and on 2026-08-20 a dry run written the same way put six probes into
        the real vault with no journal record. JOURNAL still needs patching
        because _journal() is shared with the rest of the supervisor; the
        paths this function OWNS are now parameters.
        """
        import importlib
        sup = importlib.import_module("substrate.supervisor")
        old_journal = sup.JOURNAL
        sup.JOURNAL = root / "state" / "journal.jsonl"
        try:
            return sup.promote_probes(vault=root / "vault",
                                      staging=root / "state" / "probe-proposals",
                                      workdir=root)
        finally:
            sup.JOURNAL = old_journal

    GOOD = "import json,sys; sys.stdin.read(); print(json.dumps({'score': 42.0}))"

    def test_valid_proposal_reaches_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            self._stage(root, "probe-good", self.GOOD)
            self.assertEqual(self._run(root), 1)
            landed = root / "vault" / "internal" / "active" / "probe-good"
            self.assertTrue((landed / "rubric" / "check.py").is_file())
            self.assertTrue(json.loads(
                (landed / "probe.json").read_text(encoding="utf-8"))["frozen"])
            self.assertFalse((root / "state" / "probe-proposals" / "probe-good").exists(),
                             "staging must be cleared: staged-and-promoted is not a state")

    def test_a_declarative_proposal_is_promoted_not_refused(self):
        """No check.py is a FORM, not a defect.

        probe-word-count-basic and probe-text-stats-basic have sat in the
        vault since birth with no executable rubric, and run_probe answers
        that case by name: "no executable rubric; probe is declarative only".
        P-062 demanded check.py because all eight staged proposals had one --
        a contract induced from the sample instead of from the container --
        and it then refused the first proposal that obeyed P-030 by keeping
        its scoring logic out of the repository entirely.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            d = self._stage(root, "probe-declarative", self.GOOD)
            (d / "rubric" / "check.py").unlink()
            self.assertEqual(self._run(root), 1)
            landed = root / "vault" / "internal" / "active" / "probe-declarative"
            self.assertTrue(landed.is_dir())
            rows = [json.loads(l) for l in
                    (root / "state" / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l]
            rec = [r for r in rows if r.get("kind") == "probe_promoted"][0]
            self.assertTrue(rec["declarative"])
            self.assertIsNone(rec["sha256"], "there is no rubric to fingerprint")
            self.assertEqual(rec["score"], 0.0, "declarative probes gate nothing")

    def test_a_proposal_with_neither_rubric_nor_statement_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            d = self._stage(root, "probe-empty", self.GOOD)
            (d / "rubric" / "check.py").unlink()
            (d / "statement" / "task.md").unlink()
            self.assertEqual(self._run(root), 0)
            self.assertFalse((root / "vault" / "internal" / "active" / "probe-empty").exists())
            rows = [json.loads(l) for l in
                    (root / "state" / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l]
            refused = [r for r in rows if r.get("kind") == "probe_refused"]
            self.assertEqual(len(refused), 1)
            self.assertIn("statement", refused[0]["reason"])

    def test_a_score_that_depends_on_where_it_lives_is_refused(self):
        """A rubric reading its own cwd scores differently once it moves.

        Such a probe is not frozen -- the first thing that moved it would read
        as a capability regression -- so it must not enter the vault at all.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            self._stage(root, "probe-wandering",
                        "import json,os,sys; sys.stdin.read(); "
                        "print(json.dumps({'score': 1.0 if 'vault' in os.getcwd() else 0.0}))")
            self.assertEqual(self._run(root), 0)
            self.assertFalse((root / "vault" / "internal" / "active" / "probe-wandering").exists(),
                             "a half-copied probe must be removed again")
            rows = [json.loads(l) for l in
                    (root / "state" / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l]
            self.assertIn("location-dependent",
                          [r.get("reason", "") for r in rows if r.get("kind") == "probe_refused"][0])

    def test_promotion_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            self._stage(root, "probe-once", self.GOOD)
            self.assertEqual(self._run(root), 1)
            self._stage(root, "probe-once", self.GOOD)
            self.assertEqual(self._run(root), 0, "already in the vault; must not re-promote")

    def test_explicit_paths_never_reach_the_live_vault(self):
        """The regression that made P-063 necessary.

        promote_probes once read module globals, so exercising it meant
        monkeypatching them, and a patch that did not hold wrote six probes
        into the real vault -- no journal row, no fingerprint, staging left
        behind. Passing the paths in makes that failure unrepresentable.
        """
        import importlib
        sup = importlib.import_module("substrate.supervisor")
        live_vault, live_staging = sup.VAULT, sup.PROBE_STAGING
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            self._stage(root, "probe-isolated", self.GOOD)
            self.assertEqual(self._run(root), 1)
            self.assertTrue((root / "vault" / "internal" / "active"
                             / "probe-isolated").is_dir())
        self.assertEqual((sup.VAULT, sup.PROBE_STAGING), (live_vault, live_staging),
                         "module globals must be untouched by a parameterised run")
        self.assertFalse((live_vault / "internal" / "active"
                          / "probe-isolated").exists(),
                         "a parameterised run must not reach the live vault")

    def test_zero_score_is_promoted_not_refused(self):
        """A probe measuring something that does not work yet scores 0.

        The probe gate fails on REGRESSION, never on an absolute score, and a
        probe with no history cannot fail anything. Refusing zeros would throw
        away exactly the probes that make a broken thing measurable.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "state").mkdir()
            self._stage(root, "probe-zero",
                        "import json,sys; sys.stdin.read(); print(json.dumps({'score': 0.0}))")
            self.assertEqual(self._run(root), 1)
            rows = [json.loads(l) for l in
                    (root / "state" / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l]
            rec = [r for r in rows if r.get("kind") == "probe_promoted"][0]
            self.assertEqual(rec["score"], 0.0)
            self.assertEqual(len(rec["sha256"]), 64, "the freeze is recorded as a hash")


class TestRollbackNoop(unittest.TestCase):
    """Three failures with nothing to roll back to must not need a human.

    All three keeper stops in this system's life came through one door:
    rollback() returned non-zero, the keeper read that as a broken heartbeat,
    and someone had to press start. But when HEAD already IS last-good there
    is nothing a revert could undo -- the tree is on a canary-proven commit
    and the failures are environmental. The keeper's own script says it:
    "stopping there would make a recoverable event need a human, which is the
    scaffolding this loop is meant to shed."
    """

    def _rollback(self, root, stamps_text=None):
        import importlib
        sup = importlib.import_module("substrate.supervisor")
        saved = (sup.REPO, sup.JOURNAL)
        stamps = root.parent / "keeper_rollbacks"
        if stamps_text is not None:
            stamps.write_text(stamps_text, encoding="utf-8")
        sup.REPO = root
        sup.JOURNAL = root / "state" / "journal.jsonl"
        try:
            with patch.object(sup, "resolve", return_value="abc123"), \
                 patch.object(sup, "git", return_value="abc123"), \
                 patch.object(sup, "notify"):
                return sup.rollback("3 consecutive beat failures")
        finally:
            sup.REPO, sup.JOURNAL = saved

    def _rows(self, root):
        p = root / "state" / "journal.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_first_noop_resumes_and_is_journalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "repo"
            (root / "state").mkdir(parents=True)
            self.assertEqual(self._rollback(root, stamps_text=""), 0,
                             "a first no-op rollback must not stop the keeper")
            rec = [r for r in self._rows(root) if r.get("kind") == "rollback_noop"]
            self.assertEqual(len(rec), 1, "the failure must still be recorded")
            self.assertEqual(rec[0]["prior_24h"], 0)

    def test_second_noop_inside_24h_stops(self):
        """Recoverable every time but never recovering is worse than stopping."""
        import time as _t
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "repo"
            (root / "state").mkdir(parents=True)
            recent = str(int(_t.time()) - 3600)
            self.assertEqual(self._rollback(root, stamps_text=recent + "\n"), 1,
                             "a second no-op inside 24h is systemic and must stop")
            rec = [r for r in self._rows(root) if r.get("kind") == "rollback_noop"]
            self.assertEqual(rec[0]["prior_24h"], 1)

    def test_stale_stamp_outside_the_window_does_not_stop(self):
        import time as _t
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "repo"
            (root / "state").mkdir(parents=True)
            old = str(int(_t.time()) - 86400 * 3)
            self.assertEqual(self._rollback(root, stamps_text=old + "\n"), 0,
                             "the window is 24h, not forever")
