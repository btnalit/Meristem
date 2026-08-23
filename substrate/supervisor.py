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
import tempfile
import time
import traceback
from types import SimpleNamespace

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
PROPOSAL_GUARDED_SUBSTRATE = re.compile(r"(?:root|substrate)/[\w./-]*\w+\.\w+")
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


def _append(path, record: dict) -> None:
    """Append one record to an append-only register, timestamped."""
    record = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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


def probe_scores(tree: str) -> dict:
    """The full frozen probe set against one tree, scored by HEAD's runner.

    probes.run() takes the tree it measures as an argument, so the measurement
    and its subject can be separated: the rubrics stay in the vault where P-030
    put them, the runner stays at HEAD in the production checkout, and the tree
    under examination is whatever is handed in. Launched as a subprocess with
    cwd=REPO so `from meristem.gates import probes` resolves to production even
    when the subject is a candidate that has rewritten probes.py -- a candidate
    must not be allowed to grade itself with its own ruler.

    A score of None means the probe is DECLARATIVE: it carries a statement and
    no rubric, so it has nothing to run and cannot be compared. P-066 admits
    that form to the vault with a 0 baseline on purpose. Reporting it as a
    zero would be the same overclaim P-078 removed one layer up -- "this probe
    is failing" when the truth is "nothing has been written to measure it yet".

    An empty dict means the measurement itself failed. The caller treats that
    as "no evidence", never as "no regression".
    """
    argv = [sys.executable, "-c",
            "import json,sys;sys.path.insert(0,'.');from meristem.gates import probes;"
            "print(json.dumps({r.probe_id: (None if 'no executable rubric' in"
            " (r.detail or '') else r.score) for r in"
            " probes.run(sys.argv[1], full=True).runs}))", tree]
    try:
        result = subprocess.run(argv, cwd=str(REPO), capture_output=True,
                                text=True, timeout=600)
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return {}


SCOREBOARD = REPO / "state" / "scoreboard.jsonl"


def record_scoreboard(commit: str) -> int:
    """Write the whole frozen set's score ON MAIN, so the scoreboard stops lying.

    probes.baseline() returns the LAST recorded score per probe, and a cycle
    records only the three it sampled -- so up to thirteen of sixteen entries
    are however old that probe's last draw happened to be.
    probe-journal-query-basic sat at 100.00 in the scoreboard while scoring
    0.00 on main, and BOTH the report's probe_scores and the in-cycle
    regression check read that stale number as the truth. A memory that is
    silently thirteen-sixteenths out of date is worse than no memory: the gate
    fires on phantom regressions and misses real ones.

    Written after the ff-merge, so these are the scores of MAIN, not of a
    candidate: complete, current, and attributable to the commit that produced
    them. The kernel keeps writing its per-cycle sample rows; this adds the
    full picture at the one moment the tree changes.

    The same separation as probe_scores(): rubrics from the vault, runner from
    HEAD, subject the tree just promoted.
    """
    scores = probe_scores(str(REPO))
    for pid, score in sorted(scores.items()):
        # A declarative probe records nothing. Writing 0.0 would make
        # baseline() hand the gate a number that was never measured, and the
        # first real rubric written for it would then read as an improvement
        # against a score nobody ever took.
        if score is None:
            continue
        _append(SCOREBOARD, {"kind": "probe", "probe_id": pid, "score": score,
                             "commit": commit[:12], "source": "promotion-full-set"})
    return sum(1 for s in scores.values() if s is not None)


PATTERNS = REPO / "state" / "patterns.md"
PATTERNS_ARCHIVE = REPO / "state" / "patterns-archive.md"
#: Tokens of pattern register a mutation may be asked to carry into its review
#: closure. 4000 leaves room beside a ~39k kernel+control baseline under the
#: 50000 cap; 13512 did not.
PATTERNS_KEEP_TOKENS = 4000


