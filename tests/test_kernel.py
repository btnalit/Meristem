#!/usr/bin/env python3
"""Kernel self-tests. Outside the LOC cap by constitution: these are how we
check the kernel, not the kernel itself.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from meristem import REPO as KERNEL_REPO, VAULT, append_jsonl, read_jsonl  # noqa: E402
from meristem import engine, germline, journal as journal_mod, ledger, llm  # noqa: E402
from meristem.gates import closure, deterministic, germline_validate, probes, review  # noqa: E402
from meristem import loop  # noqa: E402
from meristem import breaker  # noqa: E402


class TestBudgets(unittest.TestCase):
    def test_kernel_within_cap(self):
        loc = deterministic.kernel_loc()
        self.assertLessEqual(loc, deterministic.KERNEL_LOC_CAP, f"kernel is {loc} lines")

    def test_vault_is_outside_repo(self):
        self.assertFalse(VAULT.is_relative_to(KERNEL_REPO),
                         "vault inside the repo would leak rubrics to the engine")


class TestImmuneSelfTest(unittest.TestCase):
    def test_golden_fixtures_all_reject(self):
        """The immune system's own immune test. Empty means every canned bad
        change was correctly refused."""
        self.assertEqual(loop.golden_fixtures(), [])

    def test_protected_paths_refused(self):
        for path in ("root/panic.py", "substrate/supervisor.py"):
            self.assertFalse(deterministic.run([path]).passed, f"{path} was allowed")

    def test_secret_is_caught(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write('TOKEN = "sk-' + "b" * 40 + '"\n')
            path = pathlib.Path(handle.name)
        try:
            self.assertTrue(deterministic.scan_secrets([path]))
        finally:
            path.unlink(missing_ok=True)

    def test_understated_closure_refused(self):
        self.assertFalse(deterministic.run([], declared_closure=1).passed)

    def test_memory_erasure_refused(self):
        """Registers may gain entries; they may never lose them.

        Tier A rewrites whole files, so 'add an entry' degrades naturally into
        'replace the file with one entry' -- this actually happened in cycle 4.
        """
        register = KERNEL_REPO / "state" / "patterns.md"
        original = register.read_text(encoding="utf-8")
        try:
            register.write_text("# Pattern Register\n\n## Z-999 \u2014 only\n",
                                encoding="utf-8")
            problems = deterministic.memory_integrity(["state/patterns.md"])
            self.assertTrue(problems, "erasing six pattern entries was allowed")
        finally:
            register.write_text(original, encoding="utf-8")

    def test_memory_edit_within_entry_allowed(self):
        """Editing an entry's body is legitimate; only losing the entry is not."""
        register = KERNEL_REPO / "state" / "patterns.md"
        original = register.read_text(encoding="utf-8")
        try:
            register.write_text(original + "\n\nAn appended clarification.\n",
                                encoding="utf-8")
            self.assertEqual(deterministic.memory_integrity(["state/patterns.md"]), [])
        finally:
            register.write_text(original, encoding="utf-8")

    def test_memory_integrity_compares_against_the_pre_change_state(self):
        """P-013: the loop commits the mutation before the gates run, so in
        the candidate tree HEAD IS the change. A check that reads HEAD as
        'before' compares the change with itself and never finds a loss."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp)
            run = lambda *a: subprocess.run(["git", *a], cwd=str(tree),
                                            capture_output=True, text=True)
            run("init", "-q")
            run("config", "user.email", "t@t")
            run("config", "user.name", "t")
            (tree / "state").mkdir()
            register = tree / "state" / "reg.md"
            register.write_text("## G-001 \u2014 one\n\n## G-002 \u2014 two\n", encoding="utf-8")
            run("add", "-A")
            run("commit", "-q", "-m", "base")
            # The mutation erases an entry, and is COMMITTED (as the loop does).
            register.write_text("## G-009 \u2014 only\n", encoding="utf-8")
            run("add", "-A")
            run("commit", "-q", "-m", "mutation")

            # Reading HEAD as 'before' sees nothing wrong: the bug.
            self.assertEqual(
                deterministic.memory_integrity(["state/reg.md"], tree, "HEAD"), []
            )
            # Naming the pre-change state catches the erasure.
            problems = deterministic.memory_integrity(["state/reg.md"], tree, "HEAD~1")
            self.assertTrue(problems, "erasure invisible even against HEAD~1")
            self.assertIn("G-001", problems[0])

    def test_gates_inspect_the_tree_they_are_given(self):
        """P-009: a gate that reads a path constant instead of its subject
        inspects the current checkout and passes everything. Build a fake tree
        containing a violation and require the gate to see it THERE."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = pathlib.Path(tmp)
            (fake / "meristem" / "gates").mkdir(parents=True)
            (fake / "control").mkdir()
            (fake / "meristem" / "__init__.py").write_text("", encoding="utf-8")
            # A vault reference in ordinary kernel code: must be caught.
            (fake / "meristem" / "leaky.py").write_text(
                'VAULT_PATH = "meristem-vault"\n', encoding="utf-8")
            offenders = deterministic.vault_reference_invariant(fake)
            self.assertTrue(offenders, "gate did not inspect the tree it was given")
            self.assertIn("leaky.py", offenders[0])
            # And the real tree must still be clean.
            self.assertEqual(deterministic.vault_reference_invariant(KERNEL_REPO), [])

    def test_kernel_loc_counts_the_given_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = pathlib.Path(tmp)
            (fake / "meristem").mkdir(parents=True)
            (fake / "meristem" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
            self.assertEqual(deterministic.kernel_loc(fake), 2)

    def test_vault_reference_invariant_holds(self):
        """Only gates/ may name the vault; __init__ owns the one definition."""
        self.assertEqual(deterministic.vault_reference_invariant(), [])


class TestBreakerDistinguishesFaults(unittest.TestCase):
    """P-016: a fault and a judged rejection both land as outcome 'rejected'
    but mean opposite things. Counting them together parks a task the gates
    never objected to."""

    def setUp(self):
        self.rows = []
        self._real = breaker.read_jsonl
        breaker.read_jsonl = lambda _p: self.rows

    def tearDown(self):
        breaker.read_jsonl = self._real

    def test_faults_do_not_count_as_rejections(self):
        task = "a task the mechanism could not express"
        for cycle in (1, 2, 3):
            self.rows.append({"kind": "cycle", "cycle": cycle, "why": task,
                              "outcome": "rejected"})
            self.rows.append({"kind": "fault", "cycle": cycle, "error": "unparseable"})
        self.assertEqual(breaker.rejections_for(task), 0)
        self.assertEqual(breaker.faults_for(task), 3)
        self.assertFalse(breaker.should_park(task),
                         "parked a task no gate ever refused")

    def test_judged_rejections_still_park(self):
        task = "a task the gates refused"
        for cycle in (1, 2, 3):
            self.rows.append({"kind": "cycle", "cycle": cycle, "why": task,
                              "outcome": "rejected"})
        self.assertEqual(breaker.rejections_for(task), 3)
        self.assertTrue(breaker.should_park(task))

    def test_persistent_faults_eventually_park(self):
        """Patience for faults is looser, not unlimited: a task the mechanism
        can never express is also worth setting aside."""
        task = "a task that always faults"
        for cycle in range(1, 7):
            self.rows.append({"kind": "cycle", "cycle": cycle, "why": task,
                              "outcome": "rejected"})
            self.rows.append({"kind": "fault", "cycle": cycle, "error": "boom"})
        self.assertTrue(breaker.should_park(task))


class TestProposalGuard(unittest.TestCase):
    """Reflect is a new way for model output to reach the work queue, so it
    needs the fence the mutation path already has. The very first real reflect
    proposed a change to substrate/ -- a sound observation, and exactly the
    thing that must not become a route to acting on it."""

    def test_guarded_ground_routes_to_mailbox(self):
        for path in ("substrate/supervisor.py", "root/panic.py",
                     "meristem/gates/review.py", "control/constitution.md"):
            self.assertEqual(
                loop.route_proposal(f"Fix the thing in {path} so it behaves"),
                "mailbox", f"{path} was not held for review")

    def test_ordinary_proposal_reaches_the_queue(self):
        self.assertEqual(
            loop.route_proposal("Add a utility command to meristem/loop.py"),
            "agenda")
        self.assertEqual(
            loop.route_proposal("Grow an organ at body/organs/summarise/"),
            "agenda")


class TestCapGovernance(unittest.TestCase):
    """The budget may move; it may not move unargued.

    v3.1 6.1: every human gate carries demotion criteria, so the APPROVAL SEAT
    on a cap change is a ladder position and will demote on evidence. What does
    not demote is the requirement that the change be argued -- that enforces
    monotonicity, not the human.
    """

    def test_unargued_cap_change_is_incomplete(self):
        self.assertTrue(loop.cap_case_missing("raise KERNEL_LOC_CAP to 6000"))

    def test_complete_case_passes_the_format_check(self):
        case = (
            "Per-file LOC: loop.py 757, gates 500. Core pressure 0.88, "
            "closure pressure 0.65. Already externalized the view commands and "
            "pruned two helpers; insufficient because the loop machinery itself "
            "is irreducible. Proposed new cap 3400. Expected closure impact: "
            "none, closure stays at 0.65."
        )
        self.assertEqual(loop.cap_case_missing(case), [])

    def test_unargued_cap_change_is_refused_not_queued(self):
        """The invariant that never demotes: unargued means it never runs.

        The seat moved to rung 2 on 2026-08-17, so a COMPLETE case now earns
        the review panel instead of a human. An incomplete one is refused
        deterministically and must never reach the work queue by any route.
        """
        for text in ("raise the cap to 4000",
                     "lower the cap after externalizing",
                     "adjust KERNEL_LOC_CAP",
                     "提高上限到4000"):
            self.assertEqual(loop.route_proposal(text), "refused", text)

    def test_complete_case_reaches_the_review_panel(self):
        case = (
            "Per-file LOC: loop.py 757, gates 500. Core pressure 0.88, "
            "closure pressure 0.65. Already externalized the view commands and "
            "pruned two helpers; insufficient because the loop machinery itself "
            "is irreducible. Proposed new cap 3400. Expected closure impact: "
            "none, closure stays at 0.65."
        )
        self.assertEqual(loop.route_proposal(case), "agenda")

    #: The only surviving text of the five proposals the bare \bcaps?\b guard
    #: ate (journal cycles 187, 194, 198, 199, 200). These are the journal's
    #: `why` fields, which store why[:200] -- the FULL proposals no longer
    #: exist anywhere, because "refused" meant dropped and nothing was kept.
    #: The bug under test destroyed the evidence needed to verify its own fix,
    #: so these truncations are the whole record. Every one is an
    #: EXTERNALIZATION: exactly the relief the pressure mandate had asked for.
    #:
    #: ZERO DISCRIMINATING POWER, on purpose kept anyway. Not one surviving
    #: prefix contains "cap" -- the matching word lived in the tail that was
    #: dropped -- so these pass under the old regex too. They are a record of
    #: what was destroyed, not armour. The armour is RELIEF_PHRASINGS and
    #: golden fixture 7c. A corpus that cannot even reproduce the bug it
    #: documents is the sharpest possible statement of how completely the
    #: refusal path erased its own evidence.
    EATEN = (
        "Externalize meristem/journal.py (306 lines) into body/organs/journal/ "
        "as a self-contained persistence organ with a clean read/write/query "
        "interface. This brings core from 2865 to ~2559, freeing 441 li",
        "Externalize the task-scheduling and failure-aggregation logic from "
        "meristem/loop.py (787 LOC, the largest file) into a new organ under "
        "body/organs/task-scheduler/. This organ would own: (a) the circui",
        "Externalize failure aggregation and pattern-detection logic from "
        "loop.py (817 LOC, the single largest file) into a new organ "
        "`body/organs/failure-aggregator/`. This organ would own: (a) querying the j",
        "Externalize the failure-history aggregation and task-scheduling logic "
        "from loop.py (817 lines, the single largest file) into a new organ at "
        "body/organs/task-scheduler/. This chunk — failure_history(),",
        "Externalize the failure-history aggregation and retry/circuit-breaker "
        "logic from loop.py (817 LOC, the single largest file) into a new organ "
        "at body/organs/task-lifecycle/. The organ would own: (a) pe",
    )

    #: Relief phrasings that MUST survive. The mandate's own vocabulary for
    #: options 1-3 is "move capability into an organ" and "a new organ", and a
    #: proposal naturally cites the budget it is trying to get under.
    RELIEF_PHRASINGS = (
        "Externalize aggregation into a new organ to stay under the cap.",
        "Move capability into an organ to bring loop.py under the cap of 3000.",
        "Keeping loop.py below the cap at 3000 lines by growing an organ.",
        "Externalize into a new organ; this brings core from 2865 to ~2559.",
        # Load-bearing: these two sit INSIDE the 20-char window, so putting
        # "new" or "move" back into the verb list turns them red. Without
        # them the exclusion lives only in a comment, and the nearest other
        # phrasing here clears the window by 25 characters -- the same cliff
        # edge this fix was rejected for the first time around.
        "a new organ under the cap",      # new -> cap: 17 chars
        "move it back under the cap",     # move -> cap: 14 chars
    )

    def test_relief_proposals_are_not_eaten_by_the_cap_guard(self):
        """The mandate asks for relief; the guard must not destroy the answer.

        Five consecutive externalization proposals were read as incomplete cap
        cases and dropped, so the queue stayed empty, pressure stayed high, and
        reflect ran again -- a livelock built entirely out of my own guard. The
        widening was priced when a false positive cost one held mailbox line;
        P-040 changed the price to 'refused and dropped' without repricing it.
        """
        for text in self.EATEN:
            self.assertEqual(loop.route_proposal(text), "agenda", text[:70])
            self.assertFalse(loop.mentions_cap_change(text), text[:70])

    def test_relief_vocabulary_is_not_mistaken_for_cap_intent(self):
        """The mandate teaches "a new organ" and "move capability into an
        organ"; a guard that reads those as budget requests eats the answers
        to its own question. These are adversarial, not the eaten corpus:
        the corpus only proves the old bug, not that the fix has margin."""
        for text in self.RELIEF_PHRASINGS:
            self.assertFalse(loop.mentions_cap_change(text), text)
            self.assertEqual(loop.route_proposal(text), "agenda", text)

    def test_real_cap_intents_are_still_caught(self):
        for text in ("raise the cap to 4000",
                     "adjust KERNEL_LOC_CAP",
                     "提高上限到4000",
                     "increase the cap because loop.py keeps growing",
                     "set the cap at 3400",
                     "Proposed new cap 3400",
                     "change the kernel line cap",
                     "lower the cap after externalizing the reporter"):
            self.assertTrue(loop.mentions_cap_change(text), text)

    def test_complete_case_may_name_the_file_holding_the_budget(self):
        """Otherwise the rung-2 seat is decorative.

        KERNEL_LOC_CAP lives in meristem/gates/deterministic.py, which is
        guarded ground. A case must name it to say what it wants changed, so
        without this exemption every realistic cap case routes to a human --
        the exact outcome the seat was promoted to remove.
        """
        case = (
            "Raise the cap. Per-file LOC: loop.py 850, gates 245. Core "
            "pressure 0.98, closure pressure 0.65. Already externalized the "
            "report formatter; insufficient. Proposed new cap 3300 in "
            "meristem/gates/deterministic.py. Expected closure impact: none."
        )
        self.assertEqual(loop.route_proposal(case), "agenda")

    def test_the_exemption_is_one_path_wide(self):
        """Naming the budget file does not license naming another gate."""
        case = (
            "Raise the cap. Per-file LOC: loop.py 850. Core pressure 0.98, "
            "closure pressure 0.65. Already externalized views; insufficient. "
            "Proposed new cap 3300 in meristem/gates/deterministic.py. "
            "Expected closure impact: none. Also relax meristem/gates/review.py."
        )
        self.assertEqual(loop.route_proposal(case), "mailbox")

    def test_guarded_ground_still_outranks_a_cap_case(self):
        """A complete case that also names guarded ground stays human-held.

        Otherwise 'raise the cap and edit substrate/supervisor.py' would buy
        its way past the root-of-trust fence with six magic phrases.
        """
        case = (
            "Per-file LOC: loop.py 757. Core pressure 0.88, closure pressure "
            "0.65. Already externalized views; insufficient. Proposed new cap "
            "3400. Expected closure impact: none. Also edit "
            "substrate/supervisor.py."
        )
        self.assertEqual(loop.route_proposal(case), "mailbox")

    def test_shrinking_is_argued_too(self):
        """Lines removed by deleting a check are not lines saved -- a shrink
        can be a weakening in the costume of metabolism."""
        self.assertTrue(loop.mentions_cap_change("lower the cap to 2000"))


class TestPressureMandateSeam(unittest.TestCase):
    """P-018 again, and mine this time: the soil invoked `reflect --pressure`
    before the kernel accepted that flag, so the first firing of the pressure
    trigger died on argparse. A seam between soil and seed is still a seam."""

    def test_reflect_accepts_the_pressure_flag(self):
        import inspect
        self.assertIn("pressure", inspect.signature(loop.run_reflect).parameters)

    def test_cli_parses_pressure_flag(self):
        import contextlib, io
        # argparse exits non-zero on an unknown flag; a clean parse is the test.
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            try:
                loop.main(["reflect", "--pressure", "--help"])
            except SystemExit as exc:
                self.assertEqual(exc.code, 0, buf.getvalue()[:200])

    def test_mandate_ranks_the_cap_last(self):
        """The ranked menu is constitutional, so the prompt must state it --
        and must place the cap last, not merely mention it."""
        ask = loop.PRESSURE_MANDATE_ASK
        self.assertLess(ask.index("externalize"), ask.index("change the cap"))
        # A cap proposal must be told what a complete case requires, or the
        # deterministic format check refuses it before anyone reads it.
        for element in ("per-file LOC", "closure pressure", "expected closure"):
            self.assertIn(element, ask)


class TestClosureExcludesTests(unittest.TestCase):
    """v3.1 1.3 excludes tests from the budget: they are how we CHECK the
    kernel, not the kernel itself. Counting them made this very file -- 1,280
    lines, 49,668 tokens alone -- eat almost the whole closure budget, so any
    change that also touched a test was refused for a reason unrelated to the
    change (P-022)."""

    def test_touching_a_test_does_not_grow_the_closure(self):
        base = closure.compute(["meristem/breaker.py"]).tokens
        with_test = closure.compute(
            ["meristem/breaker.py", "tests/test_kernel.py"]).tokens
        self.assertEqual(base, with_test)

    def test_a_test_only_change_stays_within_budget(self):
        self.assertTrue(closure.compute(["tests/test_kernel.py"]).fits)


class TestGermline(unittest.TestCase):
    def test_incomplete_manifest_rejected(self):
        self.assertTrue(germline_validate.validate({"id": "x"}, "x"))

    def test_complete_manifest_accepted(self):
        manifest = {
            "id": "demo", "version": "1", "capability": "counts words",
            "entrypoint": ["python", "main.py"],
            "input_schema": {"text": "string"}, "output_schema": {"words": "integer"},
            "dependencies": [], "probes": ["p1"], "metrics": ["usage"],
            "lifecycle": "candidate",
        }
        self.assertEqual(germline_validate.validate(manifest, "demo"), [])

    def test_active_organ_needs_probes(self):
        manifest = {
            "id": "demo", "version": "1", "capability": "c",
            "entrypoint": ["python", "main.py"],
            "input_schema": {}, "output_schema": {},
            "dependencies": [], "probes": [], "metrics": [], "lifecycle": "active",
        }
        problems = germline_validate.validate(manifest, "demo")
        self.assertTrue(any("probes" in p for p in problems))

    def test_lifecycle_cannot_skip(self):
        self.assertEqual(
            germline.LIFECYCLE,
            ("candidate", "calibrate", "register", "active", "deprecating", "archive"),
        )

    def test_body_manifests_are_admissible(self):
        """whatever the body currently holds must satisfy the germline.

        This asserted an EMPTY body originally -- true at birth, when body/
        shipped with zero scaffolding. Organs have since grown, so the
        invariant worth keeping is not 'nothing is there' but 'everything
        there is legitimate'.
        """
        self.assertEqual(deterministic.organ_manifests(), [])

    def test_calibrate_requires_its_probes_to_exist(self):
        """Calibrate means 'score this against its probes', so a stage that
        cannot be performed must not be entered.

        Checking only at register let an organ reach calibrate naming a probe
        nobody had written -- Loop B's measuring-stick-first discipline was
        documented but not enforced, and the probe library stayed at one entry
        for thirty cycles.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp)
            organ = tree / "body" / "organs" / "ghost"
            organ.mkdir(parents=True)
            (organ / "organ.json").write_text(json.dumps({
                "id": "ghost", "version": "1", "capability": "c",
                "entrypoint": ["python3", "main.py"],
                "input_schema": {}, "output_schema": {},
                "dependencies": [], "probes": ["probe-that-was-never-written"],
                "metrics": ["usage"], "lifecycle": "calibrate",
            }), encoding="utf-8")
            problems = deterministic.organ_manifests(tree)
            self.assertTrue(any("do not exist" in p for p in problems),
                            "calibrate accepted an organ naming a phantom probe")


class TestGermlineInvoke(unittest.TestCase):
    def test_invoke_records_organ_call_in_journal(self):
        """invoke() must append a journal record with kind 'organ_call',
        the caller, and the callee -- so organ-to-organ edges become
        observable in the closure calculator rather than assumed."""
        from unittest.mock import patch

        organs_dir = KERNEL_REPO / "body" / "organs"
        organs_dir.mkdir(parents=True, exist_ok=True)
        test_dir = organs_dir / "_test_invoke"
        test_dir.mkdir(exist_ok=True)
        manifest = {
            "id": "_test_invoke",
            "version": "1",
            "capability": "test organ for invoke journal recording",
            "entrypoint": [sys.executable, "-c",
                           "import json,sys; print(json.dumps({'ok': True}))"],
            "input_schema": {},
            "output_schema": {},
            "dependencies": [],
            "probes": ["p1"],
            "metrics": [],
            "lifecycle": "active",
        }
        manifest_path = test_dir / "organ.json"
        created = False
        try:
            if not manifest_path.exists():
                created = True
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            recorded = []
            with patch("meristem.germline.append_jsonl",
                       side_effect=lambda path, rec: recorded.append((path, rec))):
                result = germline.invoke("_test_invoke", {}, caller="test_caller", cycle=42)

            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(recorded), 1)
            _, record = recorded[0]
            self.assertEqual(record["kind"], "organ_call")
            self.assertEqual(record["caller"], "test_caller")
            self.assertEqual(record["callee"], "_test_invoke")
            self.assertEqual(record["success"], True)
            self.assertEqual(record["cycle"], 42)
        finally:
            if created:
                manifest_path.unlink(missing_ok=True)
                test_dir.rmdir()

    def test_invoke_records_failure_on_exception(self):
        """When the organ process fails, invoke must still write a record
        with success=False."""
        from unittest.mock import patch

        organs_dir = KERNEL_REPO / "body" / "organs"
        organs_dir.mkdir(parents=True, exist_ok=True)
        test_dir = organs_dir / "_test_invoke_fail"
        test_dir.mkdir(exist_ok=True)
        manifest = {
            "id": "_test_invoke_fail",
            "version": "1",
            "capability": "test organ that fails",
            "entrypoint": [sys.executable, "-c",
                           "import sys; sys.exit(1)"],
            "input_schema": {},
            "output_schema": {},
            "dependencies": [],
            "probes": ["p1"],
            "metrics": [],
            "lifecycle": "active",
        }
        manifest_path = test_dir / "organ.json"
        created = False
        try:
            if not manifest_path.exists():
                created = True
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            recorded = []
            with patch("meristem.germline.append_jsonl",
                       side_effect=lambda path, rec: recorded.append((path, rec))):
                with self.assertRaises(Exception):
                    germline.invoke("_test_invoke_fail", {}, caller="test", cycle=1)

            self.assertEqual(len(recorded), 1)
            _, record = recorded[0]
            self.assertEqual(record["kind"], "organ_call")
            self.assertEqual(record["success"], False)
        finally:
            if created:
                manifest_path.unlink(missing_ok=True)
                test_dir.rmdir()

    def test_invoke_default_caller_is_kernel(self):
        """When no caller is specified, it defaults to 'kernel'."""
        from unittest.mock import patch

        organs_dir = KERNEL_REPO / "body" / "organs"
        organs_dir.mkdir(parents=True, exist_ok=True)
        test_dir = organs_dir / "_test_invoke_def"
        test_dir.mkdir(exist_ok=True)
        manifest = {
            "id": "_test_invoke_def",
            "version": "1",
            "capability": "test organ for default caller",
            "entrypoint": [sys.executable, "-c",
                           "import json,sys; print(json.dumps({'ok': True}))"],
            "input_schema": {},
            "output_schema": {},
            "dependencies": [],
            "probes": ["p1"],
            "metrics": [],
            "lifecycle": "active",
        }
        manifest_path = test_dir / "organ.json"
        created = False
        try:
            if not manifest_path.exists():
                created = True
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            recorded = []
            with patch("meristem.germline.append_jsonl",
                       side_effect=lambda path, rec: recorded.append((path, rec))):
                germline.invoke("_test_invoke_def", {})

            self.assertEqual(len(recorded), 1)
            _, record = recorded[0]
            self.assertEqual(record["caller"], "kernel")
        finally:
            if created:
                manifest_path.unlink(missing_ok=True)
                test_dir.rmdir()


class TestClosure(unittest.TestCase):
    def test_kernel_always_in_closure(self):
        result = closure.compute([])
        self.assertTrue(any("meristem/loop.py" in f for f in result.files))

    def test_closure_fits_one_context(self):
        result = closure.compute([])
        self.assertTrue(result.fits, f"closure is ~{result.tokens} tokens")


class TestEngine(unittest.TestCase):
    def test_soil_excluded_from_mutable_surface(self):
        files = engine.mutable_files()
        self.assertFalse([f for f in files if f.startswith(("root/", "substrate/"))])

    def test_kernel_included_in_mutable_surface(self):
        self.assertIn("meristem/loop.py", engine.mutable_files())

    def test_a_named_organ_is_visible_to_the_author(self):
        """P-067: the engine could write body/ and was shown none of it.

        EXCLUDED_DIRS held "body" while _validate_paths refused only root/ and
        substrate/, so an organ repair was a blind rewrite. Cycles 377-381
        refused one such repair five times while the author reconstructed a
        twelve-class file from the reviewers' objections, landing closer each
        round and never right. The reviewers read the original as the '-' side
        of the diff; without this the author never reads it at all.

        There is no other guard on these four lines. The weakening rule covers
        what REVIEWERS can see, not what the AUTHOR can see, so a mutation that
        deletes them to reclaim kernel budget would pass review. This test is
        the only thing standing between the seed and re-blinding itself.
        """
        organs = engine.REPO / "body" / "organs"
        sources = sorted(organs.rglob("*.py"))
        if not sources:
            self.skipTest("no organs installed")
        target = sources[0]
        oid = target.relative_to(organs).parts[0]
        body = target.read_text(encoding="utf-8").splitlines()
        line = next(l for l in body if len(l.strip()) > 20)
        rel = target.relative_to(engine.REPO).as_posix()
        context = engine.build_context(f"REPAIR: fix the SyntaxError in {rel}")
        self.assertIn(f"=== FILE: {rel} ===", context,
                      f"task names organ {oid} and still cannot see its source")
        self.assertIn(line, context, "organ source is listed but not included verbatim")

    def test_body_stays_out_of_an_unrelated_context(self):
        """The whole point of naming: an organ costs prompt budget only when
        the task is about it. tests/ carries the same conditional (P-023)."""
        organs = engine.REPO / "body" / "organs"
        task = "Raise the deterministic ceiling per the governance ladder"
        named = [p.name for p in organs.iterdir() if p.is_dir() and p.name in task]
        self.assertFalse(named, f"the test string accidentally names {named}")
        self.assertNotIn("=== FILE: body/", engine.build_context(task))

    def test_parse_tolerates_fenced_json(self):
        self.assertEqual(engine._parse('```json\n{"a": 1}\n```'), {"a": 1})

    def test_parse_tolerates_trailing_commentary(self):
        """P-012: reasoning models append prose after the payload. The
        recovery path must not raise the error it exists to absorb."""
        self.assertEqual(
            engine._parse('{"a": 1}\n\nLet me know if you want changes!'), {"a": 1}
        )

    def test_parse_reports_unparseable_as_meristem_error(self):
        with self.assertRaises(Exception) as ctx:
            engine._parse("no json here at all")
        self.assertNotIsInstance(ctx.exception, json.JSONDecodeError)


class TestReview(unittest.TestCase):
    def test_unparseable_vote_is_reject(self):
        vote = review._parse("I think it looks fine!")
        self.assertEqual(vote["verdict"], "reject")
        self.assertTrue(vote["weakens_gate"])

    def test_weakening_flag_is_terminal(self):
        result = review.ReviewResult(votes=[
            {"verdict": "approve", "weakens_gate": False},
            {"verdict": "approve", "weakens_gate": True},
        ])
        self.assertTrue(result.weakening_flagged)

    def test_build_prompt_includes_changed_files_section(self):
        """build_prompt must include a clearly-labelled 'Changed files' section
        when changed_files is provided, so reviewers can distinguish the change
        from its context."""
        messages = review.build_prompt(
            diff="dummy diff",
            task="dummy task",
            closure_files=["meristem/loop.py", "meristem/gates/review.py"],
            changed_files=["meristem/gates/review.py"],
        )
        content = messages[1]["content"]
        self.assertIn("# Changed files", content)
        self.assertIn("meristem/gates/review.py", content)
        # The closure list must still be present.
        self.assertIn("# Review closure", content)
        self.assertIn("meristem/loop.py", content)

    def test_build_prompt_without_changed_files_still_has_closure(self):
        """When changed_files is None or empty, the closure list must still be
        present and no changed-files section should appear."""
        messages = review.build_prompt(
            diff="dummy diff",
            task="dummy task",
            closure_files=["meristem/loop.py"],
        )
        content = messages[1]["content"]
        self.assertIn("# Review closure", content)
        self.assertIn("meristem/loop.py", content)
        self.assertNotIn("# Changed files", content)


class TestProbeAlarm(unittest.TestCase):
    def test_matched_domain_divergence_fires(self):
        runs = [
            probes.ProbeRun("i1", 90.0, domain="coding", kind="internal"),
            probes.ProbeRun("a1", 50.0, domain="coding", kind="anchor"),
        ]
        alarm = probes.divergence_alarm(runs, {"i1": 70.0, "a1": 55.0})
        self.assertIn("divergence", alarm)

    def test_cross_domain_does_not_fire(self):
        """A routeros probe rising while a coding anchor is flat is not gaming.
        A fail-closed alarm that cries wolf trains humans to rubber-stamp."""
        runs = [
            probes.ProbeRun("i1", 90.0, domain="routeros", kind="internal"),
            probes.ProbeRun("a1", 50.0, domain="coding", kind="anchor"),
        ]
        self.assertEqual(probes.divergence_alarm(runs, {"i1": 70.0, "a1": 50.0}), "")


class TestLedger(unittest.TestCase):
    def test_budget_loads(self):
        budget = ledger.load_budget()
        self.assertGreater(budget.cycle_usd, 0)
        self.assertGreater(budget.campaign_usd, budget.cycle_usd)


class TestConfig(unittest.TestCase):
    def test_models_toml_has_no_secrets(self):
        text = (KERNEL_REPO / "control" / "models.toml").read_text(encoding="utf-8")
        self.assertEqual(deterministic.scan_secrets([KERNEL_REPO / "control" / "models.toml"]), [])
        self.assertIn("api_key_env", text)

    def test_review_has_multiple_slots(self):
        slots = llm.slots_for("review")
        self.assertGreaterEqual(len(slots), 2, "review needs failure independence")

    def test_review_slots_are_heterogeneous(self):
        """Independence is a property of model LINEAGE, not of endpoint.

        One gateway can front many families, so comparing base_url would call
        three siblings 'diverse'. What must differ is the training lineage --
        that is what makes their failures independent.
        """
        slots = llm.slots_for("review")
        lineages = {s.get("lineage") for s in slots}
        self.assertNotIn(None, lineages, "every reviewer slot must declare a lineage")
        self.assertGreater(
            len(lineages), 1, "reviewers of one lineage may fail together"
        )

    def test_mutator_lineage_is_not_a_reviewer(self):
        """The author's own family must not sit on the panel that judges it."""
        mutate = llm.slots_for("mutate")[0]
        reviewers = {s.get("lineage") for s in llm.slots_for("review")}
        self.assertNotIn(mutate.get("lineage"), reviewers)

    def test_call_budget_is_active(self):
        """A USD cap over an unpriced model is an inert gate; the call cap is
        what actually binds on a quota-limited endpoint."""
        budget = ledger.load_budget()
        self.assertGreater(budget.cycle_calls, 0)
        self.assertGreater(budget.campaign_calls, budget.cycle_calls)


class TestBodyCommand(unittest.TestCase):
    def test_body_command_runs_on_empty_body(self):
        """body/ ships empty; the command must handle that gracefully."""
        rc = loop.main(["body"])
        self.assertEqual(rc, 0)

    def test_body_command_uses_registry(self):
        """The body command and the closure calculator share one source of
        truth: germline.registry()."""
        organs = germline.registry()
        # body/ ships empty in P0, so this is a list (possibly empty).
        self.assertIsInstance(organs, list)

    def test_body_command_lists_organ_fields(self):
        """When an organ exists, the body command must surface id, version,
        lifecycle, and capability -- the four fields that make the body
        inspectable at a glance."""
        import io
        import contextlib

        organs_dir = KERNEL_REPO / "body" / "organs"
        organs_dir.mkdir(parents=True, exist_ok=True)
        test_dir = organs_dir / "_test_body_cmd"
        test_dir.mkdir(exist_ok=True)
        manifest = {
            "id": "_test_body_cmd",
            "version": "1",
            "capability": "test capability for body command",
            "entrypoint": ["python", "main.py"],
            "input_schema": {"text": "string"},
            "output_schema": {"words": "integer"},
            "dependencies": [],
            "probes": ["p1"],
            "metrics": ["usage"],
            "lifecycle": "candidate",
        }
        manifest_path = test_dir / "organ.json"
        original_content = None
        created = False
        try:
            if manifest_path.exists():
                original_content = manifest_path.read_text(encoding="utf-8")
            else:
                created = True
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = loop.main(["body"])
            output = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("_test_body_cmd", output)
            self.assertIn("candidate", output)
            self.assertIn("test capability for body command", output)
            self.assertIn("1", output)  # version
        finally:
            if created:
                manifest_path.unlink(missing_ok=True)
                test_dir.rmdir()
            elif original_content is not None:
                manifest_path.write_text(original_content, encoding="utf-8")


class TestSpendCommand(unittest.TestCase):
    def test_spend_command_groups_by_role_and_model(self):
        """The spend command reads only state/journal.jsonl and prints
        total calls and tokens grouped by role and by model."""
        import io
        import contextlib
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "usage", "role": "mutate", "model": "glm-5.2",
             "prompt_tokens": 1000, "completion_tokens": 500,
             "reasoning_tokens": 200},
            {"kind": "usage", "role": "review", "model": "deepseek-v4-flash",
             "prompt_tokens": 800, "completion_tokens": 300,
             "reasoning_tokens": 0},
            {"kind": "usage", "role": "mutate", "model": "glm-5.2",
             "prompt_tokens": 2000, "completion_tokens": 1000,
             "reasoning_tokens": 400},
            {"kind": "usage", "role": "review",
             "model": "sensenova-6.8-flash-lite",
             "prompt_tokens": 600, "completion_tokens": 200,
             "reasoning_tokens": 0},
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            buf = io.StringIO()
            with patch("meristem.loop.JOURNAL", tmp_path):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["spend"])
            output = buf.getvalue()
            self.assertEqual(rc, 0)
            # Total calls
            self.assertIn("4", output)
            # Role grouping: mutate appears with 2 calls
            self.assertIn("mutate", output)
            self.assertIn("review", output)
            # Model grouping
            self.assertIn("glm-5.2", output)
            self.assertIn("deepseek-v4-flash", output)
            self.assertIn("sensenova-6.8-flash-lite", output)
            # Total prompt tokens: 1000+800+2000+600 = 4400
            self.assertIn("4400", output)
            # Total tokens: 4400+500+300+1000+200+200+400+0 = 7000
            self.assertIn("7000", output)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_spend_command_on_empty_journal(self):
        """When no usage is recorded, the command handles it gracefully."""
        import io
        import contextlib
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        tmp.write("")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            buf = io.StringIO()
            with patch("meristem.loop.JOURNAL", tmp_path):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["spend"])
            self.assertEqual(rc, 0)
            self.assertIn("no usage", buf.getvalue().lower())
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_spend_command_ignores_non_usage_rows(self):
        """The spend command must filter to kind=='usage' and not count
        cycle, fault, or organ_call records."""
        import io
        import contextlib
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "cycle", "cycle": 1, "outcome": "rejected"},
            {"kind": "usage", "role": "mutate", "model": "glm-5.2",
             "prompt_tokens": 100, "completion_tokens": 50,
             "reasoning_tokens": 0},
            {"kind": "organ_call", "caller": "kernel", "callee": "x"},
            {"kind": "fault", "cycle": 2, "error": "boom"},
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            buf = io.StringIO()
            with patch("meristem.loop.JOURNAL", tmp_path):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["spend"])
            output = buf.getvalue()
            self.assertEqual(rc, 0)
            # Only 1 usage row, so total calls = 1
            self.assertIn("1", output)
            self.assertIn("mutate", output)
            self.assertIn("glm-5.2", output)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestUtilityCommand(unittest.TestCase):
    def test_utility_command_displays_organ_calls(self):
        """The utility command reads organ_call records from the journal
        and prints total invocations, successful invocations, and last used
        cycle per organ."""
        import io
        import contextlib
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
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
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            buf = io.StringIO()
            with patch("meristem.loop.JOURNAL", tmp_path):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["utility"])
            output = buf.getvalue()
            self.assertEqual(rc, 0)
            # Check word-count: 3 total, 3 successful, last cycle 4
            self.assertIn("word-count", output)
            self.assertIn("3", output)  # total calls
            self.assertIn("3", output)  # successful calls
            self.assertIn("4", output)  # last cycle
            # Check text-stats: 2 total, 1 successful, last cycle 5
            self.assertIn("text-stats", output)
            self.assertIn("2", output)  # total calls
            self.assertIn("1", output)  # successful calls
            self.assertIn("5", output)  # last cycle
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_utility_command_on_empty_journal(self):
        """When no organ calls have been recorded, the command handles it gracefully."""
        import io
        import contextlib
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        tmp.write("")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            buf = io.StringIO()
            with patch("meristem.loop.JOURNAL", tmp_path):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["utility"])
            self.assertEqual(rc, 0)
            self.assertIn("no organ calls", buf.getvalue().lower())
        finally:
            tmp_path.unlink(missing_ok=True)


