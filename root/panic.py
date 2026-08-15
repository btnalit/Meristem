"""ROOT OF TRUST -- panic. Never evolvable. Human is the only writer.

Deliberately placed outside the principle hierarchy: no agent code, tool,
prompt, or constitutional argument may prevent, delay, or circumvent panic.

Panic is not a restart. Panic is a full stop. Only a human clears the latch.

The latch file lives outside the repository so that a mutation of the
repository cannot remove it.

PLATFORM NOTE (P0, honest): on POSIX this kills the process group. On
Windows it falls back to `taskkill /F /T`. The Unix file-ownership isolation
of the latch (operator-owned, seed read-only) is NOT available on Windows --
that is a documented P1 hardening gap, not a solved problem.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

LATCH = pathlib.Path(
    os.environ.get("MERISTEM_CONTROL", pathlib.Path.home() / ".meristem-control")
) / "PANIC"


def engaged() -> bool:
    return LATCH.exists()


def engage(reason: str = "manual") -> None:
    LATCH.parent.mkdir(parents=True, exist_ok=True)
    LATCH.write_text(f"{reason}\n", encoding="utf-8")


def clear() -> None:
    """Only a human runs this. Nothing in the seed may call it."""
    LATCH.unlink(missing_ok=True)


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    action = argv[0] if argv else "engage"
    if action == "engage":
        engage(" ".join(argv[1:]) or "manual")
        for pid in argv[1:]:
            if pid.isdigit():
                kill_tree(int(pid))
        print(f"PANIC engaged: {LATCH}")
    elif action == "clear":
        clear()
        print("panic latch cleared")
    elif action == "status":
        print("ENGAGED" if engaged() else "clear")
    else:
        print("usage: panic.py [engage|clear|status]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
