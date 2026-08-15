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
import random
import pathlib
import subprocess
import sys
import time

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
    """Boot the candidate in an isolated worktree and prove it is alive.

    Bricking can be semantic, not just a crash -- so 'it starts' is not the
    test. Nor is the immune self-test alone: cycle 5 passed the fixtures and
    still broke a kernel invariant its own suite asserts (P-010). Alive means
    BOTH the fixtures fire and the kernel's own tests pass.
    """
    path = REPO.parent / "meristem-canary"
    subprocess.run(["git", "worktree", "remove", "--force", str(path)],
                   cwd=str(REPO), capture_output=True)
    git("worktree", "add", "-q", "--detach", str(path), commit)
    env = {**os.environ, "PYTHONPATH": str(path)}
    stages = (
        ("immune self-test", [sys.executable, "-m", "meristem.loop", "selftest"]),
        ("kernel tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
    )
    try:
        for label, argv in stages:
            result = subprocess.run(argv, cwd=str(path), capture_output=True,
                                    text=True, timeout=600, env=env)
            if result.returncode != 0:
                return False, f"{label} failed:\n{(result.stdout + result.stderr)[-600:]}"
        return True, "canary alive: immune self-test and kernel tests both pass"
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
    publish()
    return 0


def publish() -> None:
    """Push a promoted main to the configured remote.

    Substrate work, not kernel work: the seed proposes, the soil decides what
    becomes main -- and therefore what leaves this machine. Only ever a
    fast-forward of main, never a force, and only after canary and promotion
    have already passed.

    Opt-in by design. The constitution forbids publishing beyond this machine
    and its CONFIGURED remotes without human permission, so an unset
    MERISTEM_PUBLISH means the seed evolves locally and says so. A missing
    credential is reported, never fatal: failing to publish must not undo a
    promotion that already succeeded.
    """
    if os.environ.get("MERISTEM_PUBLISH", "").lower() not in ("1", "true", "yes"):
        print("publish: disabled (set MERISTEM_PUBLISH=1 to push promotions)")
        return
    result = subprocess.run(["git", "push", "origin", "main"], cwd=str(REPO),
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        print(f"published main -> origin ({resolve('HEAD')[:12]})")
    else:
        print(f"publish FAILED (promotion stands): {result.stderr.strip()[:200]}",
              file=sys.stderr)


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


#: Heartbeat bounds, in seconds. Jitter is not decoration: a fixed cadence
#: synchronises with the provider's rate-limit windows and turns every retry
#: into a thundering herd against the same boundary. A uniform interval
#: decorrelates the seed from anything with a period of its own.
BEAT_MIN, BEAT_MAX = 900, 2700


def heartbeat(beats: int, dry: bool = False) -> int:
    """Wake at irregular intervals and let the seed act unattended.

    Cadence is substrate work: deciding WHEN the seed may act without being
    asked is promotion-adjacent authority, so it lives in the soil. What
    HAPPENS on a beat is the seed's own -- run the next agenda item if there
    is one, otherwise reflect and propose its own. The soil supplies the
    pulse; it does not supply the thought.

    Panic is checked every beat, before anything else. A latch set while the
    seed sleeps must stop it at the next wake, not at the next convenient
    moment.
    """
    pressure_raised = False
    for beat in range(1, beats + 1):
        if panic.engaged():
            print("PANIC latch engaged; heartbeat stopping", file=sys.stderr)
            return 3
        print(f"--- beat {beat}/{beats} @ {time.strftime('%H:%M:%S')}", flush=True)
        # v3.1 wrote "Core Pressure >=0.9 主动触发 externalize, 不等确定性闸撞墙"
        # and nothing ever acted on it. 0.85 is the early-warning rung, below
        # the gate: pressure pre-empts ordinary work, because a kernel that
        # keeps growing while nothing watches ends at a wall no cycle can pass.
        pressure = core_pressure()
        # Pressure pre-empts work ONCE, then stands aside. Reflection only
        # proposes; the relief itself arrives as ordinary cycles executing the
        # agenda. Pre-empting on every beat while pressure stays high is a
        # livelock -- the more urgent it gets the less gets done -- which is
        # exactly what twelve wasted beats demonstrated (P-021).
        if pressure >= PRESSURE_MANDATE and not pressure_raised:
            print(f"    core pressure {pressure:.2f} >= {PRESSURE_MANDATE}"
                  " -- reflecting under pressure mandate (once)", flush=True)
            argv = ["reflect", "--pressure"]
            pressure_raised = True
        elif pending_task():
            argv = ["cycle"]
        elif pressure >= PRESSURE_MANDATE:
            # Still under pressure with an empty agenda: the last mandate
            # produced nothing actionable, so ask again rather than idle.
            print(f"    core pressure {pressure:.2f}, agenda empty"
                  " -- re-issuing the mandate", flush=True)
            argv = ["reflect", "--pressure"]
        else:
            argv = ["reflect"]
        result = subprocess.run([sys.executable, "-m", "meristem.loop", *argv],
                                cwd=str(REPO),
                                **({} if os.name == "nt" else {"start_new_session": True}))
        # Promote whenever a candidate EXISTS, not only when the beat's exit
        # code was clean. Cycle 120 passed both reviewers, was left unpromoted
        # because its beat returned non-zero, and cycle 121 then branched from
        # the unchanged HEAD and overwrote it -- a unanimously approved change
        # discarded in silence (P-024). A candidate is the gates' verdict; it
        # is not the exit status of the process that produced it.
        if resolve(CANDIDATE_REF):
            promote()
        if beat < beats and not dry:
            delay = random.randint(BEAT_MIN, BEAT_MAX)
            print(f"    next beat in {delay // 60}m {delay % 60}s", flush=True)
            time.sleep(delay)
    return 0


#: Early-warning rung. The design's gate is 0.90; acting only there means
#: acting once the wall is already in reach. 0.85 buys a campaign's worth of
#: room to externalize, prune or make a case before anything is blocked.
PRESSURE_MANDATE = 0.85


def core_pressure() -> float:
    """Kernel LOC over its cap, read the same way the gate reads it.

    The soil measures; it does not decide what to do about the number. What
    the seed proposes in response is the seed's, and the ranked menu
    (externalize > prune > compress > raise the cap) lives in the
    constitution, not here.
    """
    result = subprocess.run([sys.executable, "-m", "meristem.loop", "status"],
                            cwd=str(REPO), capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "core pressure" in line:
            try:
                return float(line.split(":")[1].split()[0])
            except (IndexError, ValueError):
                return 0.0
    return 0.0


def pending_task() -> bool:
    """Is there work already queued? Read-only, and failure means 'reflect'."""
    result = subprocess.run([sys.executable, "-m", "meristem.loop", "status"],
                            cwd=str(REPO), capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("open agenda item"):
            return "(none)" not in line
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="supervisor")
    parser.add_argument("command",
                        choices=["run", "promote", "rollback", "canary", "heartbeat"])
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--beats", type=int, default=8)
    parser.add_argument("--dry", action="store_true",
                        help="beat without sleeping, for verification")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args(argv)

    if args.command == "heartbeat":
        return heartbeat(args.beats, args.dry)
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