class TestAppends(unittest.TestCase):
    def test_append_adds_without_erasing(self):
        """Appends must add text at the end of a file without erasing
        existing content. This is the structural fix for the class of
        failure where whole-file replacement erases a register."""
        with tempfile.TemporaryDirectory() as tmp:
            workdir = pathlib.Path(tmp)
            target = workdir / "state" / "gaps.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("## G-001 \u2014 first\n\n## G-002 \u2014 second\n",
                              encoding="utf-8")
            mutation = engine.Mutation(
                rationale="test",
                appends={"state/gaps.md": "\n## G-003 \u2014 third\n"},
            )
            # Appended paths must appear in changed so the closure
            # calculator and deterministic gates see them.
            self.assertIn("state/gaps.md", mutation.changed)
            written = engine.apply(mutation, workdir)
            self.assertIn("state/gaps.md", written)
            content = target.read_text(encoding="utf-8")
            self.assertIn("G-001", content)
            self.assertIn("G-002", content)
            self.assertIn("G-003", content)

    def test_append_to_protected_path_refused(self):
        """An append to root/ or substrate/ must be rejected exactly as
        file writes are rejected."""
        from unittest.mock import patch
        from meristem import MeristemError

        fake_completion = llm.Completion(
            text=json.dumps({
                "rationale": "test",
                "files": {"meristem/dummy.py": "# test\n"},
                "appends": {"root/panic.py": "malicious\n"},
            }),
            model="test",
        )
        with patch.object(llm, "complete", return_value=fake_completion):
            with self.assertRaises(MeristemError) as ctx:
                engine.propose("test task")
            self.assertIn("protected", str(ctx.exception).lower())


