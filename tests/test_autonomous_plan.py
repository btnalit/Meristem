"""End-to-end autonomous H1 plan rehearsals in isolated repositories.

These tests intentionally exercise the real pipeline, soil ledger, autonomous
panel, reconcile path, and rollback executor without touching the production
Meristem runtime state or provider credentials.
"""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from substrate import pipeline, soil_state
from substrate.rollback import (
    build_plan,
    execute_autonomous_rollback,
    verify_receipt_state,
)
from substrate.supervisor import autonomous_panel
from tests.test_pipeline import (
    KNOWS_THREE,
    PROBE_ID,
    _make_candidate,
    _make_repo,
    _task,
)


class AutonomousPlanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        probe = self.vault / "internal" / "active" / PROBE_ID
        probe.mkdir(parents=True)
        checks = [
            {"id": "c1", "input": "closure over budget", "cmp": "equals", "expect": "closure-budget"},
            {"id": "c2", "input": "touches protected path", "cmp": "equals", "expect": "protected-path"},
            {"id": "c3", "input": "anchor regressed", "cmp": "equals", "expect": "probe-regressed"},
            {"id": "c4", "input": "prompt surface too large", "cmp": "equals", "expect": "prompt-budget"},
            {"id": "c5", "input": "contract surface too large", "cmp": "equals", "expect": "contract-budget"},
        ]
        (probe / "probe.json").write_text(json.dumps({
            "id": PROBE_ID, "capability": "classify", "organ": "classifier", "checks": checks
        }))

    def _ctx(self, repo, soil_cycle=1):
        ctx = soil_state.SoilContext.open(
            repo, generation="gen-0", soil_cycle=soil_cycle, vault=self.vault)
        ctx.frozen_registry.freeze({
            "probe_id": PROBE_ID, "status": "active", "created_by": "seed",
            "proposed_commit": "0" * 40, "frozen_tree_sha": "0" * 40,
            "frozen_probe_manifest_sha": "fixture", "eligible_after": {
                "generation": "gen-0", "soil_cycle": 0},
        })
        return ctx

    @staticmethod
    def _git(repo, *args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    def test_complete_autonomous_plan(self):
        repo = _make_repo(self.root)
        stable = self._git(repo, "rev-parse", "HEAD")
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)

        # 1. Real soil-owned autonomous panel and full promotion transaction.
        outcome = pipeline.process_candidate(candidate, _task(), repo=repo,
                                             panel=autonomous_panel, ctx=ctx)
        self.assertIs(outcome, pipeline.Outcome.PROMOTED)
        rows = ctx.ledger.read()
        self.assertEqual([row["kind"] for row in rows], [
            "observed_fitness", "promotion_intent", "accepted_fitness",
            "promotion_committed",
        ])
        intent = next(row for row in rows if row["kind"] == "promotion_intent")
        self.assertEqual(self._git(repo, "rev-parse", "HEAD"), candidate)

        # 2. Accepted candidate is a real ignition event before rollback.
        accepted = next(row for row in rows if row["kind"] == "accepted_fitness")
        self.assertTrue(pipeline.is_ignition_event(accepted))
        self.assertEqual(accepted["commit"], candidate)

        # 3. Soil-owned autonomous bad-candidate rollback, with live receipt.
        (repo / "root" / "generations.json").parent.mkdir(parents=True, exist_ok=True)
        (repo / "root" / "generations.json").write_text(json.dumps({"live": "gen-0"}))
        (repo / "seed").mkdir(exist_ok=True)
        (repo / "seed" / "feedback.json").write_text(json.dumps({
            "facts": {"task_states": {"task-test": {"state": "open"}}}
        }))
        plan = build_plan(task_id="task-test", attempt_id=intent["attempt_id"],
                          from_commit=candidate, restore_commit=stable,
                          phase="promoted_bad_candidate", reason="primary regression",
                          authority="soil-autonomous")
        self.assertEqual(execute_autonomous_rollback(repo, plan), stable)
        receipt = {
            "task_id": "task-test", "attempt_id": intent["attempt_id"],
            "from_commit": candidate, "phase": "promoted_bad_candidate",
            "authority": "soil-autonomous", "restored_commit": stable,
            "generation": "gen-0", "soil_cycle": 1,
            "ledger_tail_hash": hashlib.sha256(ctx.ledger.path.read_bytes()).hexdigest(),
            "task_state": "open", "status": "rolled_back",
        }
        verify_receipt_state(repo, plan, receipt)
        self.assertEqual(self._git(repo, "rev-parse", "HEAD"), stable)

    def test_crash_recovery_after_intent_and_after_accept_are_idempotent(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)
        observed = ctx.ledger.append({
            "kind": "observed_fitness", "commit": candidate,
            "attempt_id": "attempt-1", "records": [{
                "probe_id": PROBE_ID, "before": 40.0, "after": 60.0, "delta": 20.0,
                "status": "improved", "checks_before": 2, "checks_after": 3,
                "checks_total": 5, "measured_by": "soil", "tree_before": "a",
                "tree_after": "b", "probe_manifest_sha": "m",
                "runner_version": "1", "execution_policy_version": "1",
            }], "task_id": "task-test", "primary_probe": PROBE_ID,
            "generation": "gen-0", "soil_cycle": 1, "calibration": False,
            "counts_as_progress": False,
        })
        ctx.ledger.append({"kind": "promotion_intent", "source": observed,
                           "attempt_id": "attempt-1", "commit": candidate,
                           "parent": self._git(repo, "rev-parse", "HEAD")})
        subprocess.run(["git", "merge", "--ff-only", "-q", candidate], cwd=repo, check=True)
        resolved = pipeline.reconcile_on_start(repo, ctx)
        self.assertEqual(resolved, [(candidate, pipeline.Outcome.PROMOTED)])
        self.assertEqual(sum(row["kind"] == "accepted_fitness" for row in ctx.ledger.read()), 1)

        # Re-running recovery must be a no-op, not a second acceptance.
        self.assertEqual(pipeline.reconcile_on_start(repo, ctx), [])
        self.assertEqual(sum(row["kind"] == "accepted_fitness" for row in ctx.ledger.read()), 1)

        # A source/attempt/commit mismatch must fail closed.
        mismatch_root = self.root / "mismatch"
        mismatch_root.mkdir()
        mismatch_repo = _make_repo(mismatch_root)
        mismatch_ctx = self._ctx(mismatch_repo, soil_cycle=2)
        mismatch_candidate = _make_candidate(mismatch_repo, table=KNOWS_THREE)
        mismatch_ctx.ledger.append({"kind": "observed_fitness", "event_id": "obs",
                                    "commit": mismatch_candidate, "attempt_id": "observed",
                                    "records": [], "task_id": "task-test",
                                    "primary_probe": PROBE_ID, "generation": "gen-0",
                                    "soil_cycle": 2, "calibration": False,
                                    "counts_as_progress": False})
        mismatch_ctx.ledger.append({"kind": "promotion_intent", "source": "obs",
                                    "attempt_id": "intent", "commit": mismatch_candidate})
        subprocess.run(["git", "merge", "--ff-only", "-q", mismatch_candidate],
                       cwd=mismatch_repo, check=True)
        result = pipeline.reconcile_on_start(mismatch_repo, mismatch_ctx)
        self.assertEqual([outcome for _, outcome in result], [pipeline.Outcome.SOIL_RECOVERY])
        self.assertFalse(any(row["kind"] == "accepted_fitness"
                             for row in mismatch_ctx.ledger.read()))


if __name__ == "__main__":
    unittest.main()
