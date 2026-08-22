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
import hashlib
import json
import os
import random
import pathlib
import re
import shutil
import subprocess
import sys
import time
import traceback

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

#: Keep in sync with meristem.loop.PROPOSAL_GUARDED by hand, like every other
#: mirrored constant here: the seed may legally mutate loop.py, so importing
#: the fence from the kernel would let it relax the soil's copy too.
#: A FILE in guarded ground holds a proposal; a bare directory mention does
#: not. Cycle 385's ring audit found four proposals held for listing
#: "protected/root/substrate/closure" among a classifier's keywords, or for
#: quoting `grep -rn ... meristem/ substrate/` as evidence. Neither asked to
#: change anything in the soil, and a held proposal has no way back.
PROPOSAL_GUARDED_SUBSTRATE = re.compile(r"(?:root|substrate)/[\w./-]*\w+\.\w+|meristem/gates/"
                                        r"[\w./-]*\w+\.py|control/(?:constitution|checklists)\.md")
#: Keep in sync with meristem.loop.CAP_PROPOSAL_MARKERS by hand. Duplicated
#: on purpose (the seed may mutate loop.py) which means the two can DRIFT --
#: and a marker the soil lacks is a hole in the very copy that exists to
#: survive the kernel being wrong. A test asserts both recognise the same
#: strings. The regex arm is duplicated too, for the same reason.
CAP_MARKERS = (
    "kernel_loc_cap", "loc cap", "raise the cap", "increase the cap",
    "lower the cap", "内核上限", "扩容", "上限",
)
#: Keep in step with meristem.loop.CAP_INTENT. Tuned for PRECISION: after
#: P-040 a false positive is refused-and-dropped (unrecoverable) while a miss
#: still faces the review panel, so "new" and "move" are deliberately absent
#: -- they are the mandate's own words for relief -- and the numeric branch
#: takes only "to", since "under the cap of 3000" describes the budget rather
#: than asking to move it.
CAP_WORD = re.compile(
    r"\b(raise|raising|increase|increasing|lower|lowering"
    r"|adjust|adjusting|change|changing|set|setting)\b[^.]{0,20}?\bcaps?\b"
    r"|\bnew\s+caps?\b"
    r"|\bcaps?\b\s*to\s*\d"
)
CAP_HOME_SUBSTRATE = "meristem/gates/deterministic.py"
#: Keep in step with meristem.loop.CAP_CITE: a per-file LOC citation.
CAP_CITE_SUBSTRATE = re.compile(r"meristem/[\w/.-]+\.py[\s:]*\d+")


#: Tracked files the loop writes in the MAIN worktree: reflect appends to
#: proposals.md and mailbox.md, _auto_promote rewrites proposals.md and
#: agenda.md, and a mutation may append to the registers.
STATE_FILES = ("control/agenda.md", "state/proposals.md", "state/mailbox.md",
               "state/gaps.md", "state/patterns.md", "state/backlog.md")


def _commit_state(reason: str) -> None:
    """Commit loop bookkeeping so the tree is clean before any merge.

    None of those writes were ever committed, so `git merge --ff-only
    <candidate>` refused -- "local changes would be overwritten" -- and the
    beat died with an approved candidate stranded. That killed a 14-beat run
    at beat 5 and then killed the keeper's run at beat 1.

    Timing is the whole fix: this must run BEFORE a cycle is spawned, never
    after. A commit made after the cycle branched moves HEAD out from under
    the candidate, and an ff-merge of a commit that is no longer a descendant
    fails just as hard -- trading a dirty tree for a broken ancestry.
    """
    # Only paths that exist: `git add` fails hard on a missing pathspec, and
    # a young repo has no backlog.md. Filtering also means the soil can never
    # commit a register's DELETION -- change is not delete (P6).
    present = [f for f in STATE_FILES if (REPO / f).exists()]
    if not present or not git("status", "--porcelain", "--", *present).strip():
        return
    git("add", "--", *present)
    git("-c", "user.name=meristem-substrate",
        "-c", "user.email=substrate@localhost",
        "commit", "-q", "-m", f"soil: {reason}")


