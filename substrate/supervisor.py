"""SUBSTRATE -- runs the loop, owns promotion. Evolvable only by succession.

Promotion authority lives HERE, not in the kernel. The kernel proposes a
candidate; the substrate decides whether it becomes main. That separation is
structural: the code being reviewed must not be the code that admits it.

Before promoting, the substrate runs its OWN check with plain git and no
kernel code -- the candidate diff must not touch protected paths. The kernel
cannot rewrite this check, which is what makes "the gates are guarded" more
than a promise the gates make about themselves.

Promotion order is: prove it lives, THEN become main.

    candidate -> canary boot (dry-run one cycle) -> fast-forward + last-good

Auto-revert is the second line: if a promoted kernel fails health checks
afterwards, revert (never reset -- history is biography) and write the lesson
down. A silent rollback that loses the lesson violates the rationale rule.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from root import panic  # noqa: E402

CANDIDATE_REF = "refs/meristem/candidate"
LAST_GOOD = "refs/meristem/last-good"
PROTECTED = ("root/", "substrate/")
HEALTH_FAIL_LIMIT = 3


def git(*args, check=True, cwd=None) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd or REPO),
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def resolve(ref: str) -> str:
    """Commit id for a ref, or '' when it does not exist.

    `git rev-parse <missing-ref>` echoes the ref name back on stdout, so a
    naive read mistakes 'no candidate' for 'candidate present'. --verify
    --quiet is the honest form.
    """
    return git("rev-parse", "--verify", "--quiet", ref, check=False)


def guard_protected(base: str, candidate: str) -> list[str]:
    """The substrate's own protected-path check. Plain git, no kernel code."""
    changed = git("diff", "--name-only", f"{base}..{candidate}").splitlines()
    return [path for path in changed if path.startswith(PROTECTED)]


def canary(commit: str) -> tuple[bool, str]:
    """Boot the candidate in an isolated worktree and dry-run one cycle.

    Bricking can be semantic, not just a crash -- so 'it starts' is not the
    test; 'it can execute its own self-test' is.
    """
    path = REPO.parent / "meristem-canary"
    subprocess.run(["git", "worktree", "remove", "--force", str(path)],
                   cwd=str(REPO), capture_output=True)
    git("worktree", "add", "-q", "--detach", str(path), commit)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "meristem.loop", "selftest"],
            cwd=str(path), capture_output=True, text=True, timeout=300,
            env={**os.environ, "PYTHONPATH": str(path)},
        )
        ok = result.returncode == 0
        return ok, (result.stdout + result.stderr).strip()[:600]
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"canary boot failed: {exc}"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(path)],
                       cwd=str(REPO), capture_output=True)


def promote() -> int:
    if panic.engaged():
        print("PANIC latch is engaged; refusing to promote", file=sys.stderr)
        return 3
    candidate = resolve(CANDIDATE_REF)
    if not candidate:
        print("no candidate awaiting promotion")
        return 0

    offenders = guard_protected("HEAD", candidate)
    if offenders:
        print(f"REFUSED: candidate touches protected paths: {offenders}", file=sys.stderr)
        return 4

    ok, output = canary(candidate)
    if not ok:
        print(f"REFUSED: canary boot failed\n{output}", file=sys.stderr)
        return 5

    git("merge", "--ff-only", candidate)
    git("update-ref", LAST_GOOD, candidate)
    git("update-ref", "-d", CANDIDATE_REF, check=False)
    print(f"promoted {candidate[:12]} -> main; last-good updated")
    return 0


def rollback(reason: str) -> int:
    """Revert, never reset. The failure is recorded, not erased."""
    last_good = resolve(LAST_GOOD)
    head = git("rev-parse", "HEAD")
    if not last_good or last_good == head:
        print("nothing to roll back to")
        return 1
    git("revert", "--no-edit", "--no-commit", f"{last_good}..HEAD", check=False)
    git("-c", "user.name=meristem-substrate",
        "-c", "user.email=substrate@localhost",
        "commit", "-q", "-m", f"revert: auto-rollback to last-good\n\nreason: {reason}")
    print(f"rolled back to {last_good[:12]}; reason recorded")
    return 0


def run_loop(cycles: int) -> int:
    """Spawn the kernel as a child process group, one cycle at a time."""
    failures = 0
    for _ in range(cycles):
        if panic.engaged():
            print("PANIC latch engaged; stopping", file=sys.stderr)
            return 3
        kwargs = {} if os.name == "nt" else {"start_new_session": True}
        result = subprocess.run([sys.executable, "-m", "meristem.loop", "cycle"],
                                cwd=str(REPO), **kwargs)
        if result.returncode == 0:
            failures = 0
            promote()
        else:
            failures += 1
            if failures >= HEALTH_FAIL_LIMIT:
                return rollback(f"{failures} consecutive kernel failures")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="supervisor")
    parser.add_argument("command", choices=["run", "promote", "rollback", "canary"])
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_loop(args.cycles)
    if args.command == "promote":
        return promote()
    if args.command == "rollback":
        return rollback(args.reason)
    ok, output = canary(git("rev-parse", "HEAD"))
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