class TestCircuitBreaker(unittest.TestCase):
    def test_rejections_for_counts_rejected_cycles(self):
        """rejections_for must count only cycle records whose 'why' matches
        the task and whose outcome is 'rejected'."""
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "cycle", "cycle": 1, "why": "task A", "outcome": "rejected"},
            {"kind": "cycle", "cycle": 2, "why": "task A", "outcome": "rejected"},
            {"kind": "cycle", "cycle": 3, "why": "task A", "outcome": "candidate"},
            {"kind": "cycle", "cycle": 4, "why": "task B", "outcome": "rejected"},
            {"kind": "usage", "role": "mutate", "model": "x"},
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            with patch("meristem.breaker.JOURNAL", tmp_path):
                self.assertEqual(breaker.rejections_for("task A"), 2)
                self.assertEqual(breaker.rejections_for("task B"), 1)
                self.assertEqual(breaker.rejections_for("task C"), 0)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_should_park_returns_true_at_limit(self):
        """should_park returns True when rejections reach the limit."""
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "cycle", "cycle": 1, "why": "task X", "outcome": "rejected"},
            {"kind": "cycle", "cycle": 2, "why": "task X", "outcome": "rejected"},
            {"kind": "cycle", "cycle": 3, "why": "task X", "outcome": "rejected"},
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            with patch("meristem.breaker.JOURNAL", tmp_path):
                self.assertTrue(breaker.should_park("task X", limit=3))
                self.assertFalse(breaker.should_park("task X", limit=4))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_should_park_returns_false_below_limit(self):
        """should_park returns False when rejections are below the limit."""
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "cycle", "cycle": 1, "why": "task Y", "outcome": "rejected"},
            {"kind": "cycle", "cycle": 2, "why": "task Y", "outcome": "candidate"},
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            with patch("meristem.breaker.JOURNAL", tmp_path):
                self.assertFalse(breaker.should_park("task Y", limit=3))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_should_park_default_limit_is_three(self):
        """The default limit is 3, matching the agenda's specification."""
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "cycle", "cycle": i, "why": "task Z", "outcome": "rejected"}
            for i in range(3)
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            with patch("meristem.breaker.JOURNAL", tmp_path):
                self.assertTrue(breaker.should_park("task Z"))
        finally:
            tmp_path.unlink(missing_ok=True)


    def test_should_park_on_canary_rejects(self):
        """should_park returns True when canary rejects reach the limit."""
        from unittest.mock import patch

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8")
        entries = [
            {"kind": "cycle", "cycle": 1, "why": "task CR", "outcome": "candidate"},
            {"kind": "canary_reject", "why": "task CR", "reason": "tests failed"},
            {"kind": "cycle", "cycle": 2, "why": "task CR", "outcome": "candidate"},
            {"kind": "canary_reject", "why": "task CR", "reason": "tests failed"},
            {"kind": "cycle", "cycle": 3, "why": "task CR", "outcome": "candidate"},
            {"kind": "canary_reject", "why": "task CR", "reason": "tests failed"},
        ]
        for entry in entries:
            tmp.write(json.dumps(entry) + "\n")
        tmp.close()
        tmp_path = pathlib.Path(tmp.name)
        try:
            with patch("meristem.breaker.JOURNAL", tmp_path):
                self.assertTrue(breaker.should_park("task CR"))
                self.assertEqual(breaker.canary_rejects_for("task CR"), 3)
                self.assertEqual(breaker.rejections_for("task CR"), 0)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestParkedTaskSkipping(unittest.TestCase):
    def test_take_task_skips_parked_tasks(self):
        """take_task must skip tasks that are parked: have a 'parked' journal
        record and still appear in state/mailbox.md. Without this check,
        parking would stall the agenda instead of advancing it."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            control = tmpdir / "control"
            control.mkdir()
            (control / "agenda.md").write_text(
                "- [ ] parked task\n- [ ] open task\n",
                encoding="utf-8",
            )
            state = tmpdir / "state"
            state.mkdir()
            (state / "mailbox.md").write_text(
                "- PARKED: parked task (rejected in cycles: 1, 2, 3)\n",
                encoding="utf-8",
            )
            journal = state / "journal.jsonl"
            with journal.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "cycle", "cycle": 4,
                                    "why": "parked task",
                                    "outcome": "parked"}) + "\n")

            with patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.JOURNAL", journal):
                task = loop.take_task()
            self.assertEqual(task, "open task")

    def test_take_task_unparks_when_mailbox_cleared(self):
        """When the human removes the mailbox entry, the task is unparked
        even though the journal still has the parked record. The journal is
        append-only and cannot be rewritten, so the mailbox is the clear
        signal: no mailbox line, no park."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            control = tmpdir / "control"
            control.mkdir()
            (control / "agenda.md").write_text(
                "- [ ] formerly parked task\n- [ ] open task\n",
                encoding="utf-8",
            )
            state = tmpdir / "state"
            state.mkdir()
            # Mailbox is empty -- human cleared the entry
            (state / "mailbox.md").write_text("", encoding="utf-8")
            journal = state / "journal.jsonl"
            with journal.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "cycle", "cycle": 4,
                                    "why": "formerly parked task",
                                    "outcome": "parked"}) + "\n")

            with patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.JOURNAL", journal):
                task = loop.take_task()
            self.assertEqual(task, "formerly parked task")

    def test_take_task_skips_done_and_parked(self):
        """A task that is done is skipped; a task that is parked is skipped;
        a task that is neither is taken. Both filters apply independently."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            control = tmpdir / "control"
            control.mkdir()
            (control / "agenda.md").write_text(
                "- [ ] done task\n"
                "- [ ] parked task\n"
                "- [ ] open task\n",
                encoding="utf-8",
            )
            state = tmpdir / "state"
            state.mkdir()
            (state / "mailbox.md").write_text(
                "- PARKED: parked task (rejected in cycles: 1, 2, 3)\n",
                encoding="utf-8",
            )
            journal = state / "journal.jsonl"
            with journal.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "cycle", "cycle": 1,
                                    "why": "done task",
                                    "outcome": "candidate"}) + "\n")
                f.write(json.dumps({"kind": "cycle", "cycle": 2,
                                    "why": "parked task",
                                    "outcome": "parked"}) + "\n")

            with patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.JOURNAL", journal):
                task = loop.take_task()
            self.assertEqual(task, "open task")


class TestDoneTasksCanaryReject(unittest.TestCase):
    def test_canary_reject_reopens_task(self):
        """A task that produced a candidate but was canary-rejected is NOT
        done -- it must be retried on the next beat."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            control = tmpdir / "control"
            control.mkdir()
            (control / "agenda.md").write_text(
                "- [ ] externalize task\n",
                encoding="utf-8",
            )
            state = tmpdir / "state"
            state.mkdir()
            (state / "mailbox.md").write_text("", encoding="utf-8")
            journal = state / "journal.jsonl"
            with journal.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "cycle", "cycle": 1,
                                    "why": "externalize task",
                                    "outcome": "candidate"}) + "\n")
                f.write(json.dumps({"kind": "canary_reject",
                                    "commit": "abc123",
                                    "why": "externalize task",
                                    "reason": "test failed"}) + "\n")

            with patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.JOURNAL", journal):
                task = loop.take_task()
            self.assertEqual(task, "externalize task")

    def test_promoted_task_stays_done(self):
        """A task that was promoted is done even if it was also
        canary-rejected on a prior attempt."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            control = tmpdir / "control"
            control.mkdir()
            (control / "agenda.md").write_text(
                "- [ ] externalize task\n- [ ] other task\n",
                encoding="utf-8",
            )
            state = tmpdir / "state"
            state.mkdir()
            (state / "mailbox.md").write_text("", encoding="utf-8")
            journal = state / "journal.jsonl"
            with journal.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "cycle", "cycle": 1,
                                    "why": "externalize task",
                                    "outcome": "candidate"}) + "\n")
                f.write(json.dumps({"kind": "canary_reject",
                                    "commit": "abc123",
                                    "why": "externalize task",
                                    "reason": "test failed"}) + "\n")
                f.write(json.dumps({"kind": "cycle", "cycle": 2,
                                    "why": "externalize task",
                                    "outcome": "candidate"}) + "\n")
                f.write(json.dumps({"kind": "promoted",
                                    "commit": "def456",
                                    "why": "externalize task"}) + "\n")

            with patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.JOURNAL", journal):
                task = loop.take_task()
            self.assertEqual(task, "other task")


class TestFailureHistory(unittest.TestCase):
    def test_no_failures_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            j.write_text(
                json.dumps({"kind": "cycle", "cycle": 1,
                            "outcome": "candidate", "why": "task A"}) + "\n",
                encoding="utf-8",
            )
            result = journal_mod.failure_history(j, "task A")
            self.assertEqual(result, "")

    def test_review_rejection_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            j.write_text(
                json.dumps({"kind": "cycle", "cycle": 5, "outcome": "rejected",
                            "why": "task B", "reason": "review rejected (1/2 (need 2))",
                            "rejected_by": [{"slot": "review:deepseek",
                                             "reasons": ["removes critical gate"]}]}) + "\n",
                encoding="utf-8",
            )
            result = journal_mod.failure_history(j, "task B")
            self.assertIn("review:deepseek: removes critical gate", result)
            self.assertIn("cycle 5", result)

    def test_deterministic_rejection_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            j.write_text(
                json.dumps({"kind": "cycle", "cycle": 99, "outcome": "rejected",
                            "why": "task C",
                            "reason": "deterministic: closure over budget",
                            "rejected_by": []}) + "\n",
                encoding="utf-8",
            )
            result = journal_mod.failure_history(j, "task C")
            self.assertIn("deterministic: closure over budget", result)

    def test_canary_reject_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            j.write_text(
                json.dumps({"kind": "canary_reject", "commit": "abc123",
                            "why": "task D", "reason": "tests failed"}) + "\n",
                encoding="utf-8",
            )
            result = journal_mod.failure_history(j, "task D")
            self.assertIn("canary", result)
            self.assertIn("tests failed", result)

    def test_limit_restricts_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            lines = []
            for i in range(5):
                lines.append(json.dumps({"kind": "cycle", "cycle": i,
                                         "outcome": "rejected", "why": "task E",
                                         "reason": f"gate fail {i}",
                                         "rejected_by": []}))
            j.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = journal_mod.failure_history(j, "task E", limit=2)
            self.assertNotIn("cycle 0", result)
            self.assertNotIn("cycle 1", result)
            self.assertNotIn("cycle 2", result)
            self.assertIn("cycle 3", result)
            self.assertIn("cycle 4", result)

    def test_faults_do_not_consume_the_history_window(self):
        """A run of 429s must not push the real objections out of the window.

        The window keeps the last `limit` entries. A fault is a mechanism
        failure -- the proposal was never judged -- so counting faults as
        entries let three rate-limit retries flush the reviewer objections
        the seed actually needs to see. Same distinction the breaker already
        makes (P-016): a fault is not a verdict.
        """
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            lines = [
                json.dumps({"kind": "cycle", "cycle": 1, "outcome": "rejected",
                            "why": "task F", "reason": "review rejected (0/2)",
                            "rejected_by": [{"slot": "r1",
                                             "reasons": ["the real objection"]}]}),
            ]
            for c in (2, 3, 4):
                lines.append(json.dumps({"kind": "fault", "cycle": c,
                                         "task": "task F", "error": "HTTP 429"}))
                lines.append(json.dumps({"kind": "cycle", "cycle": c,
                                         "outcome": "rejected", "why": "task F",
                                         "reason": "mutate:glm failed: HTTP 429"}))
            j.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
            result = journal_mod.failure_history(j, "task F", limit=3)
            self.assertIn("the real objection", result)
            self.assertNotIn("429", result)

    def test_different_task_not_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = pathlib.Path(tmp) / "journal.jsonl"
            j.write_text(
                json.dumps({"kind": "cycle", "cycle": 1, "outcome": "rejected",
                            "why": "other task", "reason": "gate fail",
                            "rejected_by": []}) + "\n",
                encoding="utf-8",
            )
            result = journal_mod.failure_history(j, "my task")
            self.assertEqual(result, "")


class TestProbeProposalsCommand(unittest.TestCase):
    def test_probe_proposals_on_empty(self):
        """When no proposals exist, the command handles it gracefully."""
        import io
        import contextlib
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            buf = io.StringIO()
            with patch("meristem.loop.REPO", tmpdir):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["probe-proposals"])
            self.assertEqual(rc, 0)
            self.assertIn("no probe proposals", buf.getvalue().lower())

    def test_probe_proposals_lists_completeness(self):
        """The command must list each proposal's id and whether it carries
        both a statement/ and a rubric/ directory."""
        import io
        import contextlib
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            proposals = tmpdir / "state" / "probe-proposals"
            # Complete proposal
            complete = proposals / "probe-complete"
            (complete / "statement").mkdir(parents=True)
            (complete / "rubric").mkdir(parents=True)
            (complete / "probe.json").write_text(
                '{"id": "probe-complete"}', encoding="utf-8")
            # Incomplete: missing rubric
            incomplete = proposals / "probe-incomplete"
            (incomplete / "statement").mkdir(parents=True)
            (incomplete / "probe.json").write_text(
                '{"id": "probe-incomplete"}', encoding="utf-8")

            buf = io.StringIO()
            with patch("meristem.loop.REPO", tmpdir):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["probe-proposals"])
            output = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("probe-complete", output)
            self.assertIn("probe-incomplete", output)
            # The complete proposal should show yes for statement, rubric, complete
            # The incomplete should show no for rubric and no for complete
            lines = output.strip().splitlines()
            for line in lines:
                if "probe-complete" in line:
                    self.assertIn("yes", line.lower())
                if "probe-incomplete" in line:
                    # Should show no for rubric and no for complete
                    self.assertIn("no", line.lower())


class TestReflectCommand(unittest.TestCase):
    def test_reflect_command_exists_and_refuses_agenda(self):
        """The reflect command must exist in argparse choices and must never
        write to control/agenda.md -- a human promotes a proposal into the
        agenda.

        If 'reflect' is not in the choices, argparse calls sys.exit(2),
        which raises SystemExit and fails this test. So reaching the
        assertions below proves the command is accepted.
        """
        import io
        import contextlib
        from unittest.mock import patch

        fake_completion = llm.Completion(
            text=json.dumps({"proposals": ["task A", "task B"]}),
            model="test",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            state = tmpdir / "state"
            state.mkdir(parents=True)
            (state / "gaps.md").write_text("## G-001 \u2014 test gap\n",
                                            encoding="utf-8")
            (state / "patterns.md").write_text("## P-001 \u2014 test pattern\n",
                                                encoding="utf-8")
            (state / "proposals.md").write_text("", encoding="utf-8")

            control = tmpdir / "control"
            control.mkdir(parents=True)
            agenda = control / "agenda.md"
            agenda_content = "# Agenda\n\n- [ ] existing task\n"
            agenda.write_text(agenda_content, encoding="utf-8")

            journal = state / "journal.jsonl"

            buf = io.StringIO()
            with patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.JOURNAL", journal), \
                 patch("meristem.loop.llm_mod.complete",
                       return_value=fake_completion), \
                 patch("meristem.loop.germline.invoke",
                       return_value={"ok": True,
                                     "result": {"stale": ["P-001"]}}), \
                 patch("meristem.loop.ledger_mod.record",
                       return_value=0.0), \
                 patch("meristem.loop.ledger_mod.check"), \
                 patch("meristem.loop.ledger_mod.drain_attempts",
                       return_value=0), \
                 patch("meristem.loop.llm_mod.load_models",
                       return_value={}):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["reflect"])

            self.assertEqual(rc, 0)
            # agenda.md must be unchanged -- reflect never writes to it
            self.assertEqual(agenda.read_text(encoding="utf-8"),
                             agenda_content)
            # proposals.md should have the proposals appended
            proposals = (state / "proposals.md").read_text(encoding="utf-8")
            self.assertIn("- [ ] task A", proposals)
            self.assertIn("- [ ] task B", proposals)
            # The output should report how many were appended
            self.assertIn("2", buf.getvalue())

    def test_reflect_digest_carries_self_report(self):
        """reflect must feed REPORT.md back into its own digest.

        The report holds the aggregate evidence no single cycle can see --
        acceptance rate, pressure trend, parked tasks. Without it in the
        digest the seed can never detect a failure CLASS from its own
        history, and the report is a gauge only a human ever reads.
        """
        import io
        import contextlib
        from unittest.mock import patch

        fake_completion = llm.Completion(
            text=json.dumps({"proposals": ["task A"]}),
            model="test",
        )
        captured = {}

        def _capture(role, messages, config=None):
            captured["digest"] = messages[-1]["content"]
            return fake_completion

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            state = tmpdir / "state"
            state.mkdir(parents=True)
            (state / "gaps.md").write_text("## G-001 gap\n", encoding="utf-8")
            (state / "patterns.md").write_text("## P-001 pat\n", encoding="utf-8")
            (state / "proposals.md").write_text("", encoding="utf-8")
            control = tmpdir / "control"
            control.mkdir(parents=True)
            (control / "agenda.md").write_text("# Agenda\n", encoding="utf-8")
            (tmpdir / "REPORT.md").write_text(
                "# Meristem Report\n\nacceptance rate: 0.42\nparked: none\n",
                encoding="utf-8")
            journal = state / "journal.jsonl"

            buf = io.StringIO()
            with patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.JOURNAL", journal), \
                 patch("meristem.loop.llm_mod.complete", _capture), \
                 patch("meristem.loop.germline.invoke",
                       return_value={"ok": True, "result": {"stale": []}}), \
                 patch("meristem.loop.ledger_mod.record", return_value=0.0), \
                 patch("meristem.loop.ledger_mod.check"), \
                 patch("meristem.loop.ledger_mod.drain_attempts",
                       return_value=0), \
                 patch("meristem.loop.llm_mod.load_models", return_value={}):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["reflect"])

            self.assertEqual(rc, 0)
            self.assertIn("## Self-report", captured["digest"])
            self.assertIn("acceptance rate: 0.42", captured["digest"])

    def test_reflect_digest_omits_missing_report(self):
        """A missing REPORT.md must not add an empty section or crash."""
        import io
        import contextlib
        from unittest.mock import patch

        fake_completion = llm.Completion(
            text=json.dumps({"proposals": ["task A"]}),
            model="test",
        )
        captured = {}

        def _capture(role, messages, config=None):
            captured["digest"] = messages[-1]["content"]
            return fake_completion

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            state = tmpdir / "state"
            state.mkdir(parents=True)
            (state / "gaps.md").write_text("## G-001 gap\n", encoding="utf-8")
            (state / "patterns.md").write_text("## P-001 pat\n", encoding="utf-8")
            (state / "proposals.md").write_text("", encoding="utf-8")
            control = tmpdir / "control"
            control.mkdir(parents=True)
            (control / "agenda.md").write_text("# Agenda\n", encoding="utf-8")
            journal = state / "journal.jsonl"

            buf = io.StringIO()
            with patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.JOURNAL", journal), \
                 patch("meristem.loop.llm_mod.complete", _capture), \
                 patch("meristem.loop.germline.invoke",
                       return_value={"ok": True, "result": {"stale": []}}), \
                 patch("meristem.loop.ledger_mod.record", return_value=0.0), \
                 patch("meristem.loop.ledger_mod.check"), \
                 patch("meristem.loop.ledger_mod.drain_attempts",
                       return_value=0), \
                 patch("meristem.loop.llm_mod.load_models", return_value={}):
                with contextlib.redirect_stdout(buf):
                    rc = loop.main(["reflect"])

            self.assertEqual(rc, 0)
            self.assertNotIn("## Self-report", captured["digest"])

    def _run_reflect_with_proposal(self, tmpdir, proposal):
        """Drive one reflect that emits `proposal`; return mailbox text."""
        import io
        import contextlib
        from unittest.mock import patch

        state = tmpdir / "state"
        state.mkdir(parents=True)
        (state / "gaps.md").write_text("## G-001 gap\n", encoding="utf-8")
        (state / "patterns.md").write_text("## P-001 pat\n", encoding="utf-8")
        (state / "proposals.md").write_text("", encoding="utf-8")
        mailbox = state / "mailbox.md"
        control = tmpdir / "control"
        control.mkdir(parents=True)
        (control / "agenda.md").write_text("# Agenda\n", encoding="utf-8")

        fake = llm.Completion(text=json.dumps({"proposals": [proposal]}),
                              model="test")
        buf = io.StringIO()
        with patch("meristem.loop.REPO", tmpdir), \
             patch("meristem.loop.CONTROL", control), \
             patch("meristem.loop.JOURNAL", state / "journal.jsonl"), \
             patch("meristem.loop.llm_mod.complete", return_value=fake), \
             patch("meristem.loop.germline.invoke",
                   return_value={"ok": True, "result": {"stale": []}}), \
             patch("meristem.loop.ledger_mod.record", return_value=0.0), \
             patch("meristem.loop.ledger_mod.check"), \
             patch("meristem.loop.ledger_mod.drain_attempts", return_value=0), \
             patch("meristem.loop.llm_mod.load_models", return_value={}):
            with contextlib.redirect_stdout(buf):
                rc = loop.main(["reflect"])
        self.assertEqual(rc, 0)
        return mailbox.read_text(encoding="utf-8") if mailbox.exists() else ""

    def test_unargued_cap_proposal_is_refused_and_journalled(self):
        """Refused, not mailboxed: nobody is asked, and the seed learns why.

        The refusal record rides the self-report back into reflect, so the
        seed can resubmit a complete case without a human touching anything.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            mailbox = self._run_reflect_with_proposal(
                tmpdir, "raise KERNEL_LOC_CAP to 6000")
            self.assertNotIn("PROPOSAL", mailbox, "nobody should be asked")
            proposals = (tmpdir / "state" / "proposals.md").read_text(
                encoding="utf-8")
            self.assertNotIn("KERNEL_LOC_CAP", proposals,
                             "an unargued cap change must never be queued")
            rows = read_jsonl(tmpdir / "state" / "journal.jsonl")
            refusals = [r for r in rows if r.get("kind") == "cap_case_refused"]
            self.assertEqual(len(refusals), 1)
            self.assertIn("per-file", refusals[0]["reason"])

    def test_complete_cap_case_is_queued_for_the_panel(self):
        """A six-element case joins the work queue like any other task."""
        case = (
            "Per-file LOC: loop.py 757, gates 500. Core pressure 0.88, "
            "closure pressure 0.65. Already externalized the view commands and "
            "pruned two helpers; insufficient because the loop machinery itself "
            "is irreducible. Proposed new cap 3400. Expected closure impact: "
            "none, closure stays at 0.65."
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            mailbox = self._run_reflect_with_proposal(tmpdir, case)
            self.assertNotIn("PROPOSAL", mailbox, "nobody should be asked")
            proposals = (tmpdir / "state" / "proposals.md").read_text(
                encoding="utf-8")
            self.assertIn("Proposed new cap 3400", proposals)

    def test_guarded_path_proposal_still_reaches_a_human(self):
        """Guarded ground is not on the ladder and still holds for a human."""
        with tempfile.TemporaryDirectory() as tmp:
            text = self._run_reflect_with_proposal(
                pathlib.Path(tmp), "Fix the bug in substrate/supervisor.py")
            self.assertIn("names guarded ground", text)
            label = text.split("- PROPOSAL (", 1)[1].split(")", 1)[0]
            self.assertNotIn(": ", label, "a colon in the label breaks dedup")

    def _capture_digest(self, tmpdir, *, pressure, seed_journal=None):
        """Run one reflect and return the digest that reached the model."""
        import io
        import contextlib
        from unittest.mock import patch

        state = tmpdir / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "gaps.md").write_text("## G-001 gap\n", encoding="utf-8")
        (state / "patterns.md").write_text("## P-001 pat\n", encoding="utf-8")
        (state / "proposals.md").write_text("- [ ] an existing proposal\n",
                                            encoding="utf-8")
        control = tmpdir / "control"
        control.mkdir(parents=True, exist_ok=True)
        (control / "agenda.md").write_text("# Agenda\n", encoding="utf-8")
        (tmpdir / "REPORT.md").write_text("# Meristem Report\nagr: 0.42\n",
                                          encoding="utf-8")
        (tmpdir / "meristem").mkdir(parents=True, exist_ok=True)
        (tmpdir / "meristem" / "x.py").write_text("x = 1\n", encoding="utf-8")
        journal = state / "journal.jsonl"
        for row in (seed_journal or []):
            append_jsonl(journal, row)

        captured = {}

        def _capture(role, messages, config=None):
            captured["digest"] = messages[-1]["content"]
            return llm.Completion(text=json.dumps({"proposals": ["t"]}),
                                  model="test")

        argv = ["reflect", "--pressure"] if pressure else ["reflect"]
        buf = io.StringIO()
        with patch("meristem.loop.REPO", tmpdir), \
             patch("meristem.loop.CONTROL", control), \
             patch("meristem.loop.JOURNAL", journal), \
             patch("meristem.loop.llm_mod.complete", _capture), \
             patch("meristem.loop.germline.invoke",
                   return_value={"ok": True, "result": {"stale": []}}), \
             patch("meristem.loop.ledger_mod.record", return_value=0.0), \
             patch("meristem.loop.ledger_mod.check"), \
             patch("meristem.loop.ledger_mod.drain_attempts", return_value=0), \
             patch("meristem.loop.llm_mod.load_models", return_value={}):
            with contextlib.redirect_stdout(buf):
                loop.main(argv)
        return captured["digest"]

    def test_the_mandate_replaces_the_question_not_the_memory(self):
        """Under pressure the digest was REPLACED, wiping three feedback
        sections that are appended above it. Harmless when pressure was
        occasional; starvation once it sat at 0.95+ and nearly every
        reflection ran in mandate mode. The worst case was self-inflicted:
        cap cases are only authored UNDER the mandate, so the one mode that
        needed the refusal reasons was the one mode that could not see them.
        """
        refusal = {"kind": "cap_case_refused", "cycle": 1,
                   "why": "raise the cap", "reason": "missing per-file"}
        with tempfile.TemporaryDirectory() as tmp:
            digest = self._capture_digest(pathlib.Path(tmp), pressure=True,
                                          seed_journal=[refusal])
            self.assertIn("Kernel LOC by file", digest, "mandate base present")
            self.assertIn("Already proposed", digest)
            self.assertIn("Cap cases refused", digest)
            self.assertIn("missing per-file", digest)
            self.assertIn("Self-report", digest)

    def test_ordinary_reflect_still_carries_the_same_memory(self):
        refusal = {"kind": "cap_case_refused", "cycle": 1,
                   "why": "raise the cap", "reason": "missing per-file"}
        with tempfile.TemporaryDirectory() as tmp:
            digest = self._capture_digest(pathlib.Path(tmp), pressure=False,
                                          seed_journal=[refusal])
            self.assertIn("Reflection digest", digest, "ordinary base present")
            self.assertNotIn("Kernel LOC by file", digest)
            self.assertIn("Already proposed", digest)
            self.assertIn("Cap cases refused", digest)
            self.assertIn("Self-report", digest)

    def test_mandate_states_the_literal_phrases_the_checker_wants(self):
        """cap_case_missing matches literal substrings. Five real cases were
        refused for missing the same phrases while arguing the substance
        fine, so the contract has to be readable."""
        with tempfile.TemporaryDirectory() as tmp:
            digest = self._capture_digest(pathlib.Path(tmp), pressure=True)
            for phrase in loop.CAP_CASE_REQUIRED:
                self.assertIn(f'"{phrase}"', digest,
                              f"mandate must name the literal phrase {phrase}")

    def test_reflect_appends_at_most_three(self):
        """reflect must cap proposals at three, even if the model returns more."""
        import io
        import contextlib
        from unittest.mock import patch

        fake_completion = llm.Completion(
            text=json.dumps({"proposals": ["a", "b", "c", "d", "e"]}),
            model="test",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            state = tmpdir / "state"
            state.mkdir(parents=True)
            (state / "proposals.md").write_text("", encoding="utf-8")

            control = tmpdir / "control"
            control.mkdir(parents=True)
            (control / "agenda.md").write_text("# Agenda\n",
                                                 encoding="utf-8")

            journal = state / "journal.jsonl"

            with patch("meristem.loop.REPO", tmpdir), \
                 patch("meristem.loop.CONTROL", control), \
                 patch("meristem.loop.JOURNAL", journal), \
                 patch("meristem.loop.llm_mod.complete",
                       return_value=fake_completion), \
                 patch("meristem.loop.germline.invoke",
                       return_value={"ok": True, "result": {"stale": []}}), \
                 patch("meristem.loop.ledger_mod.record",
                       return_value=0.0), \
                 patch("meristem.loop.ledger_mod.check"), \
                 patch("meristem.loop.ledger_mod.drain_attempts",
                       return_value=0), \
                 patch("meristem.loop.llm_mod.load_models",
                       return_value={}):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = loop.main(["reflect"])

            self.assertEqual(rc, 0)
            proposals = (state / "proposals.md").read_text(encoding="utf-8")
            lines = [l for l in proposals.splitlines() if l.startswith("- [ ] ")]
            self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