def _has_unactioned_proposals() -> bool:
    """True if proposals.md has any lines that look like proposals."""
    if not PROPOSALS.exists():
        return False
    for line in PROPOSALS.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("- [ ] "):
            return True
    return False


#: Substrate's own copy of the mandatory cap-case elements, duplicated for
#: the same reason as the path list below: the seed can legally mutate
#: loop.py, so the soil must be able to tell an argued case from a bare
#: demand without asking the kernel.
CAP_CASE_REQUIRED_SUBSTRATE = (
    "per-file", "core pressure", "closure pressure",
    "already externalized", "proposed", "expected",
)


def _is_guarded_proposal(text: str) -> bool:
    """Substrate's own copy of the guarded-path check.

    MUST NOT import from meristem.loop — the seed can legally mutate loop.py,
    so importing eligibility from the kernel would let the seed relax the
    fence in a way reviewers could miss.
    """
    lowered = text.lower()
    if any(m in lowered for m in CAP_MARKERS) or CAP_WORD.search(lowered):
        # The cap seat reached rung 2 on 2026-08-17: a COMPLETE case is
        # ordinary work and may be promoted, because the panel grants it.
        # An incomplete one stays held. The kernel refuses those before they
        # ever reach proposals.md; this is the soil's independent second
        # opinion, which is the whole point of not importing the first.
        if any(e not in lowered for e in CAP_CASE_REQUIRED_SUBSTRATE):
            return True
        # The budgets live in guarded ground, so a case must name it to be
        # actionable. Drop that one mention; anything guarded still standing
        # holds the proposal.
        # The mandate requires a per-file breakdown and four kernel files
        # sit under meristem/gates/, so an honest case always cited guarded
        # ground. Strip citations (path + line count) only.
        rest = CAP_CITE_SUBSTRATE.sub("", lowered).replace(CAP_HOME_SUBSTRATE, "")
        return bool(PROPOSAL_GUARDED_SUBSTRATE.search(rest))
    return bool(PROPOSAL_GUARDED_SUBSTRATE.search(lowered))


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
        if row.get("kind") == "seat_change":
            # A seat change settles the account. Without this the failures
            # that CAUSED a demotion are still in promotes[-3:] after the
            # re-arm, so one fresh failure re-demotes instantly -- a 3-strike
            # rule collapsed to 1 strike, oscillating the seat and firing a
            # notification pair each way. Every seat change starts a fresh
            # budget; a failure may only be billed to one demotion.
            promotes = []
        elif row.get("kind") == "auto_promote":
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
    Guarded ground stays held for a human. A cap case does not: since
    2026-08-17 the cap seat also sits at rung 2, so a COMPLETE budget case is
    promoted like any other work and the panel grants it. The owner is
    notified by name, not asked. An incomplete case never gets this far.
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
    is_cap = any(m in promoted_text.lower() for m in CAP_MARKERS)
    _journal({"kind": "auto_promote", "task": promoted_text[:200],
              "cap_case": is_cap,
              "why": "approval seat rung 2: non-guarded proposal auto-promoted"})
    if is_cap:
        # A budget move is the one promotion the owner asked to hear about by
        # name. It is still only a notice: the panel grants it, nobody is
        # asked, and the loop does not wait for a reply.
        notify("cap_case", "FYI, no action needed. The seed submitted a "
                           "COMPLETE budget case and it is queued for the "
                           f"review panel (2/2 + canary):\n  {promoted_text[:400]}")
    else:
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


#: Keep in sync with meristem.germline.LIFECYCLE by hand. Duplicated on
#: purpose, like CAP_PROPOSAL_MARKERS above: this file MUST NOT import from
#: the kernel, which the seed may legally mutate.
LIFECYCLE_STAGES = ("candidate", "calibrate", "register", "active",
                    "deprecating", "archive")


