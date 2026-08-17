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
import datetime
import json
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
JOURNAL = REPO / "state" / "journal.jsonl"
PROPOSALS = REPO / "state" / "proposals.md"
AGENDA = REPO / "control" / "agenda.md"

PROPOSAL_GUARDED_SUBSTRATE = (
    "root/", "substrate/", "meristem/gates/",
    "control/constitution.md", "control/checklists.md",
)
CAP_MARKERS = (
    "kernel_loc_cap", "loc cap", "raise the cap", "increase the cap",
    "lower the cap", "内核上限", "扩容",
)


def _has_unactioned_proposals() -> bool:
    """True if proposals.md has any lines that look like proposals."""
    if not PROPOSALS.exists():
        return False
    for line in PROPOSALS.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("- [ ] "):
            return True
    return False


def _is_guarded_proposal(text: str) -> bool:
    """Substrate's own copy of the guarded-path check.

    MUST NOT import from meristem.loop — the seed can legally mutate loop.py,
    so importing eligibility from the kernel would let the seed relax the
    fence in a way reviewers could miss.
    """
    lowered = text.lower()
    if any(p in lowered for p in PROPOSAL_GUARDED_SUBSTRATE):
        return True
    return any(m in lowered for m in CAP_MARKERS)


SEAT_LOCK = REPO / "state" / "approval_seat.rung1.lock"
DEMOTION_STREAK = 3
#: Consecutive accepted cycles after a demotion that re-arm rung 2.
REARM_STREAK = 3


def _cycles_since_demotion() -> list:
    """Cycle outcomes journalled after the MOST RECENT demotion.

    Resetting on every demotion matters: counting from the first one would
    let cycles earned before a later demotion pay for it.
    """
    if not JOURNAL.exists():
        return []
    outcomes: list = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "seat_change" and row.get("to_rung") == 1:
            outcomes = []
        elif row.get("kind") == "cycle" and row.get("outcome") in (
                "candidate", "rejected", "parked"):
            outcomes.append(row.get("outcome"))
    return outcomes


def _check_rearm() -> bool:
    """Restore rung 2 once the seed has earned it back. True if still locked.

    A gate that only ever tightens is the ossified layer 6.1 exists to
    prevent: the human seat is a PROSTHETIC, so it needs an exit condition
    that does not require a human to remember to delete a file. Demotion
    stays automatic and needs no evidence -- the ratchet turns freely toward
    safety -- but the way back is now evidence-backed rather than manual.
    """
    outcomes = _cycles_since_demotion()
    if len(outcomes) < REARM_STREAK or any(
            o != "candidate" for o in outcomes[-REARM_STREAK:]):
        return True
    SEAT_LOCK.unlink(missing_ok=True)
    _journal({"kind": "seat_change", "seat": "proposal_approval",
              "from_rung": 1, "to_rung": 2,
              "reason": f"{REARM_STREAK} consecutive accepted cycles since demotion"})
    notify("seat_rearm",
           f"Approval seat restored to rung 2: {REARM_STREAK} consecutive "
           f"cycles accepted since the demotion. No action needed.")
    return False


def _check_demotion() -> bool:
    """Scan journal for consecutive self-promoted task failures.

    If the N most recent auto_promote tasks all ended rejected/parked with
    none accepted, write a lock file and notify. Returns True if demoted.
    """
    if SEAT_LOCK.exists():
        return _check_rearm()
    if not JOURNAL.exists():
        return False
    promotes = []
    outcomes = {}
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "auto_promote":
            promotes.append(row.get("task", "")[:80])
        elif row.get("kind") == "cycle" and row.get("outcome") in ("candidate", "rejected", "parked"):
            why = row.get("why", "")[:80]
            outcomes[why] = row.get("outcome")
    if len(promotes) < DEMOTION_STREAK:
        return False
    recent = promotes[-DEMOTION_STREAK:]
    for task in recent:
        if outcomes.get(task) == "candidate":
            return False
    if all(outcomes.get(t) in ("rejected", "parked") for t in recent):
        SEAT_LOCK.write_text(
            f"Demoted: {DEMOTION_STREAK} consecutive self-promoted tasks "
            f"rejected/parked. Delete this file to re-arm rung 2.\n",
            encoding="utf-8",
        )
        _journal({"kind": "seat_change", "seat": "proposal_approval",
                  "from_rung": 2, "to_rung": 1,
                  "reason": f"{DEMOTION_STREAK} consecutive failures"})
        notify("seat_demotion",
               f"Approval seat demoted to rung 1: {DEMOTION_STREAK} consecutive "
               f"self-promoted tasks failed. Delete {SEAT_LOCK.name} to re-arm.")
        return True
    return False