def rotate_patterns(keep_tokens: int = PATTERNS_KEEP_TOKENS) -> int:
    """Move the oldest pattern entries to an archive so the live file fits.

    Cycle 388 was refused by the deterministic gate with "closure ~52704 >
    50000 budget", and the two files it touched were state/patterns.md and
    state/decisions.jsonl. patterns.md had reached 13512 tokens against a
    headroom of about 10820 -- so ANY mutation touching it was refused
    automatically, whatever it said. The register that records failures had
    grown until writing to it was a budget violation.

    Moving is not losing. memory_integrity refuses a mutation that DROPS
    "## X-NNN" headings from a state/*.md it changed, and the docstring says
    what it is for: accidental erasure during a whole-file rewrite. Every
    heading rotated out is still greppable in the tree afterwards, in
    patterns-archive.md, in the same commit. The decision is recorded in
    decisions.jsonl because this is the first reading given to that invariant.

    G-006 entries are exempt, all of them. They are what park_task writes, and
    they are the carrier of the park-regeneration path: of thirty-two parked
    tasks, twenty-one came back in a new form, and the route is
    park -> G-006 in patterns.md -> the reflect digest -> a reworded task.
    Archiving one would cut a parked task's only way back. Six entries is a
    cheap exemption for a live loop.

    Returns the number of entries archived. Called from the beat, before
    _commit_state, so it rides that commit -- a rotation of its own would move
    HEAD mid-cycle and P-074 would refuse the candidate in flight as stale,
    which is exactly the accident that cost cycle 384.
    """
    if not PATTERNS.is_file():
        return 0
    chunks = re.split(r"(?m)^(?=## )", PATTERNS.read_text(encoding="utf-8"))
    head, entries = chunks[0], chunks[1:]
    keep, archive, budget = [], [], keep_tokens
    for chunk in reversed(entries):
        cost = len(chunk.splitlines()) * 12
        if chunk.lstrip().startswith("## G-006") or budget - cost >= 0:
            keep.append(chunk)
            budget -= cost
        else:
            archive.append(chunk)
    if not archive:
        return 0
    keep.reverse()
    archive.reverse()
    with PATTERNS_ARCHIVE.open("a", encoding="utf-8") as f:
        f.write("".join(archive))
    PATTERNS.write_text(head + "".join(keep), encoding="utf-8")
    _journal({"kind": "register_rotated", "register": "state/patterns.md",
              "archived": len(archive), "kept": len(keep),
              "archive": "state/patterns-archive.md"})
    return len(archive)


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
        # The frozen set, at last. probes.py has documented "the full frozen
        # set runs before promotion" since P0 and full=True had no caller
        # anywhere in the tree -- declared, unasserted. A cycle samples three
        # of sixteen, so thirteen frozen probes went unexamined every beat.
        #
        # Measured against HEAD, not against the scoreboard. A probe already
        # failing on main is not this candidate's regression, and refusing
        # every candidate for damage it did not do would park the agenda while
        # the one task that could fix it never ran. Right now that is not
        # hypothetical: probe-journal-query-basic scores 0 on main where the
        # scoreboard remembers 100, and no cycle has ever noticed, because the
        # rotating sample never drew it.
        before, after = probe_scores(str(REPO)), probe_scores(str(path))
        # Three different states, and calling them all "broken" would be the
        # P-078 overclaim again: a probe with no rubric has not failed, it has
        # never been asked anything.
        unmeasurable = sorted(p for p, s in before.items() if s is None)
        broken = sorted(p for p, s in before.items() if s is not None and s <= 0)
        if broken or unmeasurable:
            _journal({"kind": "probe_broken", "commit": commit[:12],
                      "probes": broken, "note": "already failing on HEAD",
                      "unmeasurable": unmeasurable,
                      "unmeasurable_note": "declarative: statement, no rubric"})
        fell = sorted(f"{p} {before[p]:.2f} -> {after[p]:.2f}"
                      for p in before if before[p] is not None
                      and after.get(p) is not None and after[p] < before[p])
        if fell:
            return False, "full frozen set regressed: " + "; ".join(fell)
        if not before:
            _journal({"kind": "probe_unmeasured", "commit": commit[:12]})
        return True, ("canary alive: immune self-test, kernel tests, and "
                      f"{len(after)} frozen probes with no regression")
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
    # The tree just changed; the scoreboard's memory of it is now provably
    # current instead of up to thirteen-sixteenths stale.
    recorded = record_scoreboard(candidate)
    if not recorded:
        _journal({"kind": "probe_unmeasured", "commit": candidate[:12],
                  "note": "scoreboard not refreshed after promotion"})
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
#: C-65：vault **只从 `MERISTEM_VAULT` 读，不提供相对路径缺省**。
#: 这里原本是 `os.environ.get("MERISTEM_VAULT", REPO.parent / "meristem-vault")`。
#: 2026-08-23 发现 `.claude/worktrees/meristem-vault/` 是一份完整的 anchor vault
#: 副本——有人在 worktree 里跑了 bootstrap，`REPO.parent` 落在 `.claude/worktrees/`
#: 而不是仓库外。**那个缺省失败时不报错，只是把 vault 定位到错的地方**，
#: 而 vault 存在的全部理由就是「物理上不可见胜过要求 prompt 不要看」。
#: 同一条纪律的 v5 侧实现见 `substrate/soil_state.resolve_vault()`。
VAULT = pathlib.Path(os.environ["MERISTEM_VAULT"]) if os.environ.get("MERISTEM_VAULT") else None
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
    if vault is None:
        # C-65：宁可拒绝运行，也不猜一个 vault 路径。猜错时它不报错，
        # 只是把冻结后的 probe 写到别处——而那正是 2026-08-23 那次泄漏的形状。
        raise RuntimeError("MERISTEM_VAULT 未设置，拒绝定位 vault（C-65）")
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
            # Before the bookkeeping commit, never after and never on its
            # own: a rotation that moved HEAD by itself would orphan a
            # candidate in flight exactly as a manual commit did at cycle 384.
            rotate_patterns()
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