def guard_lifecycle(base: str, candidate: str) -> list[str]:
    """An organ advances one lifecycle stage per promotion, and never skips.

    germline.advance() enforces exactly this and has never been called, by
    anything, including tests. Every lifecycle change in this system's history
    happened the other way: a mutation rewriting organ.json directly, on a
    path where nothing checked step order. Cycle 383 moved feasibility-check
    from candidate to calibrate that way, correctly. Nothing would have
    stopped it going to active in the same commit.

    So the rule lived in the kernel and was never applied to the only route
    that used it -- declared, unasserted, which is the failure this whole
    architecture exists to refuse.

    Review saw the shape of this at cycle 363 and attached it to the wrong
    object: it refused a proposal asking for READ access to organ.json on the
    grounds that organs could then self-promote past the substrate. Refusing
    that proposal did nothing, because WRITE access is what creates the risk
    and _validate_paths has never withheld it. The concern was right and this
    is where it belongs -- in the soil, where promotion authority lives.

    Both trees are read through git, never from disk: a beat's bookkeeping
    may already have moved the working copy.
    """
    problems = []
    for rel in git("diff", "--name-only", f"{base}..{candidate}").splitlines():
        if not rel.startswith("body/organs/") or not rel.endswith("/organ.json"):
            continue
        try:
            new = json.loads(git("show", f"{candidate}:{rel}")).get("lifecycle")
        except (RuntimeError, ValueError):
            continue  # deleted or unparseable; the manifest gates own that
        before = git("show", f"{base}:{rel}", check=False)
        if not before:
            if new != "candidate":
                problems.append(
                    f"{rel}: a new organ enters at 'candidate', not '{new}'")
            continue
        try:
            old = json.loads(before).get("lifecycle")
        except ValueError:
            continue
        if old == new:
            continue
        if old not in LIFECYCLE_STAGES or new not in LIFECYCLE_STAGES:
            problems.append(f"{rel}: unknown lifecycle stage '{old}' -> '{new}'")
        elif LIFECYCLE_STAGES.index(new) != LIFECYCLE_STAGES.index(old) + 1:
            problems.append(
                f"{rel}: '{old}' -> '{new}' skips the lifecycle;"
                " one stage per promotion, forward only")
    return problems


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

    subject = git("log", "-1", "--format=%s", candidate)
    m = re.match(r"cycle\s+\d+:\s*", subject)
    why = subject[m.end():] if m else ""

    # Staleness first, because every gate below it compares against HEAD and
    # answers nonsense once HEAD has moved. Cycle 384 is what that looks like:
    # a mutation that touched two files under body/organs/failure-aggregator,
    # approved 2/2 by the panel, refused with "touches protected paths:
    # ['substrate/supervisor.py']" -- a file it never opened. main had gained
    # three commits while the cycle was in flight, so HEAD..candidate rendered
    # them as reversals and guard_protected read the reversal as an edit.
    #
    # The candidate was doomed either way: the merge below is --ff-only and a
    # candidate that no longer descends from HEAD cannot fast-forward. What
    # was wrong was the reason. It goes to the mailbox, to the webhook, and
    # through failure_history() into the next mutation prompt, where it would
    # have taught the seed to avoid a path it had not touched.
    #
    # Unattended, HEAD does not move mid-cycle: _commit_state runs at the top
    # of the beat and the cycle is a blocking subprocess. It moves when a
    # human commits to main during a beat, which is how this was found.
    #
    # Refused, not rebased. Replaying the diff onto the new HEAD would promote
    # a tree the panel never saw in the form it would land in.
    if git("merge-base", "HEAD", candidate) != git("rev-parse", "HEAD"):
        reason = ("REFUSED: candidate branched from an earlier HEAD and can no "
                  "longer fast-forward -- main moved while the cycle was in "
                  "flight. Nothing is wrong with the change itself.")
        print(reason, file=sys.stderr)
        # canary_reject for the same reason as every other refusal here: any
        # other kind marks the task done forever (P-026). The task reopens and
        # the seed does the work again against the tree that exists now.
        _journal({"kind": "canary_reject", "commit": candidate[:12],
                  "why": why, "reason": reason})
        notify("stale_candidate",
               "Candidate refused: main moved while the cycle was in flight, so "
               "it can no longer fast-forward. The change itself passed review. "
               "Task reopened for retry.")
        git("update-ref", "-d", CANDIDATE_REF, check=False)
        return 7

    offenders = guard_protected("HEAD", candidate)
    if offenders:
        # Journal it and CLEAR the ref. Neither used to happen: the refusal
        # printed to stderr and left CANDIDATE_REF standing, so every later
        # beat re-resolved the same candidate, refused it the same way, and
        # left no trace -- a permanent silent wedge with the loop stuck
        # "ahead" on a commit it will never accept.
        #
        # Recorded as canary_reject ON PURPOSE, not as a new kind. The cycle
        # record for this task says outcome=candidate, and done_tasks() is
        # (candidates - canary_rejects) | promoted -- so clearing the ref
        # under any OTHER kind would make the task read as done forever and
        # the work would vanish (P-026's exact failure). Reusing this kind
        # reopens the task, hands the seed the real reason through
        # failure_history(), and lets the breaker park it after three tries.
        reason = f"REFUSED: touches protected paths: {offenders}"
        print(reason, file=sys.stderr)
        _journal({"kind": "canary_reject", "commit": candidate[:12],
                  "why": why, "reason": reason})
        notify("protected_refusal",
               f"Candidate refused, touches protected ground: {offenders}. "
               f"Task reopened for retry. No action needed.")
        git("update-ref", "-d", CANDIDATE_REF, check=False)
        return 4

    jumps = guard_lifecycle("HEAD", candidate)
    if jumps:
        # Same refusal idiom as the protected-path branch above, and the same
        # kind on purpose: canary_reject reopens the task, hands the seed the
        # real reason through failure_history(), and lets the breaker park it
        # after three tries. Refused BEFORE the canary boot, which is the
        # expensive step.
        reason = f"REFUSED: organ lifecycle skips a stage: {jumps}"
        print(reason, file=sys.stderr)
        _journal({"kind": "canary_reject", "commit": candidate[:12],
                  "why": why, "reason": reason[:400]})
        notify("lifecycle_refusal",
               f"Candidate refused, an organ lifecycle skipped a stage: {jumps}. "
               f"Task reopened for retry. No action needed.")
        git("update-ref", "-d", CANDIDATE_REF, check=False)
        return 6

    ok, output = canary(candidate)
    if not ok:
        print(f"REFUSED: canary boot failed\n{output}", file=sys.stderr)
        # Identity first, summary second. unittest prints the failing test's
        # name near the TOP of its output and the tally at the bottom, so a
        # plain output[-400:] kept "Ran 146 tests / FAILED (failures=1)" and
        # dropped the only fact the seed could act on. failure_history() then
        # hands it just reason[:200], so the names must lead (cycle 225 was
        # refused this way and had no way to learn which test it broke).
        named = re.findall(r"(?:FAIL|ERROR): [^\n]+", output)
        tally = re.findall(r"FAILED \([^)]*\)", output)
        reason = ("; ".join(named) + (" || " + tally[-1] if tally else "")
                  if named else output[-400:])
        _journal({"kind": "canary_reject", "commit": candidate[:12],
                  "why": why, "reason": reason[:400]})
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
    # "Never fatal" has to include the exception, not just the exit code. Only
    # the returncode branch was guarded, so a slow network raised
    # TimeoutExpired straight through publish -> promote -> heartbeat: the
    # beat died with exit 1, the keeper stopped the whole run, and the
    # promotion this was supposed to merely ANNOUNCE had already succeeded.
    # One push that took over 120s cost a fourteen-beat night.
    try:
        result = subprocess.run(["git", "push", "origin", "main"], cwd=str(REPO),
                                capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError) as exc:
        _journal({"kind": "publish_failed", "reason": f"{type(exc).__name__}: {exc}"[:300]})
        print(f"publish FAILED (promotion stands): {type(exc).__name__}",
              file=sys.stderr)
        return
    if result.returncode == 0:
        print(f"published main -> origin ({resolve('HEAD')[:12]})")
    else:
        _journal({"kind": "publish_failed",
                  "reason": result.stderr.strip()[:300]})
        print(f"publish FAILED (promotion stands): {result.stderr.strip()[:200]}",
              file=sys.stderr)


