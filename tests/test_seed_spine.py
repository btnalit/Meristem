"""P0-a acceptance tests for the seed spine (meristem/).

Covers exactly the acceptance criteria in docs/MERISTEM-V5-SPEC.md SS11 for
the four seed-owned rows (`engine` path / `engine` prompt face / `loop`
spine / `status` contract) plus the repo-wide CA-3 grep assertion.

unittest.TestCase (not bare pytest functions): the soil's canary() runs
`python -m unittest discover -s tests` (substrate/supervisor.py), which
only collects TestCase subclasses. Bare `def test_x():` functions are
silently invisible to that runner even though pytest would collect them.

Run: python -m pytest tests/test_seed_spine.py -q
 or: python -m unittest tests.test_seed_spine -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
MERISTEM_SRC = REPO / "meristem"

sys.path.insert(0, str(REPO))

from meristem import engine, llm, loop, narrative  # noqa: E402


# ---------------------------------------------------------------------------
# engine path: whitelist rejects everything not on SEED_WRITABLE (SS11)
# ---------------------------------------------------------------------------

REJECTED_PATHS = (
    ".pytest_cache/x.py",
    "root/panic.py",
    "../x",
    "/abs/path",
    "seed/model-interface.json",
)


class ValidatePathsTests(unittest.TestCase):
    def test_rejects_each_required_bad_path(self):
        for bad_path in REJECTED_PATHS:
            with self.subTest(bad_path=bad_path):
                with self.assertRaises(engine.PathViolation):
                    engine._validate_paths({bad_path: "content"}, "test")

    def test_accepts_writable_examples(self):
        engine._validate_paths(
            {
                "seed/narrative.md": "x",
                "body/organs/classifier/rules.py": "x",
                "tests/test_something.py": "x",
            },
            "test",
        )  # must not raise

    def test_rejects_dotfile_segment_anywhere(self):
        with self.assertRaises(engine.PathViolation):
            engine._validate_paths({"body/organs/.hidden/x.py": "x"}, "test")

    def test_rejects_readonly_feedback_json(self):
        with self.assertRaises(engine.PathViolation):
            engine._validate_paths({"seed/feedback.json": "{}"}, "test")


# ---------------------------------------------------------------------------
# engine prompt face: over-budget prompts must cause zero model calls (I10)
# ---------------------------------------------------------------------------

class PromptBudgetTests(unittest.TestCase):
    def test_over_budget_makes_zero_llm_calls(self):
        calls = []
        with mock.patch.object(llm, "call_model", lambda role, prompt: calls.append((role, prompt))):
            huge_extra = "word " * 40000  # ~52000 estimated tokens, over PROMPT_BUDGET
            with self.assertRaises(engine.PromptOverBudget):
                engine.propose("do something", config={}, extra=huge_extra)
        self.assertEqual(calls, [])

    def test_within_budget_calls_llm_exactly_once(self):
        calls = []

        def fake_call_model(role, prompt):
            calls.append((role, prompt))
            return llm.CallResult(status="allowed",
                                  content=json.dumps({"seed/narrative.md": "ok"}))

        with mock.patch.object(llm, "call_model", fake_call_model):
            mutation = engine.propose("small task", config={})
        self.assertEqual(len(calls), 1)
        self.assertEqual(mutation.files, {"seed/narrative.md": "ok"})


# ---------------------------------------------------------------------------
# F4: llm.call_model must fail closed, loudly and greppably, when the soil
# has not injected MERISTEM_MODEL_GATEWAY -- no guessed default entrypoint.
# A wrong guess and an absent gateway would otherwise both surface to the
# seed as an identical "refused", which is the hardest kind of integration
# fault to diagnose.
# ---------------------------------------------------------------------------

class GatewayNotInjectedTests(unittest.TestCase):
    def test_missing_env_var_is_refused_with_reason_and_stderr_marker(self):
        with mock.patch.object(llm, "_roles_available", lambda: {"mutate"}):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MERISTEM_MODEL_GATEWAY", None)
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    result = llm.call_model("mutate", "prompt text")
        self.assertEqual(result.status, "refused")
        self.assertEqual(result.reason, "gateway_not_injected")
        self.assertIn("GATEWAY_NOT_INJECTED", buf.getvalue())


# ---------------------------------------------------------------------------
# loop spine: given a task and a writable worktree, produce a commit; the
# seed writes no ledger of any kind.
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "root"],
                   cwd=str(path), check=True)


def _commit_count(path: Path) -> int:
    result = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                            cwd=str(path), capture_output=True, text=True, check=True)
    return int(result.stdout.strip())


class LoopSpineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        _init_git_repo(self.workdir)

    def test_run_cycle_produces_one_commit(self):
        before = _commit_count(self.workdir)

        def fake_call_model(role, prompt):
            self.assertEqual(role, "mutate")
            return llm.CallResult(
                status="allowed",
                content=json.dumps({"body/organs/classifier/patch.py": "X = 1\n"}),
            )

        with mock.patch.object(engine.llm, "call_model", fake_call_model):
            result = loop.run_cycle("teach the classifier a new keyword", 1, workdir=self.workdir)

        self.assertTrue(result.ok)
        self.assertTrue(result.commit)
        self.assertEqual(_commit_count(self.workdir), before + 1)
        written = self.workdir / "body/organs/classifier/patch.py"
        self.assertEqual(written.read_text(encoding="utf-8"), "X = 1\n")

    def test_run_cycle_with_no_task_is_not_a_candidate(self):
        result = loop.run_cycle(None, 1, workdir=self.workdir)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "no_task")

    def test_run_cycle_path_violation_yields_no_commit(self):
        before = _commit_count(self.workdir)

        def fake_call_model(role, prompt):
            return llm.CallResult(status="allowed",
                                  content=json.dumps({"root/panic.py": "boom"}))

        with mock.patch.object(engine.llm, "call_model", fake_call_model):
            result = loop.run_cycle("try to touch root", 1, workdir=self.workdir)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "path_violation")
        self.assertEqual(_commit_count(self.workdir), before)


class NoLedgerWriteTests(unittest.TestCase):
    def test_seed_source_has_no_ledger_write_path(self):
        """Static assertion: nothing in meristem/ references a soil-owned
        record path. The seed has no ledger-write interface (C4) -- no
        string in this package names one.
        """
        forbidden = ("state/soil", "soil-ledger", "soil_ledger", "scoreboard")
        for py_file in sorted(MERISTEM_SRC.glob("*.py")):
            text = py_file.read_text(encoding="utf-8").lower()
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{py_file.name} references {needle!r}")


# ---------------------------------------------------------------------------
# CA-3: meristem/ must not contain any vault / soil-ledger / scoreboard
# path constant or string, repo-wide grep over the package.
# ---------------------------------------------------------------------------

class CA3Tests(unittest.TestCase):
    def test_no_vault_ledger_scoreboard_strings_in_meristem(self):
        forbidden = ("vault", "soil-ledger", "soil_ledger", "scoreboard")
        offenders = []
        for py_file in sorted(MERISTEM_SRC.glob("*.py")):
            text = py_file.read_text(encoding="utf-8").lower()
            for needle in forbidden:
                if needle in text:
                    offenders.append((py_file.name, needle))
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------------------
# status contract: output must be parseable by the two patterns SS9.1
# requires, and by supervisor.py's actual (split-based) parsing logic,
# reproduced here verbatim as the reverse test.
# ---------------------------------------------------------------------------

CORE_PRESSURE_RE = re.compile(r"^core pressure: -?\d+(?:\.\d+)?\s*$")
AGENDA_ITEM_RE = re.compile(r"^open agenda item: .*$")


def _supervisor_core_pressure(stdout: str) -> float:
    # Mirrors substrate/supervisor.py core_pressure()'s parse exactly.
    for line in stdout.splitlines():
        if "core pressure" in line:
            return float(line.split(":")[1].split()[0])
    raise AssertionError("no 'core pressure' line in status output")


def _supervisor_pending_task(stdout: str) -> bool:
    # Mirrors substrate/supervisor.py pending_task()'s parse exactly.
    for line in stdout.splitlines():
        if line.startswith("open agenda item"):
            return "(none)" not in line
    raise AssertionError("no 'open agenda item' line in status output")


class StatusContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / "seed").mkdir(parents=True, exist_ok=True)

    def _capture_status(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = narrative.print_status(self.repo)
        self.assertEqual(exit_code, 0)
        return buf.getvalue()

    def test_matches_contract_regexes_no_agenda(self):
        out = self._capture_status()
        lines = out.splitlines()
        self.assertTrue(any(CORE_PRESSURE_RE.match(line) for line in lines))
        self.assertTrue(any(AGENDA_ITEM_RE.match(line) for line in lines))
        self.assertEqual(_supervisor_core_pressure(out), 0.0)
        self.assertFalse(_supervisor_pending_task(out))  # "(none)" -> no pending task

    def test_matches_contract_regexes_with_agenda(self):
        (self.repo / "seed" / "agenda.md").write_text("- improve the classifier\n", encoding="utf-8")
        out = self._capture_status()
        lines = out.splitlines()
        self.assertTrue(any(CORE_PRESSURE_RE.match(line) for line in lines))
        self.assertTrue(any(AGENDA_ITEM_RE.match(line) for line in lines))
        self.assertTrue(_supervisor_pending_task(out))
        self.assertIn("improve the classifier", out)


if __name__ == "__main__":
    unittest.main()