# ===========================================================================
# v5 P0-a 土壤入口：manual-cycle / ignition-status（§12.0.2）
#
# 与上方 v3.1 的 promote/canary/heartbeat 并存，但**不共用任何判定逻辑**：
# v3.1 那套把测量与判决做在种子里（违反 S2/S3），v5 全部搬进 substrate/pipeline.py。
# **不要在两套之间借用函数** —— 借一个就等于把 v3.1 的语义偷渡进 v5 的判据，
# 而同一件事有两个判定处，正是 §17.5 点名的漂移温床。
# ===========================================================================
from substrate import pipeline as _pipeline  # noqa: E402
from substrate import probe_runner as _probe_runner  # noqa: E402
from substrate import soil_state as _soil_state  # noqa: E402

#: P0-a 的 Task 声明。**放在土壤侧而不是 `seed/`**：P0-a 是「人给任务，人做判决」
#: （§12.0），任务由实验者给出；而 §10.1 的种子可写白名单里本来就没有任何
#: Task 声明文件，种子此刻既写不了它、也不该写它——它是判自己的那张声明。
#: **未闭合**：P0-b「种子自己选题」需要一条种子可写的声明路径，§10.1 尚未给出。
DEFAULT_TASK_DECLARATION = "soil/p0a-task.json"


def _generation(repo=None) -> str:
    """世代权威在 `root/`（root of trust），不在土壤自己手里。

    读不到就**抛错，不退回 `gen-0`**：`generation` 是 §8.2 的六个强制字段之一，
    猜一个值会让台账带着一个看起来正常的错标签，而错标签比缺字段更坏——它像数据。
    """
    path = (pathlib.Path(repo) if repo is not None else REPO) / "root" / "generations.json"
    return json.loads(path.read_text(encoding="utf-8"))["live"]


def _next_soil_cycle(repo) -> int:
    """本次是第几个被测候选 = 台账里已有的 `observed_fitness` 数 + 1。

    **不另设计数文件**：多一份可变状态就多一处可与台账不一致的地方，
    而台账本身就是权威（§8.2）。这个数因此可在任意一份台账副本上离线重算。
    """
    ledger = _soil_state.Ledger(pathlib.Path(repo) / "state" / "soil-ledger.jsonl")
    return sum(1 for r in ledger.read() if r.get("kind") == "observed_fitness") + 1


