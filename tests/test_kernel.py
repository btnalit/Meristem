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
from meristem import engine, germline, ledger, llm  # noqa: E402
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
            register.write_text("# Pattern Register\n\n## Z-999 — only\n",
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
            register.write_text("## G-001 — one\n\n## G-002 — two\n", encoding="utf-8")
            run("add", "-A")
            run("commit", "-q", "-m", "base")
            # The mutation erases an entry, and is COMMITTED (as the loop does).
            register.write_text("## G-009 — only\n", encoding="utf-8")
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
        """Whatever the body currently holds must satisfy the germline.

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
                result = germline.invoke("_test_invoke", {}, caller="test_caller")

            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(recorded), 1)
            _, record = recorded[0]
            self.assertEqual(record["kind"], "organ_call")
            self.assertEqual(record["caller"], "test_caller")
            self.assertEqual(record["callee"], "_test_invoke")
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
        lifecycle, and capability — the four fields that make the body
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


class TestAppends(unittest.TestCase):
    def test_append_adds_without_erasing(self):
        """Appends must add text at the end of a file without erasing
        existing content. This is the structural fix for the class of
        failure where whole-file replacement erases a register."""
        with tempfile.TemporaryDirectory() as tmp:
            workdir = pathlib.Path(tmp)
            target = workdir / "state" / "gaps.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("## G-001 — first\n\n## G-002 — second\n",
                              encoding="utf-8")
            mutation = engine.Mutation(
                rationale="test",
                appends={"state/gaps.md": "\n## G-003 — third\n"},
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


if __name__ == "__main__":
    unittest.main()