def _auto_promote() -> bool:
    """Move the top eligible proposal from proposals.md to agenda.md.

    Returns True if a proposal was promoted, False if none eligible.
    Guarded-ground and cap-change proposals stay in proposals.md (or mailbox)
    for human review — the code gate is untouched, only scheduling autonomy
    is granted. The approval seat moved rung 1 → 2 per decisions.jsonl.
    """
    if _check_demotion():
        return False
    if not PROPOSALS.exists():
        return False
    lines = PROPOSALS.read_text(encoding="utf-8").splitlines()
    promoted_text = None
    remaining = []
    for line in lines:
        stripped = line.strip()
        if promoted_text is None and stripped.startswith("- [ ] "):
            proposal = stripped[6:]
            if not _is_guarded_proposal(proposal):
                promoted_text = proposal
                continue
        remaining.append(line)
    if promoted_text is None:
        return False
    PROPOSALS.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    agenda = AGENDA.read_text(encoding="utf-8") if AGENDA.exists() else ""
    marker = f"- [ ] {promoted_text}\n"
    done_marker = f"- [x] {promoted_text}\n"
    if marker in agenda or done_marker in agenda:
        print(f"    dedup-drop (already in agenda): {promoted_text[:120]}", flush=True)
        return False
    with AGENDA.open("a", encoding="utf-8") as f:
        f.write(marker)
    _journal({"kind": "auto_promote", "task": promoted_text[:200],
              "why": "approval seat rung 2: non-guarded proposal auto-promoted"})
    notify("auto_promote", f"Self-promoted to agenda:\n  {promoted_text[:200]}")
    print(f"    auto-promoted: {promoted_text[:120]}", flush=True)
    return True


def notify(event: str, message: str) -> None:
    """Send a notification via configured webhook. Opt-in, like publish."""
    url = os.environ.get("MERISTEM_WEBHOOK_URL", "")
    if not url:
        return
    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": f"[meristem:{event}] {message}"},
    }).encode("utf-8")
    import urllib.request
    req = urllib.request.Request(url, data=payload,
                                headers={"Content-Type": "application/json"},
                                method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print(f"notify failed ({event}): {exc}", file=sys.stderr)


def _journal(record: dict) -> None:
    """Append one record to the journal. The substrate writes its own records
    without importing kernel code -- the structural separation is load-bearing."""
    record = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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

    subject = git("log", "-1", "--format=%s", candidate)
    import re
    m = re.match(r"cycle\s+\d+:\s*", subject)
    why = subject[m.end():] if m else ""

    ok, output = canary(candidate)
    if not ok:
        print(f"REFUSED: canary boot failed\n{output}", file=sys.stderr)
        _journal({"kind": "canary_reject", "commit": candidate[:12],
                  "why": why, "reason": output[-400:]})
        git("update-ref", "-d", CANDIDATE_REF, check=False)
        return 5

    git("merge", "--ff-only", candidate)
    git("update-ref", LAST_GOOD, candidate)
    git("update-ref", "-d", CANDIDATE_REF, check=False)
    _journal({"kind": "promoted", "commit": candidate[:12], "why": why})
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
    notify("rollback", f"Auto-rollback to {last_good[:12]}: {reason}")
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


def _pressure_reflected_today() -> bool:
    """Check if a pressure reflect already ran today (survives restarts)."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    if not JOURNAL.exists():
        return False
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = row.get("ts", "")
        if ts.startswith(today) and row.get("why") == "reflect --pressure":
            return True
    return False


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
    pressure_raised = _pressure_reflected_today()
    failures = 0
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
        elif pressure >= PRESSURE_MANDATE and not _has_unactioned_proposals():
            # Still under pressure with an empty agenda AND no proposals
            # waiting: the last mandate produced nothing, so ask again.
            # But if proposals exist the seed already has an answer that
            # nobody consumed -- re-asking is the livelock (P-021).
            print(f"    core pressure {pressure:.2f}, agenda empty, no proposals"
                  " -- re-issuing the mandate", flush=True)
            argv = ["reflect", "--pressure"]
        elif _auto_promote():
            argv = ["cycle"]
        else:
            argv = ["reflect"]
        result = subprocess.run([sys.executable, "-m", "meristem.loop", *argv],
                                cwd=str(REPO),
                                **({} if os.name == "nt" else {"start_new_session": True}))
        if result.returncode != 0:
            failures += 1
            if failures >= HEALTH_FAIL_LIMIT:
                return rollback(f"{failures} consecutive heartbeat failures")
        else:
            failures = 0
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
    subprocess.run([sys.executable, "-m", "meristem.loop", "report"],
                    cwd=str(REPO))
    pressure = core_pressure()
    summary = ""
    report_path = REPO / "REPORT.md"
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8")
        summary = text[:800] if len(text) > 800 else text
    notify("heartbeat_done",
           f"Heartbeat finished ({beats} beats). Pressure: {pressure:.2f}\n"
           f"--- REPORT ---\n{summary}")
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