#: Probe staging -> vault. control/probe-protocol.md: "the seed writes
#: proposals into the repository; the gates promote validated proposals into
#: the vault. The seed never touches the vault directly." The seed held up its
#: half -- eight proposals staged by 2026-08-20, four of them in one day -- and
#: this half did not exist. No probe the seed ever wrote had scored anything,
#: so Loop B ("the measuring stick precedes the capability") was an open
#: circuit: it kept making sticks that never measured.
VAULT = pathlib.Path(os.environ.get("MERISTEM_VAULT", REPO.parent / "meristem-vault"))
PROBE_STAGING = REPO / "state" / "probe-proposals"


def _score_probe(probe_dir, probe_id: str, workdir):
    """Run one rubric the way the kernel does -- without importing the kernel.

    Substrate mirrors the contract instead of calling meristem.gates.probes,
    for the reason _is_guarded_proposal mirrors the fence: the seed may legally
    mutate the kernel, and a promotion path that asked the kernel whether a
    probe is sound would let the seed answer for itself. Returns None when the
    rubric cannot produce a score at all.

    workdir is passed, never read from the module: see promote_probes.
    """
    check = probe_dir / "rubric" / "check.py"
    try:
        result = subprocess.run(
            [sys.executable, str(check)],
            input=json.dumps({"workdir": str(workdir), "probe": probe_id}),
            capture_output=True, text=True, timeout=120, cwd=str(probe_dir))
        return float(json.loads(result.stdout or "{}").get("score", 0.0))
    except (subprocess.SubprocessError, ValueError, OSError,
            json.JSONDecodeError, TypeError):
        return None


