"""Tests for the `MERISTEM_MODEL_GATEWAY` injection fix in
substrate/supervisor.py (S7, v5 spec §13.3 table C / §18 v5.9 row).

`_seed_candidate()` used to build the seed subprocess's environment purely
from `probe_runner._sandboxed_env()` (a fixed allowlist) plus a couple of
explicitly-added keys (`PYTHONPATH`, `MERISTEM_SOIL_CYCLE`). Because the
allowlist does not include `MERISTEM_MODEL_GATEWAY`, even a *correctly set*
value of that variable in the supervisor's own process environment would
have been silently dropped -- indistinguishable, from the seed's point of
view, from the variable never having been set at all
(`meristem.llm.call_model` fails closed to `gateway_not_injected` either
way). The fix adds it explicitly, the same way `PYTHONPATH` and
`MERISTEM_SOIL_CYCLE` already are.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from substrate import probe_runner as _probe_runner  # noqa: E402
from substrate import supervisor  # noqa: E402

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


class AllowlistWouldSilentlyDropItTests(unittest.TestCase):
    """Demonstrates *why* the fix is necessary: the allowlist alone is not
    sufficient, so the variable must be added explicitly."""

    def test_sandboxed_env_allowlist_does_not_include_the_gateway_var(self):
        with mock.patch.dict(os.environ, {"MERISTEM_MODEL_GATEWAY": "python -m x"}):
            self.assertNotIn("MERISTEM_MODEL_GATEWAY", _probe_runner._sandboxed_env())


class ModelGatewayEntrypointShapeTests(unittest.TestCase):
    """The injected value must be an absolute-path invocation, not a
    PYTHONPATH-relative `-m` import -- see the docstring at
    supervisor.MODEL_GATEWAY_ENTRYPOINT for why a worktree-relative import
    would resolve state/ and soil/ to the wrong (candidate) tree."""

    def test_entrypoint_is_an_absolute_path_to_the_real_repo(self):
        parts = supervisor.MODEL_GATEWAY_ENTRYPOINT.split()
        self.assertEqual(parts[0], sys.executable)
        gateway_path = Path(" ".join(parts[1:]))
        self.assertTrue(gateway_path.is_absolute())
        self.assertEqual(gateway_path, REPO / "substrate" / "model_gateway_client.py")
        self.assertTrue(gateway_path.is_file())


class SeedCandidateInjectsGatewayVarTests(unittest.TestCase):
    """End-to-end through the real `_seed_candidate()` env-building code,
    with only the final `meristem.loop cycle` subprocess.run call
    intercepted (git subprocess calls run for real against a throwaway
    temp repo)."""

    def test_env_passed_to_the_seed_subprocess_contains_the_gateway_var(self):
        real_run = subprocess.run
        captured = []

        def fake_run(cmd, *args, **kwargs):
            if cmd[:1] == ["git"]:
                return real_run(cmd, *args, **kwargs)
            captured.append(kwargs.get("env"))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            real_run(["git", "-c", "init.defaultBranch=main", "init", "-q"],
                     cwd=str(repo), env=GIT_ENV, check=True)
            (repo / "README.md").write_text("x", encoding="utf-8")
            real_run(["git", "add", "."], cwd=str(repo), env=GIT_ENV, check=True)
            real_run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), env=GIT_ENV,
                     check=True)

            ctx = SimpleNamespace(soil_cycle=1, generation="gen-test",
                                  ledger=SimpleNamespace(append=lambda e: "ev-x"))
            task = SimpleNamespace(task_id="t1")

            worktree = None
            try:
                fake_gateway = SimpleNamespace(poll=lambda: 0)
                with mock.patch("substrate.supervisor._start_model_gateway", return_value=fake_gateway), \
                     mock.patch("substrate.supervisor.subprocess.run", side_effect=fake_run):
                    _commit, worktree = supervisor._seed_candidate(repo, ctx, task)
            finally:
                if worktree is not None:
                    supervisor._drop_worktree(repo, worktree)

        self.assertEqual(len(captured), 1, "expected exactly one non-git subprocess call")
        env = captured[0]
        self.assertIn("MERISTEM_MODEL_GATEWAY", env)
        self.assertIn("/tmp/meristem-worker-", env["MERISTEM_MODEL_GATEWAY"])
        self.assertTrue(env["MERISTEM_MODEL_GATEWAY"].endswith("/substrate/model_gateway_client.py"))
        # The seed's own model-key/vault secrets must still be absent --
        # this fix must not have widened the env beyond the one new key.
        self.assertNotIn("MERISTEM_VAULT", env)
        self.assertNotIn("SENSENOVA_API_KEY", env)

    def test_seed_receives_socket_only_not_credential_pointer(self):
        real_run = subprocess.run
        captured = []

        def fake_run(cmd, *args, **kwargs):
            if cmd[:1] == ["git"]:
                return real_run(cmd, *args, **kwargs)
            captured.append(kwargs.get("env"))
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"MERISTEM_CREDENTIALS_FILE": "/soil/provider.key",
                             "SENSENOVA_API_KEY": "must-not-be-forwarded"}, clear=False):
            repo = Path(tmp) / "repo"
            repo.mkdir()
            real_run(["git", "-c", "init.defaultBranch=main", "init", "-q"],
                     cwd=str(repo), env=GIT_ENV, check=True)
            (repo / "README.md").write_text("x", encoding="utf-8")
            real_run(["git", "add", "."], cwd=str(repo), env=GIT_ENV, check=True)
            real_run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), env=GIT_ENV,
                     check=True)
            ctx = SimpleNamespace(soil_cycle=1, generation="gen-test",
                                  ledger=SimpleNamespace(append=lambda e: "ev-x"))
            task = SimpleNamespace(task_id="t1")
            worktree = None
            try:
                fake_gateway = SimpleNamespace(poll=lambda: 0)
                with mock.patch("substrate.supervisor._start_model_gateway", return_value=fake_gateway), \
                     mock.patch("substrate.supervisor.subprocess.run", side_effect=fake_run):
                    _commit, worktree = supervisor._seed_candidate(repo, ctx, task)
            finally:
                if worktree is not None:
                    supervisor._drop_worktree(repo, worktree)

        self.assertEqual(len(captured), 1)
        env = captured[0]
        self.assertIn("MERISTEM_MODEL_SOCKET", env)
        self.assertNotIn("MERISTEM_CREDENTIALS_FILE", env)
        self.assertNotIn("SENSENOVA_API_KEY", env)


class WorkerSurfaceIsolationTests(unittest.TestCase):
    def test_worker_surface_has_no_soil_state_vault_or_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            for rel in ("meristem", "body/organs", "seed", "soil", "state", "vault", ".git"):
                (source / rel).mkdir(parents=True, exist_ok=True)
            (source / "soil/model-policy.toml").write_text("private", encoding="utf-8")
            (source / "state/soil-ledger.jsonl").write_text("private", encoding="utf-8")
            (source / "vault/secret").write_text("private", encoding="utf-8")
            (source / "body/organs/a.py").write_text("old", encoding="utf-8")
            worker = Path(tmp) / "worker"
            supervisor._copy_worker_surface(source, worker)
            self.assertFalse((worker / "soil").exists())
            self.assertFalse((worker / "state").exists())
            self.assertFalse((worker / "vault").exists())
            self.assertFalse((worker / ".git").exists())
            self.assertTrue((worker / "body/organs/a.py").is_file())

    def test_worker_surface_rejects_nested_symlink_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            (source / "meristem").mkdir(parents=True)
            (source / "body/organs").mkdir(parents=True)
            (source / "seed").mkdir()
            (source / "soil").mkdir()
            (source / "soil/secret").write_text("private", encoding="utf-8")
            try:
                (source / "seed/leak").symlink_to(source / "soil", target_is_directory=True)
            except OSError:
                self.skipTest("platform does not permit symlink creation")
            with self.assertRaises(OSError):
                supervisor._copy_worker_surface(source, Path(tmp) / "worker")

    def test_recovery_only_copies_declared_worker_writable_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp) / "worker"
            worktree = Path(tmp) / "worktree"
            (worker / "body/organs").mkdir(parents=True)
            (worker / "soil").mkdir()
            (worker / "body/organs/a.py").write_text("new", encoding="utf-8")
            (worker / "soil/model-policy.toml").write_text("attack", encoding="utf-8")
            (worktree / "body/organs").mkdir(parents=True)
            (worktree / "soil").mkdir()
            (worktree / "body/organs/a.py").write_text("old", encoding="utf-8")
            (worktree / "soil/model-policy.toml").write_text("private", encoding="utf-8")
            changed = supervisor._recover_worker_changes(worker, worktree)
            self.assertEqual(changed, ["body/organs/a.py"])
            self.assertEqual((worktree / "body/organs/a.py").read_text(), "new")
            self.assertEqual((worktree / "soil/model-policy.toml").read_text(), "private")
            (worker / "body/organs/a.py").unlink()
            changed = supervisor._recover_worker_changes(worker, worktree)
            self.assertEqual(changed, ["body/organs/a.py"])
            self.assertFalse((worktree / "body/organs/a.py").exists())

    def test_recovery_rejects_target_symlink_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp) / "worker"
            worktree = Path(tmp) / "worktree"
            (worker / "body/organs").mkdir(parents=True)
            (worktree / "body/organs").mkdir(parents=True)
            outside = Path(tmp) / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            (worktree / "body/organs/x").symlink_to(outside)
            (worker / "body/organs/x").write_text("attack", encoding="utf-8")
            with self.assertRaises(OSError):
                supervisor._recover_worker_changes(worker, worktree)
            self.assertEqual(outside.read_text(), "private")


if __name__ == "__main__":
    unittest.main()
