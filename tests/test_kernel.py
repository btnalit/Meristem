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

    def test_empty_body_is_valid(self):
        """body/ ships empty on purpose -- zero scaffolding."""
        self.assertEqual(deterministic.organ_manifests(), [])


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
        slots = llm.slots_for("review")
        hosts = {s["base_url"] for s in slots}
        self.assertGreater(len(hosts), 1, "reviewers from one provider may fail together")


if __name__ == "__main__":
    unittest.main()
