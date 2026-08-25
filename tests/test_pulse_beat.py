"""Pure-bash-behavior tests for substrate/pulse-beat.sh (P0-c).

Runs `pulse-beat.sh` against stubbed `run-soil.sh` / `systemctl` / `curl` on
PATH, with MERISTEM_RUNTIME_DIR and MERISTEM_ENV_FILE overridden to a tmp
directory -- nothing here touches real paths, real systemd, or the network.

`flock` is required for the wrapper to get past its own lock (a missing
`flock` binary is indistinguishable, from the wrapper's point of view, from
"lock busy", so it exits 0 without ever running the beat). It is not present
on this project's Windows dev sandbox (MSYS bash); the execution tests
below skip there and run for real on the Linux deployment target.
"""
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PULSE_BEAT = REPO / "substrate" / "pulse-beat.sh"

HAVE_BASH = shutil.which("bash") is not None
HAVE_FLOCK = shutil.which("flock") is not None


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@unittest.skipUnless(HAVE_BASH, "bash unavailable on this platform")
class SyntaxTests(unittest.TestCase):
    def test_bash_syntax_check(self):
        result = subprocess.run(["bash", "-n", str(PULSE_BEAT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(HAVE_BASH, "bash unavailable on this platform")
@unittest.skipUnless(HAVE_FLOCK, "flock unavailable on this platform")
class PulseBeatExecutionTests(unittest.TestCase):
    """Each test builds its own tmp `substrate/` directory containing a copy
    of the real pulse-beat.sh next to a stubbed run-soil.sh, so
    `"$SCRIPT_DIR/run-soil.sh"` resolves to the stub instead of the real
    root-only credential adapter."""

    def _setup(self, tmp: Path, *, run_soil_rc: int) -> dict:
        substrate = tmp / "substrate"
        substrate.mkdir()
        pulse_beat = substrate / "pulse-beat.sh"
        shutil.copy2(PULSE_BEAT, pulse_beat)
        pulse_beat.chmod(pulse_beat.stat().st_mode | stat.S_IEXEC)
        _write_executable(substrate / "run-soil.sh", f"#!/bin/sh\nexit {run_soil_rc}\n")

        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        systemctl_calls = tmp / "systemctl.calls"
        curl_calls = tmp / "curl.calls"
        # Paths embedded into shell script content must be POSIX-form and
        # shell-quoted -- an unquoted Windows backslash path is mangled by
        # /bin/sh (backslash-escape mis-parse), even though the *stub's own
        # location* resolves fine via MSYS's PATH auto-translation.
        systemctl_calls_q = shlex.quote(systemctl_calls.as_posix())
        curl_calls_q = shlex.quote(curl_calls.as_posix())
        _write_executable(
            bin_dir / "systemctl",
            f"#!/bin/sh\necho \"$@\" >> {systemctl_calls_q}\nexit 0\n")
        _write_executable(
            bin_dir / "curl",
            f"#!/bin/sh\necho \"$@\" >> {curl_calls_q}\nexit 0\n")

        runtime_dir = tmp / "runtime"
        env = dict(os.environ)
        env["MERISTEM_RUNTIME_DIR"] = str(runtime_dir)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        return {
            "pulse_beat": pulse_beat,
            "env": env,
            "systemctl_calls": systemctl_calls,
            "curl_calls": curl_calls,
        }

    def test_nonzero_rc_disables_timer_and_notifies(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            ctx = self._setup(tmp, run_soil_rc=2)
            env_file = tmp / "env"
            env_file.write_text(
                "MERISTEM_WEBHOOK_URL=http://example.invalid/webhook\n",
                encoding="utf-8")
            ctx["env"]["MERISTEM_ENV_FILE"] = str(env_file)

            result = subprocess.run(["bash", str(ctx["pulse_beat"])],
                                    env=ctx["env"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertTrue(ctx["systemctl_calls"].is_file())
            self.assertIn("disable --now meristem-pulse.timer",
                          ctx["systemctl_calls"].read_text(encoding="utf-8"))
            self.assertTrue(ctx["curl_calls"].is_file())
            curl_argv = ctx["curl_calls"].read_text(encoding="utf-8")
            self.assertIn("http://example.invalid/webhook", curl_argv)
            self.assertIn("rc=2", curl_argv)

    def test_webhook_with_executable_syntax_is_ignored_beat_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            ctx = self._setup(tmp, run_soil_rc=2)
            env_file = tmp / "env"
            env_file.write_text(
                "MERISTEM_WEBHOOK_URL=http://evil.example/$(whoami)\n",
                encoding="utf-8")
            ctx["env"]["MERISTEM_ENV_FILE"] = str(env_file)

            result = subprocess.run(["bash", str(ctx["pulse_beat"])],
                                    env=ctx["env"], capture_output=True, text=True)
            # The beat still ran (rc=2 from the stub propagated) even though
            # the webhook value itself was rejected.
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("executable syntax", result.stderr)
            self.assertTrue(ctx["systemctl_calls"].is_file())
            self.assertFalse(ctx["curl_calls"].exists())

    def test_rc_zero_makes_no_systemctl_or_curl_calls(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            ctx = self._setup(tmp, run_soil_rc=0)
            env_file = tmp / "env"
            env_file.write_text(
                "MERISTEM_WEBHOOK_URL=http://example.invalid/webhook\n",
                encoding="utf-8")
            ctx["env"]["MERISTEM_ENV_FILE"] = str(env_file)

            result = subprocess.run(["bash", str(ctx["pulse_beat"])],
                                    env=ctx["env"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ctx["systemctl_calls"].exists())
            self.assertFalse(ctx["curl_calls"].exists())


if __name__ == "__main__":
    unittest.main()
