import os
import shlex
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "substrate" / "run-soil.sh"


class CredentialAdapterTests(unittest.TestCase):
    def _env_file(self, root: Path, name: str, value: str = "adapter-test-token") -> Path:
        path = root / name
        path.write_text(f"{name}={value}\n", encoding="utf-8")
        path.chmod(0o600)
        os.chown(path, 0, 0)
        return path

    def _run(self, env_file: Path, runtime: Path, mode: str, *args: str,
             inherited: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({
            "MERISTEM_ENV_FILE": str(env_file),
            "MERISTEM_RUNTIME_DIR": str(runtime),
        })
        if inherited:
            env.update(inherited)
        return subprocess.run(
            [str(RUNNER), "--mode", mode, "--", *args],
            cwd=REPO,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_each_allowlisted_mode_runs_supervisor_and_cleans(self):
        mappings = {
            "agnes-temporary": "AGNES_API_KEY",
            "openrouter-free": "OPENROUTER_API_KEY",
            "sensenova": "SENSENOVA_API_KEY",
        }
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            for mode, source_name in mappings.items():
                runtime = root / mode
                runtime.mkdir()
                env_file = self._env_file(root, source_name)
                result = self._run(env_file, runtime, mode, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(runtime.iterdir()), [])
                self.assertNotIn("adapter-test-token", result.stdout + result.stderr)

    def test_inherited_provider_key_does_not_satisfy_other_mode(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            env_file = self._env_file(root, "OPENROUTER_API_KEY")
            result = self._run(
                env_file,
                runtime,
                "agnes-temporary",
                "--help",
                inherited={"AGNES_API_KEY": "inherited-must-not-work"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required provider credential is absent", result.stderr)
            self.assertEqual(list(runtime.iterdir()), [])
            self.assertNotIn("inherited-must-not-work", result.stdout + result.stderr)

    def test_startup_removes_stale_credential_file(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            stale = runtime / "credential.stale"
            stale.write_text("stale-test-token\n", encoding="utf-8")
            stale.chmod(0o600)
            os.chown(stale, 997, 997)
            env_file = self._env_file(root, "AGNES_API_KEY")
            result = self._run(env_file, runtime, "agnes-temporary", "--help")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual(list(runtime.iterdir()), [])

    def test_child_allowlist_and_runtime_file_metadata(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            fake_bin = root / "bin"
            fake_bin.mkdir()
            report = root / "report"
            fake_python = fake_bin / "python3"
            report_q = shlex.quote(str(report))
            fake_python.write_text(
                "#!/bin/sh\n"
                f"stat -c '%U:%G %a' \"$MERISTEM_CREDENTIALS_FILE\" > {report_q}\n"
                f"env | cut -d= -f1 | sort >> {report_q}\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            env_file = root / "env"
            env_file.write_text(
                "AGNES_API_KEY=adapter-test-token\n"
                "MERISTEM_VAULT=/tmp/meristem-vault\n"
                "MERISTEM_WEBHOOK_URL=source-only-secret\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            os.chown(env_file, 0, 0)
            env = dict(os.environ)
            env.update({
                "MERISTEM_ENV_FILE": str(env_file),
                "MERISTEM_RUNTIME_DIR": str(runtime),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            })
            result = subprocess.run(
                [str(RUNNER), "--mode", "agnes-temporary"],
                cwd=REPO,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = report.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "soil:soil 600")
            names = set(lines[1:])
            self.assertIn("HOME", names)
            self.assertIn("MERISTEM_CREDENTIALS_FILE", names)
            self.assertIn("MERISTEM_MODEL_MODE", names)
            self.assertIn("PATH", names)
            self.assertIn("PYTHONPATH", names)
            self.assertIn("MERISTEM_VAULT", names)
            self.assertNotIn("AGNES_API_KEY", names)
            self.assertNotIn("OPENROUTER_API_KEY", names)
            self.assertNotIn("SENSENOVA_API_KEY", names)
            self.assertNotIn("MERISTEM_WEBHOOK_URL", names)
            self.assertEqual(list(runtime.iterdir()), [])

    def test_nonzero_supervisor_exit_cleans_file(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            env_file = self._env_file(root, "AGNES_API_KEY")
            result = self._run(env_file, runtime, "agnes-temporary", "--not-a-real-option")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(runtime.iterdir()), [])

    def test_sigterm_cleans_file_before_exit(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_python = fake_bin / "python3"
            fake_python.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
            fake_python.chmod(0o755)
            env_file = self._env_file(root, "AGNES_API_KEY")
            env = dict(os.environ)
            env.update({
                "MERISTEM_ENV_FILE": str(env_file),
                "MERISTEM_RUNTIME_DIR": str(runtime),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            })
            proc = subprocess.Popen(
                [str(RUNNER), "--mode", "agnes-temporary"],
                cwd=REPO,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not list(runtime.iterdir()) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(list(runtime.iterdir()))
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
                self.assertEqual(proc.returncode, 143)
                self.assertEqual(list(runtime.iterdir()), [])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()

    def test_source_file_never_executes_commands(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            marker = root / "executed"
            env_file = root / "env"
            env_file.write_text(
                f"AGNES_API_KEY=adapter-test-token\ntouch {marker}\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            os.chown(env_file, 0, 0)
            result = self._run(env_file, runtime, "agnes-temporary", "--help")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(list(runtime.iterdir()), [])
            self.assertNotIn("adapter-test-token", result.stdout + result.stderr)

    def test_source_file_must_be_root_private_regular_non_symlink(self):
        with tempfile.TemporaryDirectory(prefix="meristem-adapter-") as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            env_file = self._env_file(root, "AGNES_API_KEY")
            env_file.chmod(0o644)
            result = self._run(env_file, runtime, "agnes-temporary", "--help")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("root:root 0600", result.stderr)
            self.assertEqual(list(runtime.iterdir()), [])

            env_file.unlink()
            target = root / "real-env"
            target.write_text("AGNES_API_KEY=adapter-test-token\n", encoding="utf-8")
            target.chmod(0o600)
            env_file.symlink_to(target)
            result = self._run(env_file, runtime, "agnes-temporary", "--help")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or symlinked", result.stderr)
            self.assertEqual(list(runtime.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