def promote_probes(vault=None, staging=None, workdir=None) -> int:
    """Validated staged proposals become frozen vault probes.

    Every path is a PARAMETER, defaulting to the live one. The first version
    read the module globals, so the only way to exercise it was to monkeypatch
    them -- and on 2026-08-20 a dry run whose patch did not hold copied six
    seed-authored probes into the real vault with no journal record and no
    fingerprint. The rule was already written down, in gates/closure.py:
    "EVERY function takes the tree it is inspecting. A gate that reads a path
    constant instead of the candidate it was handed is inspecting the wrong
    tree." A promotion path that can only be tested by reaching into the
    module is a promotion path that can only be tested dangerously.

    A probe that scores 0 is NOT refused. The probe gate fails on REGRESSION
    (probes.run: score < previous), and a probe with no history cannot fail
    anything -- its first run only records a baseline. Several of these will
    score 0 on arrival because they measure organs that are built but never
    called; that zero is the point. It is the first time "not wired" becomes
    a number instead of a paragraph.
    """
    vault = pathlib.Path(vault) if vault is not None else VAULT
    staging = pathlib.Path(staging) if staging is not None else PROBE_STAGING
    workdir = pathlib.Path(workdir) if workdir is not None else REPO
    if not staging.is_dir():
        return 0
    promoted = 0
    for src in sorted(p for p in staging.iterdir() if p.is_dir()):
        pid = src.name
        dest = vault / "internal" / "active" / pid
        if dest.exists():
            continue
        refuse, manifest, staged = None, {}, None
        try:
            manifest = json.loads((src / "probe.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            refuse = f"unreadable probe.json: {exc}"
        if refuse is None and manifest.get("id") != pid:
            refuse = f"probe.json id {manifest.get('id')!r} != directory {pid!r}"
        if refuse is None and not (src / "statement" / "task.md").is_file():
            refuse = "no statement/task.md"
        # Two forms, because the vault holds two. probe-word-count-basic and
        # probe-text-stats-basic have carried no check.py since birth, and
        # run_probe answers that case explicitly: "no executable rubric; probe
        # is declarative only", score 0. P-062 required check.py because all
        # eight staged proposals happened to have one -- a validation contract
        # induced from the sample rather than from the container, which then
        # refused the first proposal that obeyed P-030 ("no scoring logic in
        # the repository") by keeping its rubric out. Same error as P-050, one
        # layer over: there a test generalised from the current value, here a
        # gate generalised from the current sample.
        check = src / "rubric" / "check.py"
        declarative = not check.is_file()
        if refuse is None and not declarative:
            try:
                compile(check.read_text(encoding="utf-8"), str(check), "exec")
            except (OSError, SyntaxError) as exc:
                refuse = f"rubric does not compile: {exc}"
        if refuse is None and not declarative:
            staged = _score_probe(src, pid, workdir)
            if staged is None:
                refuse = "rubric returned no score"
        if refuse is None and declarative:
            # Nothing to run, so nothing to compare: a declarative probe
            # records a 0 baseline and gates nothing until a rubric is
            # authored vault-side. Harmless by construction -- the probe gate
            # fails on regression, and 0 cannot regress.
            staged = 0.0
        if refuse is None and not declarative:
            # Score again from inside the vault. A rubric that reaches for
            # relative paths scores differently once its cwd moves, and a probe
            # whose value depends on where it lives is not frozen -- it would
            # read as a capability regression the first time anything moved.
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
            manifest["frozen"] = True
            (dest / "probe.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            vaulted = _score_probe(dest, pid, workdir)
            if vaulted is None or vaulted != staged:
                shutil.rmtree(dest, ignore_errors=True)
                refuse = f"location-dependent: staged {staged} vs vault {vaulted}"
        if refuse is None and declarative:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
            manifest["frozen"] = True
            (dest / "probe.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if refuse is not None:
            # Refusals are journalled too. A rubric that cannot be promoted
            # would otherwise be re-validated every beat with nobody told why.
            _journal({"kind": "probe_refused", "probe_id": pid, "reason": refuse[:300]})
            print(f"probe refused: {pid}: {refuse}", file=sys.stderr)
            continue
        # The hash is the freeze made literal. The seed cannot reach the vault,
        # but this is the first path by which seed-authored code enters it, so
        # the contents get a fingerprint on the way in -- any later divergence
        # is then detectable, including from a bug of mine.
        _journal({"kind": "probe_promoted", "probe_id": pid, "score": staged,
                  "declarative": declarative,
                  "sha256": None if declarative
                  else hashlib.sha256(check.read_bytes()).hexdigest()})
        shutil.rmtree(src, ignore_errors=True)
        print(f"probe promoted: {pid} (baseline {staged})")
        promoted += 1
    return promoted


def rollback(reason: str) -> int:
    """Revert, never reset. The failure is recorded, not erased."""
    last_good = resolve(LAST_GOOD)
    head = git("rev-parse", "HEAD")
    if not last_good or last_good == head:
        # Nothing to revert TO: HEAD is already the canary-proven commit, so
        # the failures are environmental rather than a bad mutation and no
        # revert can help. Returning 1 made the keeper read "I could not roll
        # back" as "the heartbeat is broken" and stop for a human -- two
        # different facts sharing one exit code. Three of the three keeper
        # stops in this system's life came through that door.
        #
        # The failure now goes where failures belong -- the journal and the
        # webhook -- and the exit code reports process health only. Bounded by
        # the SAME 24h window the keeper applies to real rollbacks: a second
        # occurrence inside it is systemic and does stop, because "recoverable
        # every time but never recovering" is worse than stopping, and quieter.
        # The keeper cannot see this case itself (it detects rollbacks by
        # grepping commit subjects, and a no-op writes no commit), so the
        # bound is enforced here against its own stamp file.
        stamps, now = REPO.parent / "keeper_rollbacks", int(time.time())
        prior = sum(1 for s in stamps.read_text(encoding="utf-8").split()
                    if s.isdigit() and now - int(s) < 86400) if stamps.exists() else 0
        _journal({"kind": "rollback_noop", "head": head[:12],
                  "reason": reason, "prior_24h": prior})
        notify("rollback_noop",
               f"{reason}, but HEAD is already last-good so there is nothing to "
               + ("revert. Second time in 24h -- stopping for review."
                  if prior else "revert. Resuming; no action needed."))
        with stamps.open("a", encoding="utf-8") as handle:
            handle.write(f"{now}\n")
        print(f"nothing to roll back to (prior in 24h: {prior})")
        return 1 if prior else 0
    git("revert", "--no-edit", "--no-commit", f"{last_good}..HEAD", check=False)
    if not git("diff", "--cached", "--name-only"):
        # An earlier rollback already landed the tree at last-good, so the
        # revert staged nothing and `commit` exits non-zero on an empty tree.
        # That RuntimeError escaped the beat firewall and stopped the keeper
        # while the FIRST rollback had in fact succeeded (P-055). Narrowed to
        # the empty case on purpose: a lock file or a full disk must still
        # raise, so this is not a catch-all around the commit.
        print(f"already at {last_good[:12]}; nothing left to revert")
        notify("rollback", f"Already at last-good {last_good[:12]}: {reason}")
        return 0
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
        # Everything from here to the end of the beat runs inside a firewall.
        # The seed's failures were always handled gracefully -- counted,
        # escalated to rollback after three. The SOIL's own exceptions were
        # fatal: one raise anywhere in core_pressure / _auto_promote /
        # _commit_state / promote killed all fourteen beats. That asymmetry is
        # backwards, the soil being the layer that is supposed to hold still,
        # and it cost three separate nights (P-041, P-043) before it was named.
        #
        # This adds no new failure machinery. It ROUTES a soil exception into
        # the machinery that already exists -- the same `failures` counter,
        # the same HEALTH_FAIL_LIMIT, the same rollback, the same keeper stop.
        # A transient push timeout should cost one beat, not a night.
        try:
            pressure = core_pressure()
            # Pressure pre-empts work ONCE, then stands aside. Reflection only
            # proposes; the relief itself arrives as ordinary cycles executing
            # the agenda. Pre-empting on every beat while pressure stays high
            # is a livelock -- the more urgent it gets the less gets done --
            # which is exactly what twelve wasted beats demonstrated (P-021).
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
                print(f"    core pressure {pressure:.2f}, agenda empty, no"
                      " proposals -- re-issuing the mandate", flush=True)
                argv = ["reflect", "--pressure"]
            elif _auto_promote():
                argv = ["cycle"]
            else:
                argv = ["reflect"]
            # Clean the tree BEFORE the work starts, so a cycle branches from a
            # commit that already carries the previous beat's bookkeeping and
            # the ff-merge at promotion has nothing to collide with.
            _commit_state(f"beat {beat} bookkeeping")
            result = subprocess.run([sys.executable, "-m", "meristem.loop", *argv],
                                    cwd=str(REPO),
                                    **({} if os.name == "nt" else {"start_new_session": True}))
            if result.returncode != 0:
                failures += 1
                if failures >= HEALTH_FAIL_LIMIT:
                    return rollback(f"{failures} consecutive heartbeat failures")
            # Promote whenever a candidate EXISTS, not only when the beat's
            # exit code was clean. Cycle 120 passed both reviewers, was left
            # unpromoted because its beat returned non-zero, and cycle 121 then
            # branched from the unchanged HEAD and overwrote it -- a
            # unanimously approved change discarded in silence (P-024). A
            # candidate is the gates' verdict; it is not the exit status of the
            # process that produced it.
            if resolve(CANDIDATE_REF):
                promote()
            # After promotion, not before: a candidate that just landed may
            # have staged the very proposal being promoted here. Staging is
            # deleted on success, and that deletion rides the NEXT beat's
            # _commit_state, which is where every other state change goes.
            promote_probes()
            # Clear the budget only once the WHOLE beat came through. Resetting
            # right after the subprocess looked equivalent and was not: promote()
            # raises AFTER that point, so every beat wiped the exception budget
            # before the exception could be counted, the streak never reached
            # three, and rollback could never fire on soil failures. The test
            # for the three-strike path is what caught it.
            if result.returncode == 0:
                failures = 0
            # Publish whenever main has moved, not only when a promotion
            # moved it. publish() was reachable from promote() alone, so a
            # soil commit or a beat's bookkeeping sat unpushed for as long
            # as the seed went without landing a change -- seven commits
            # and most of a day, the last time. The server is where commits
            # and pushes happen and GitHub is the mirror; a mirror that
            # only updates on promotion is not a mirror. Env-gated,
            # idempotent when there is nothing to send, never fatal.
            publish()
        except Exception as exc:
            # Exception, never BaseException: SystemExit and KeyboardInterrupt
            # must still pass through. No `continue` either -- the sleep below
            # is outside this block, and skipping it would spin the beats with
            # no pause at all when the raise happens before the subprocess.
            failures += 1
            _journal({"kind": "beat_exception", "beat": beat,
                      "reason": f"{type(exc).__name__}: {exc}"[:200],
                      "traceback": traceback.format_exc()[:400]})
            print(f"    beat {beat} raised {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            notify("beat_exception",
                   f"Beat {beat} failed with {type(exc).__name__} "
                   f"({failures}/{HEALTH_FAIL_LIMIT} before rollback). "
                   f"The run continues. No action needed.")
            if failures >= HEALTH_FAIL_LIMIT:
                return rollback(f"{failures} consecutive beat failures")
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
    # 0.0 is the DANGEROUS direction to fail in: unknown pressure reads as
    # "no pressure", which suppresses the mandate at exactly the moment the
    # kernel is against its cap. Leaving the return value alone for now --
    # changing a heartbeat decision input is not something to ship into an
    # unattended night -- but a silent 0.0 must at least leave a trace.
    if result.returncode != 0:
        _journal({"kind": "probe_fault", "why": "core_pressure",
                  "reason": f"status exited {result.returncode}: "
                            f"{result.stderr.strip()[:200]}"})
        print(f"core_pressure: status exited {result.returncode}", file=sys.stderr)
        return 0.0
    for line in result.stdout.splitlines():
        if "core pressure" in line:
            try:
                return float(line.split(":")[1].split()[0])
            except (IndexError, ValueError):
                _journal({"kind": "probe_fault", "why": "core_pressure",
                          "reason": f"unparseable: {line[:120]}"})
                return 0.0
    _journal({"kind": "probe_fault", "why": "core_pressure",
              "reason": "no 'core pressure' line in status output"})
    return 0.0


def pending_task() -> bool:
    """Is there work already queued? Read-only, and failure means 'reflect'."""
    result = subprocess.run([sys.executable, "-m", "meristem.loop", "status"],
                            cwd=str(REPO), capture_output=True, text=True)
    if result.returncode != 0:
        _journal({"kind": "probe_fault", "why": "pending_task",
                  "reason": f"status exited {result.returncode}: "
                            f"{result.stderr.strip()[:200]}"})
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