def _task_id(text: str) -> str:
    """镜像 `meristem.task.task_id`（内容哈希即身份，§4.1）。

    **土壤不导入种子**（I9 / CA-4 断言 `substrate/` 不得 import `meristem`），
    所以这里复刻规则而不是调用它——与 `_is_guarded_proposal` 复刻围栏、
    `_score_probe` 复刻 rubric 契约同一个理由：**让种子回答「种子做得对不对」，
    等于让它给自己判分。** 代价是两处须保持一致，而这一致性**目前没有断言在守**。
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _agenda_first_task(repo):
    """议程首条可做的任务文本；没有议程返回 None。

    复刻 `meristem.task.take_task` 的取题规则（理由同 `_task_id`），但**只复刻到
    「首条非空非注释行」为止**：done/parked 过滤要读 `seed/feedback.json`，
    而那份投影 P0-a 尚未产出。两者在 P0-a 等价，之后不等价——记为未闭合项。
    """
    agenda = pathlib.Path(repo) / "seed" / "agenda.md"
    if not agenda.is_file():
        return None
    for raw in agenda.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line[:2] in ("- ", "* "):
            line = line[2:].strip()
        if line:
            return line
    return None


def _load_task(repo, task_path=None):
    """读 Task 声明，并与议程首条**核对身份**。

    不一致即拒绝：**一个任务两个身份**正是本规格反复点名的那种漂移
    （`campaign` 一词的代价是循环死锁），而 `task_id` 是台账里事件↔Task 的唯一锚。
    """
    path = pathlib.Path(task_path) if task_path else pathlib.Path(repo) / DEFAULT_TASK_DECLARATION
    if not path.is_file():
        raise SystemExit(f"Task 声明不存在：{path}（§8.1.4；P0-a 由实验者给出）")
    task = _pipeline.Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
    agenda_text = _agenda_first_task(repo)
    if agenda_text is not None and task.task_id != _task_id(agenda_text):
        raise SystemExit(
            f"task_id 与议程首条不一致：声明 {task.task_id}，"
            f"议程 {_task_id(agenda_text)}（{agenda_text[:60]!r}）。"
            "一个任务只能有一个身份，拒绝继续。")
    return task


def manual_prompt(commit: str, diff: str, task):
    """P0-a 的 **Panel adapter**（§12.0.2）。

    **y/n 落在 Verdict 位置，不落在 merge 位置** —— `process_candidate` 一行不改，
    `promotion_intent` / scoreboard / `accepted_fitness` / `promotion_committed`
    这条晋升事务链完整走完。P0-b 把这个函数换成真 panel，其余代码一行不动：
    **拆脚手架 = 换一个函数指针。**

    **不打印 fitness。** §12.0.2 的散文写着「`manual_prompt` 把 fitness 打给实验者」，
    而 §10.2 的签名与其理由写着「面板只收 diff + 任务声明，**不传 observed** ——
    评审员看见 +20 分会锚定向批准，判决就被测量污染了」。两处冲突，**按 §10.2 实现**：
    它是签名的定义处，且带着不变量的理由；§12.0.2 那句是描述性散文。
    已记入 §18 勘误（v5.10）。
    """
    print(f"\n=== 候选 {commit[:12]} ===")
    print(f"任务 {task.task_id} · target={task.target} · expected={task.expected} "
          f"· minimum_delta={task.minimum_delta}")
    print(f"--- diff（{len(diff.splitlines())} 行）---")
    print(diff)
    print("--- diff 结束 ---")
    print("按土壤版评审清单判决。**你看不到分数，这是刻意的**："
          "兑现声明的核验由 task_evaluator 在判决之外已经做完。")
    passed = input("accept? [y/N] ").strip().lower() in ("y", "yes")
    reason = "manual accept" if passed else (input("拒绝理由：").strip() or "manual reject")
    return _pipeline.Verdict(passed=passed, authority="manual", reason=reason)


def _seed_candidate(repo, ctx, task):
    """跑一次种子 `cycle`，产出候选 commit。返回 `(commit, worktree_path)`。

    种子在**独立 worktree** 里提交，主线 HEAD 不动 —— 于是
    `candidate.parent == HEAD` 成立，C3 的祖先检查才有意义（§10.1「提交到 worktree」）。
    若种子直接提交在主线上，HEAD 会跟着候选一起走，那条检查恒假。

    `kind:"cycle"` 记录**由土壤据退出码与 commit 生成**，种子不写台账（C4 / §10.1）。
    """
    base = _pipeline.git(repo, "rev-parse", "HEAD")
    worktree = pathlib.Path(tempfile.mkdtemp(prefix="meristem-candidate-")) / "wt"
    _pipeline.git(repo, "worktree", "add", "-q", "--detach", str(worktree), base)
    # **裁剪环境，不整份传递**（§15.6 C6）。种子的 `cycle` 是种子自己的代码，
    # 整份 `os.environ` 会把 `MERISTEM_VAULT`（C-65 保证一定在）连同任何模型 key、
    # webhook 一起交给它 —— vault 存在的全部理由就是种子看不见它。
    # 与 `pipeline.canary()` 复用同一份 allowlist，不各写各的。
    env = {**_probe_runner._sandboxed_env(), "PYTHONPATH": str(worktree),
           "MERISTEM_SOIL_CYCLE": str(ctx.soil_cycle)}
    try:
        result = subprocess.run([sys.executable, "-m", "meristem.loop", "cycle"],
                                cwd=str(worktree), env=env, capture_output=True,
                                text=True, timeout=1800)
    except (subprocess.SubprocessError, OSError) as exc:
        result = SimpleNamespace(returncode=-1, stdout="", stderr=str(exc))
    head = _pipeline.git(worktree, "rev-parse", "HEAD") if result.returncode == 0 else None
    # 退出码 0 但 HEAD 没动 = 种子没提交任何东西。**那不是一个候选**，
    # 台账里就不能把 base 记成本拍产出的 commit —— 错标签比没有标签更坏，
    # 它看起来像数据（同 loop.py 拒绝把未知拍号伪造成 0 的理由）。
    commit = head if head is not None and head != base else None
    ctx.ledger.append({"kind": "cycle", "commit": commit, "task_id": task.task_id,
                       "generation": ctx.generation, "soil_cycle": ctx.soil_cycle,
                       "exit_code": result.returncode})
    if commit is None:
        print(f"种子未产出候选（exit {result.returncode}）："
              f"{(result.stdout + result.stderr)[-400:]}", file=sys.stderr)
        return None, worktree
    return commit, worktree


def _drop_worktree(repo, worktree) -> None:
    """拆掉候选 worktree，**失败要出声**。

    原先这里静默吞掉 `git worktree remove` 的失败：注册项会一次次泄漏而没有任何
    痕迹。同时删掉 `mkdtemp` 建的那层父目录 —— `git worktree remove` 只认
    `wt/` 那一级，父目录每跑一次 `manual-cycle` 就留一个空壳，
    无人值守跑一夜会攒出一堆。
    """
    worktree = pathlib.Path(worktree)
    result = subprocess.run(["git", "worktree", "remove", "--force", str(worktree)],
                            cwd=str(repo), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"worktree remove 失败（注册项可能泄漏）：{result.stderr.strip()[:200]}",
              file=sys.stderr)
    shutil.rmtree(worktree.parent, ignore_errors=True)


def manual_cycle(*, calibration: bool = False, candidate=None, task_path=None) -> int:
    """§12.0.2：**走与未来 heartbeat 完全相同的代码路径。** 唯一的区别是判决位上坐着人。"""
    repo = REPO
    ctx = _soil_state.SoilContext.open(
        repo, generation=_generation(repo), soil_cycle=_next_soil_cycle(repo),
        calibration=calibration)

    # 启动必跑。**P0-a 不只是在测种子，也是在测土壤自己的事务链**：
    # 崩溃恢复要到 P0-c 无人值守时才第一次被真正需要，绕过它就等于没测（§12.0.2）。
    for commit, outcome in _pipeline.reconcile_on_start(repo, ctx):
        print(f"reconcile: {commit[:12]} -> {outcome.name}")

    if calibration and candidate is None:
        print("--calibration 必须配 --candidate <sha>：校准是**人工给定的确定能提升的"
              "变更**（§12.0.1），不经种子产出。", file=sys.stderr)
        return 2

    task = _load_task(repo, task_path)
    worktree = None
    if candidate is None:
        commit, worktree = _seed_candidate(repo, ctx, task)
        if commit is None:
            _drop_worktree(repo, worktree)
            return 1
    else:
        commit = _pipeline.git(repo, "rev-parse", candidate)

    try:
        outcome = _pipeline.process_candidate(commit, task, repo=repo,
                                              panel=manual_prompt, ctx=ctx)
    finally:
        if worktree is not None:
            _drop_worktree(repo, worktree)

    print(f"outcome: {outcome.name}")
    if calibration:
        print("校准：已测量、强制回滚、**永不 merge** —— 结构上产不出 accepted_fitness"
              "，因此永不计入点火（§12.0.1）。")
    return 0


def ignition_status(repo=None) -> int:
    """§1.2 判据的**唯一求值点**（§12.0.2）。只读台账，不查 task registry。

    **退出码只区分「读数产出了」与「读数产不出来」，不区分计数多少。**
    0 = 读数已产出（计数是 0 还是 5 都算产出）；1 = **台账损坏，读数不可得**。
    绝不让退出码携带判据语义 —— 判据的定义在 §1.2，不在某个人对退出码的理解里，
    否则下一个读者就会拿 `if ignition-status; then` 当判据用。

    **台账损坏走 fail closed，不是抛 traceback。** §1.2 要求谓词严格下标、
    缺键当场抛错（不许读者猜方向）——那条纪律留在谓词里；而**命令**必须把它
    翻译成一句可处置的话。一个读不出数的仪表要说「我读不出」，
    不是把栈打在操作员脸上：这正是判据最需要成立的时刻（崩溃恢复、事后审计）。
    """
    repo = pathlib.Path(repo) if repo is not None else REPO
    rows = _soil_state.Ledger(repo / "state" / "soil-ledger.jsonl").read()
    try:
        hits = [ev for ev in rows if _pipeline.is_ignition_event(ev)]
    except (KeyError, TypeError) as exc:
        print(f"台账损坏，出生判据无法求值：缺失或类型错误的字段 {exc}\n"
              f"  台账：{repo / 'state' / 'soil-ledger.jsonl'}\n"
              f"  §8.2 的强制字段与 records schema 由写入侧保证；"
              f"读到不合规的行意味着有东西绕过了 substrate/soil_state.Ledger。",
              file=sys.stderr)
        return 1

    print(f"ignition events: {len(hits)}   (criterion §1.2)")
    for ev in hits:
        rec = next(r for r in ev["records"]
                   if r["probe_id"] == ev["primary_probe"] and r["status"] == "improved")
        print(f"  soil_cycle {ev['soil_cycle']}  commit {str(ev['commit'])[:12]}  "
              f"task {ev['task_id']}  {ev['primary_probe']}  "
              f"{rec['before']} → {rec['after']}")

    counts: dict = {}
    try:
        for ev in rows:
            reason = _pipeline.ignition_exclusion_reason(ev)
            if reason is not None:
                counts[reason] = counts.get(reason, 0) + 1
    except (KeyError, TypeError) as exc:
        print(f"台账损坏，excluded 归因无法求值：{exc}", file=sys.stderr)
        return 1
    # 归因顺序定死（§12.0.2）：读数不稳定的仪表比没有仪表更坏。
    # **顺序从 `pipeline.IGNITION_CONJUNCTS` 派生，不在这里手抄一份** ——
    # 抄一份就是两个独立维护的副本，而这个项目到处在防的正是这种漂移。
    # `ignition_exclusion_reason` 对第一项返回的是带说明的 `kind≠accepted_fitness`，
    # 其余三项与合取项同名，故按前缀匹配对齐。
    parts = []
    for conjunct in _pipeline.IGNITION_CONJUNCTS:
        for key, count in counts.items():
            if key == conjunct or key.startswith(conjunct):
                parts.append(f"{count} {key}")
    print("excluded: " + (" · ".join(parts) if parts else "0"))
    return 0


#: v3.1 时代的运行入口。**默认拒绝执行**（§13.3 波次 2 之前）。
#:
#: 它们不是「还没接上新流水线」那么简单 —— **接上去是 P0-c 的工程**（keeper /
#: breaker / 预算窗口，§12），而 P0-a 的定义就是「人给任务，人做判决」。
#: 真正的危险在于它们**现在就能跑，而且跑起来会绕开整条 v5 链**：
#: `heartbeat` 以 `cwd=REPO` 起 `python -m meristem.loop cycle`，
#: 而 `meristem.loop` 如今是 **v5 的种子**，其 `run_cycle` 默认 workdir 即 REPO
#: —— 种子会**直接提交到主线工作树**，随后由 v3.1 的 `promote()` 判决：
#: 没有 before/after 测量、没有 `soil-ledger`、没有 `accepted_fitness`、
#: 没有点火记账。**一次这样的运行会污染主线，而台账上不会留下任何痕迹。**
#:
#: 于是它违反了本项目最基本的一条：**同一件事只能有一个权威判定入口。**
#: 保留代码（§13.3 要按项改造，不是删掉），但**不再默认可触发**。
#: 诊断 v3.1 时可用 `MERISTEM_ALLOW_LEGACY=1` 显式解锁 —— 解锁是人的动作，
#: 与 panic 闩同一形状：**默认安全，例外要显式说出口。**
LEGACY_COMMANDS = ("run", "promote", "rollback", "canary", "heartbeat")


def _refuse_legacy(command: str) -> int:
    print(
        f"拒绝执行 `{command}`：这是 v3.1 的运行入口，尚未按 §13.3 改造到 v5 流水线。\n"
        f"\n"
        f"  为什么不是「先跑着」：`heartbeat` 会以 cwd=REPO 起 `meristem.loop cycle`，\n"
        f"  而那如今是 v5 的种子 —— 它会直接提交到主线，再由 v3.1 的 promote() 判决，\n"
        f"  **绕开 before/after 测量、soil-ledger、accepted_fitness 与点火记账**。\n"
        f"\n"
        f"  P0-a 的运行入口是：python -m substrate.supervisor manual-cycle [--candidate <sha>]\n"
        f"  判据的求值点是：  python -m substrate.supervisor ignition-status\n"
        f"\n"
        f"  确实要跑 v3.1 的诊断路径，显式解锁：MERISTEM_ALLOW_LEGACY=1",
        file=sys.stderr)
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="supervisor")
    parser.add_argument("command",
                        choices=["run", "promote", "rollback", "canary", "heartbeat",
                                 "manual-cycle", "ignition-status"])
    parser.add_argument("--calibration", action="store_true",
                        help="装置对照组（§12.0.1）：人工给定的变更，强制回滚、永不 merge")
    parser.add_argument("--candidate", default=None,
                        help="处理一个已存在的候选 commit，而不是跑种子产出候选")
    parser.add_argument("--task", default=None,
                        help=f"Task 声明路径（默认 {DEFAULT_TASK_DECLARATION}）")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--beats", type=int, default=8)
    parser.add_argument("--dry", action="store_true",
                        help="beat without sleeping, for verification")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args(argv)

    if (args.command in LEGACY_COMMANDS
            and os.environ.get("MERISTEM_ALLOW_LEGACY", "").strip() != "1"):
        return _refuse_legacy(args.command)

    if args.command == "manual-cycle":
        return manual_cycle(calibration=args.calibration, candidate=args.candidate,
                            task_path=args.task)
    if args.command == "ignition-status":
        return ignition_status()
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
