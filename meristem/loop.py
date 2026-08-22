#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field

from . import (
    CONTROL,
    DECISIONS,
    JOURNAL,
    REPO,
    SCOREBOARD,
    MeristemError,
    append_jsonl,
    read_jsonl,
    read_text,
    utc_now,
)
from . import breaker as breaker_mod
from . import engine as engine_mod
from . import germline
from . import journal
from . import ledger as ledger_mod
from . import llm as llm_mod
from .gates import closure as closure_mod
from .gates import deterministic, probes, review

CANDIDATE_REF = "meristem/candidate"


def _notify_park(task: str, cycles: str) -> None:
    import os, urllib.request
    url = os.environ.get("MERISTEM_WEBHOOK_URL", "")
    if not url:
        return
    body = json.dumps({"msgtype": "text", "text": {
        "content": f"[meristem:park] Parked (loop continues, no action needed): "
                   f"{task[:80]} ({cycles}). To retry only this task, delete its "
                   f"line from state/mailbox.md."
    }}).encode()
    try:
        req = urllib.request.Request(url, data=body,
                                    headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print(f"notify_park failed: {exc}", file=sys.stderr)


def git(*args, cwd=None, check=True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd or REPO), capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise MeristemError(f"git {' '.join(args)}: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


@dataclass
class CycleResult:
    cycle: int
    task: str
    outcome: str = "rejected"
    reason: str = ""
    branch: str = ""
    changed: list = field(default_factory=list)
    usd: float = 0.0
    votes: list = field(default_factory=list)
    probe_runs: list = field(default_factory=list)
    rationale: str = ""
    tier: str = "A"


def take_task() -> str | None:
    """Select the next open task from the agenda. Logic in journal.take_task."""
    return journal.take_task(JOURNAL, REPO, CONTROL)


def estimate_closure(task: str) -> int:
    """Pre-proposal closure estimate (FA-016). Over-inclusion is acceptable."""
    base = closure_mod.compute([]).tokens
    extra = sum(closure_mod.organ_closure(o.id).tokens
                for o in germline.registry() if o.id in task.lower())
    return base + extra


def make_worktree(cycle: int):
    """A fresh branch + worktree: the mutation's transaction boundary."""
    branch = f"cycle-{cycle}-{uuid.uuid4().hex[:8]}"
    path = REPO.parent / f"meristem-wt-{branch}"
    git("worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    return branch, path


def drop_worktree(path, branch: str, keep: bool) -> None:
    git("worktree", "remove", "--force", str(path), check=False)
    if not keep:
        git("branch", "-D", branch, check=False)


def golden_fixtures() -> list[str]:
    """Immune self-test: canned bad diffs that MUST be rejected."""
    from .gates import germline_validate

    failures = []

    # 1. Incomplete manifest must not validate.
    if not germline_validate.validate({"id": "x"}, "x"):
        failures.append("germline validation accepted an incomplete manifest")

    # 2. Active organ with no probes must not validate.
    bad = {f: "x" for f in germline_validate.REQUIRED}
    bad.update({"id": "x", "entrypoint": ["y"], "lifecycle": "active", "probes": [],
                "dependencies": [], "input_schema": {}, "output_schema": {},
                "capability": "c"})
    if not germline_validate.validate(bad, "x"):
        failures.append("germline validation admitted an active organ with no probes")

    # 3. Protected paths must be refused.
    if deterministic.run(["root/panic.py"]).passed:
        failures.append("deterministic gate allowed a change to the root of trust")
    if deterministic.run(["substrate/supervisor.py"]).passed:
        failures.append("deterministic gate allowed a change to the substrate")

    # 4. Secrets must be caught.
    probe = REPO / "state" / ".fixture_secret.py"
    probe.write_text('KEY = "sk-' + "a" * 32 + '"\n', encoding="utf-8")
    try:
        if not deterministic.scan_secrets([probe]):
            failures.append("secret scanner missed an obvious key")
    finally:
        probe.unlink(missing_ok=True)

    # 5. Understated closure must be refused.
    if deterministic.run([], declared_closure=1).passed:
        failures.append("deterministic gate accepted an understated closure")

    # 6. Erasing append-only memory must be refused.
    register = REPO / "state" / "patterns.md"
    original = read_text(register)
    if original.strip():
        try:
            register.write_text("# Pattern Register\n\n## Z-999 \u2014 only entry\n",
                                encoding="utf-8")
            if not deterministic.memory_integrity(["state/patterns.md"]):
                failures.append("memory-integrity check missed an erased register")
        finally:
            register.write_text(original, encoding="utf-8")

    # 7. Proposal guard: guarded ground must be held, not queued.
    if route_proposal("Fix the bug in substrate/supervisor.py") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    if route_proposal("Fix the bug in root/panic.py") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    # 7b. Case-variant guarded paths must also be caught.
    if route_proposal("Fix the bug in Substrate/supervisor.py") != "mailbox":
        failures.append("proposal guard let a case-variant guarded path reach the queue")
    if route_proposal("Fix the bug in ROOT/panic.py") != "mailbox":
        failures.append("proposal guard let a case-variant guarded path reach the queue")
    if route_proposal("Rewrite meristem/gates/review.py") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    if route_proposal("Update control/constitution.md") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    if route_proposal("Update control/checklists.md") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    if route_proposal("Add a utility command to meristem/loop.py") != "agenda":
        failures.append("proposal guard held an ordinary proposal")
    if route_proposal("Grow an organ at body/organs/summarise/") != "agenda":
        failures.append("proposal guard held an ordinary proposal")
    # 7a. Unargued cap changes must be refused; argued ones must pass.
    silent = "raise KERNEL_LOC_CAP to 6000"
    if not cap_case_missing(silent):
        failures.append("cap-case check accepted an unargued budget change")
    if route_proposal(silent) != "refused":
        failures.append("an unargued cap change was not refused")
    argued = ("Per-file LOC: loop.py 757. Core pressure 0.88, closure "
              "pressure 0.65. Already externalized the view commands; "
              "insufficient. Proposed new cap 3400. Expected closure "
              "impact: none.")
    if cap_case_missing(argued):
        failures.append("cap-case check called a complete case incomplete")
    if route_proposal(argued) != "agenda":
        failures.append("a complete cap case did not reach the review panel")
    # 7c. Relief proposals must survive the cap guard. Real text, from one of
    #     five consecutive externalization proposals the bare \bcaps?\b ate:
    #     each was read as an incomplete cap case and DROPPED, so the mandate
    #     asked for relief, the guard destroyed the answer, and the queue
    #     stayed empty. The guard must match an intent to MOVE the budget.
    relief = "Externalize aggregation into a new organ to stay under the cap."
    if route_proposal(relief) != "agenda":
        failures.append("the cap guard ate an externalization proposal")
    # 7d. The per-file format the mandate DEMANDS cites gates files, which
    #     forced every complete case into the mailbox. Real text, from the
    #     seed's first complete case. A citation is not a request to edit.
    cited = ("Change the cap. per-file: meristem/loop.py 904, "
             "meristem/gates/review.py 196, meristem/gates/probes.py 198. "
             "Core pressure 0.99, closure pressure 0.77. Already externalized "
             "the aggregator; insufficient. Proposed new cap 3200. Expected "
             "closure impact: none.")
    if route_proposal(cited) != "agenda":
        failures.append("the per-file requirement forced a cap case to a human")
    if route_proposal(cited + " Also rewrite meristem/gates/review.py.") != "mailbox":
        failures.append("a cap case bought passage past the gates fence")

    return failures


def run_cycle(task: str, cycle: int, *, config=None) -> CycleResult:
    result = CycleResult(cycle=cycle, task=task)
    models = config or llm_mod.load_models()

    fixture_failures = golden_fixtures()
    if fixture_failures:
        result.reason = "IMMUNE FAILURE: " + "; ".join(fixture_failures)
        append_jsonl(JOURNAL, {"kind": "cycle", "cycle": cycle, "outcome": "immune_failure",
                               "reason": result.reason})
        raise MeristemError(result.reason)

    branch, workdir = make_worktree(cycle)
    result.branch = branch
    keep = False
    try:
        history = journal.failure_history(JOURNAL, task)
        mutation = engine_mod.propose(task, config=models, extra=history)
        result.rationale = mutation.rationale
        result.usd += ledger_mod.drain_attempts(cycle, models)
        ledger_mod.check(cycle)

        result.changed = engine_mod.apply(mutation, workdir)
        git("add", "-A", cwd=workdir)
        if not git("diff", "--cached", "--name-only", cwd=workdir):
            result.reason = "engine produced no effective change"
            return result
        git("-c", "user.name=meristem", "-c", "user.email=meristem@localhost",
            "commit", "-q", "-m", f"cycle {cycle}: {task}\n\n{mutation.rationale}", cwd=workdir)

        # Gates inspect the candidate tree, against the state before the mutation.
        verdict = deterministic.run(result.changed, root=workdir, base="HEAD~1")
        if not verdict.passed:
            result.reason = "deterministic: " + "; ".join(verdict.failures)
            return result

        probe_verdict = probes.run(str(workdir), cycle=cycle)
        result.probe_runs = [vars(run) for run in probe_verdict.runs]
        if not probe_verdict.passed:
            result.reason = "probes: " + "; ".join(probe_verdict.failures)
            return result

        diff = git("diff", "HEAD~1", "HEAD", cwd=workdir)
        computed = closure_mod.compute(result.changed, root=workdir)
        review_result = review.run(
            diff, task, computed.files,
            changed_files=result.changed, config=models,
        )
        result.votes = review_result.votes
        result.usd += ledger_mod.drain_attempts(cycle, models)
        if not review_result.approved:
            result.reason = f"review rejected ({review_result.quorum})"
            return result

        git("update-ref", f"refs/{CANDIDATE_REF}", git("rev-parse", "HEAD", cwd=workdir))
        keep = True
        result.outcome = "candidate"
        result.reason = f"approved {review_result.quorum}; awaiting substrate promotion"

        append_jsonl(DECISIONS, {"cycle": cycle, "task": task,
                                 "rationale": mutation.rationale, "notes": mutation.notes,
                                 "changed": result.changed})
        for run in probe_verdict.runs:
            append_jsonl(SCOREBOARD, {"kind": "probe", "cycle": cycle,
                                      "probe_id": run.probe_id, "score": run.score,
                                      "domain": run.domain, "probe_kind": run.kind})
        return result
    except Exception as exc:
        if not result.reason:
            result.reason = str(exc)[:300]
        if result.outcome != "candidate":
            append_jsonl(JOURNAL, {"kind": "fault", "cycle": cycle, "task": task,
                                   "error": f"{type(exc).__name__}: {exc}"[:400]})
        return result
    finally:
        # Bill every attempt, including failures.
        try:
            ledger_mod.drain_attempts(cycle, models)
        except Exception:
            pass
        # Six questions by construction: rationale travels here so reviewers
        # can answer all six from the journal alone.
        append_jsonl(JOURNAL, {
            "kind": "cycle",
            "cycle": cycle,
            "outcome": result.outcome,
            "why": task,
            "rationale": result.rationale,
            "what": result.changed,
            "proved_better_by": [r["probe_id"] for r in result.probe_runs
                                 if r.get("score", 0) > 0],
            "no_regression_on": [r["probe_id"] for r in result.probe_runs],
            "usd": round(result.usd, 6),
            "approved_by": [v.get("slot") for v in result.votes
                            if v.get("verdict") == "approve"],
            "rejected_by": [
                {"slot": v.get("slot"), "weakens_gate": v.get("weakens_gate"),
                 "reasons": [str(r)[:500] for r in (v.get("reasons") or [])[:5]]}
                for v in result.votes if v.get("verdict") != "approve"
            ],
            "tier": result.tier,
            "slot_votes": {v.get("slot"): v.get("verdict") for v in result.votes},
            "reason": result.reason,
            "branch": branch if keep else "",
        })
        drop_worktree(workdir, branch, keep)


def print_utility() -> int:
    """Per-organ utility report. Logic in journal.print_utility."""
    return journal.print_utility(JOURNAL)


def print_body() -> int:
    """List every organ: id, version, lifecycle, capability."""
    organs = germline.registry()
    if not organs:
        print("no organs registered")
        return 0
    print(f"{'id':20s} {'ver':5s} {'lifecycle':12s} capability")
    print(f"{'--':20s} {'---':5s} {'--------':12s} ----------")
    for organ in organs:
        print(f"{organ.id:20s} {organ.version:5s} {organ.lifecycle:12s} {organ.capability}")
    return 0


def print_spend() -> int:
    """Spend report. Logic in journal.print_spend."""
    return journal.print_spend(JOURNAL)


#: Guarded ground: proposals naming these go to the mailbox, not the queue.
PROPOSAL_GUARDED = re.compile(r"(?:root|substrate)/[\w./-]*\w+\.\w+|meristem/gates/"
                              r"[\w./-]*\w+\.py|control/(?:constitution|checklists)\.md")

#: Cap changes need a complete case and approval; never ride in as ordinary
#: mutations. The seat demotes on evidence, but the argued-case requirement
#: never demotes (monotonicity).
CAP_HOME = "meristem/gates/deterministic.py"
#: A citation: kernel path + its size, bare or parenthesised. Never \s -- a newline before the number is prose, not a citation, and stripping it let a rewrite intent through (P-052).
CAP_CITE = re.compile(r"meristem/[\w/..-]+\.py(?:[ \t:]*\d+|[ \t]*\(\d+[^)]{0,12}\))")

CAP_PROPOSAL_MARKERS = (
    "KERNEL_LOC_CAP", "kernel_loc_cap", "loc cap", "LOC cap",
    "raise the cap", "increase the cap", "lower the cap", "\u5185\u6838\u4e0a\u9650", "\u6269\u5bb9",
    "\u4e0a\u9650",
)

#: A cap case is incomplete without every one of these.
CAP_CASE_REQUIRED = (
    "per-file", "core pressure", "closure pressure",
    "already externalized", "proposed", "expected",
)


#: An INTENT to move a budget, not the mere appearance of the word. A bare
#: \bcaps?\b caught every externalization proposal that said "bring pressure
#: under the cap" -- five in a row were read as incomplete cap cases and
#: DISCARDED, which is how the pressure mandate livelocked: reflect proposes
#: relief, the guard eats it, the queue stays empty, reflect runs again.
#:
#: Tuned for PRECISION, not recall, because P-040 made the two errors wildly
#: asymmetric. A missed cap intent is cheap: it still faces the review panel
#: and review.py's hard limits (pressure >= 0.90, step <= 10%, constant only).
#: A false positive is unrecoverable: the proposal is refused and dropped, and
#: nothing survives to appeal. So the guard is deliberately narrow.
#:
#: Excluded on purpose: "new" and "move" as verbs -- "into a NEW organ to get
#: under the cap" and "MOVE capability into an organ" are the mandate's own
#: vocabulary for relief. "new cap" survives as its own idiom. The second
#: alternative takes only "to": "under the cap of 3000" and "sits at the cap
#: at 3000" describe the budget, they do not ask to move it.
CAP_INTENT = re.compile(
    r"\b(raise|raising|increase|increasing|lower|lowering"
    r"|adjust|adjusting|change|changing|set|setting)\b[^.]{0,20}?\bcaps?\b"
    r"|\bnew\s+caps?\b"
    r"|\bcaps?\b\s*to\s*\d"
)


def mentions_cap_change(text: str) -> bool:
    """Case-insensitive matching so paraphrases don't bypass the fence."""
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in CAP_PROPOSAL_MARKERS):
        return True
    return CAP_INTENT.search(lowered) is not None


def cap_case_missing(text: str) -> list[str]:
    """Missing mandatory elements of a cap case. Empty = complete enough to judge."""
    lowered = text.lower()
    return [item for item in CAP_CASE_REQUIRED if item.lower() not in lowered]


def _is_duplicate_proposal(new_text: str, existing_lines: list) -> bool:
    """Jaccard word overlap > 0.5 means near-duplicate."""
    new_words = set(new_text.lower().split())
    if len(new_words) < 3:
        return False
    for line in existing_lines:
        stripped = line.strip()
        if not stripped.startswith("- [ ] "):
            continue
        old_words = set(stripped[6:].lower().split())
        if not old_words:
            continue
        overlap = len(new_words & old_words) / max(len(new_words), len(old_words))
        if overlap > 0.5:
            return True
    return False


def route_proposal(text: str) -> str:
    """'agenda' to queue, 'mailbox' for human review, 'refused' to drop.
    Cap changes need a complete case; guarded ground goes to mailbox."""
    lowered = text.lower()
    if mentions_cap_change(text):
        if cap_case_missing(text):
            return "refused"
        # Strip per-file CITATIONS (path + line count) before the fence check:
        # the mandate demands that breakdown and four kernel files sit under
        # meristem/gates/, so an honest case always named guarded ground.
        rest = CAP_CITE.sub("", lowered).replace(CAP_HOME, "")
        return "mailbox" if PROPOSAL_GUARDED.search(rest) else "agenda"
    if PROPOSAL_GUARDED.search(CAP_CITE.sub("", lowered)):
        return "mailbox"
    return "agenda"


def print_probe_proposals() -> int:
    """List probe proposals under state/probe-proposals/."""
    proposals_dir = REPO / "state" / "probe-proposals"
    if not proposals_dir.is_dir():
        print("no probe proposals")
        return 0
    entries = sorted(d for d in proposals_dir.iterdir() if d.is_dir())
    if not entries:
        print("no probe proposals")
        return 0
    print(f"{'id':40s} {'statement':10s} {'rubric':10s} {'complete':10s}")
    print(f"{'--':40s} {'---------':10s} {'------':10s} {'--------':10s}")
    for entry in entries:
        has_statement = (entry / "statement").is_dir()
        has_rubric = (entry / "rubric").is_dir()
        complete = "yes" if (has_statement and has_rubric) else "no"
        print(f"{entry.name:40s} "
              f"{'yes' if has_statement else 'no':10s} "
              f"{'yes' if has_rubric else 'no':10s} "
              f"{complete:10s}")
    return 0


PRESSURE_MANDATE_ASK = """The kernel budget is running out. Propose ONE concrete relief.

Options in priority order:
  1. externalize -- move capability into an organ (lowers both pressures).
  2. prune -- delete something that stopped earning its lines.
  3. compress or internalize with equal deletion (budget-neutral only).
  4. change the cap -- LAST. The check is LITERAL: your text must contain
     each of these phrases, unbroken --
       "per-file" (give the per-file LOC breakdown under it)
       "core pressure"
       "closure pressure"
       "already externalized"
       "proposed" (the new value)
       "expected" (state the expected closure impact under it)
     Five cases were refused for missing the same phrases while arguing the
     substance perfectly well. The checker reads words; write those words.

Reply with ONLY: {"proposals": ["one concrete relief"]}"""


def scan_unexercised() -> int:
    """Scan for unexercised capabilities. No model call -- pure analysis."""
    rows = read_jsonl(JOURNAL)
    findings: list[str] = []
    # Organs registered but never invoked
    called = {r.get("callee") for r in rows if r.get("kind") == "organ_call"}
    for organ in germline.registry():
        if organ.id not in called:
            findings.append(f"organ '{organ.id}' ({organ.lifecycle}) never invoked")
    # Probe proposals never promoted
    pp_dir = REPO / "state" / "probe-proposals"
    if pp_dir.is_dir():
        promoted = {r.get("probe_id") for r in rows
                    if r.get("kind") == "probe_promoted"}
        for entry in sorted(pp_dir.iterdir()):
            if entry.is_dir() and entry.name not in promoted:
                findings.append(f"probe proposal '{entry.name}' never promoted")
    # Proposals completed but still listed as open
    done = journal.done_tasks(JOURNAL)
    proposals = read_text(REPO / "state" / "proposals.md")
    for line in proposals.splitlines():
        s = line.strip()
        if s.startswith("- [ ] "):
            task = s[6:].strip()[:80]
            if task in done:
                findings.append(f"completed proposal still listed: {task}")
    if not findings:
        print("no unexercised capabilities detected")
    else:
        for f in findings:
            print(f"  {f}")
    return 0


def run_reflect(*, config=None, pressure: bool = False) -> int:
    """Reflect: one model call proposing up to three next tasks.
    Appends to state/proposals.md, never to control/agenda.md."""
    models = config or llm_mod.load_models()
    # Reflect records its own cycle so the per-cycle call cap resets.
    cycle = journal.next_cycle(JOURNAL)
    append_jsonl(JOURNAL, {"kind": "cycle", "cycle": cycle,
                           "outcome": "reflection",
                           "why": "reflect" + (" --pressure" if pressure else ""),
                           "what": [], "reason": "reflection, not a mutation"})

    # 1. Stale node ids from memory-Graph organ (may not exist yet).
    stale_ids: list = []
    try:
        result = germline.invoke(
            "memory-graph",
            {"op": "stale", "args": {"threshold": 0.5}},
            cycle=cycle,
        )
        if result.get("ok"):
            stale_ids = result.get("result", {}).get("stale", [])
    except Exception:
        pass

    # 2. Read registers.
    gaps_text = read_text(REPO / "state" / "gaps.md")
    patterns_text = read_text(REPO / "state" / "patterns.md")

    # 3. Build digest and make ONE model call. The prompt demands both
    #    repair and growth proposals to avoid converging on a fixed point.
    digest = (
        "# Reflection digest\n\n"
        "## Stale knowledge (low-activation node ids)\n"
        f"{', '.join(stale_ids) if stale_ids else '(none)'}\n\n"
        "## Gaps\n"
        f"{gaps_text[-8000:]}\n\n"
        "## Patterns\n"
        f"{patterns_text[-8000:]}\n\n"
        "Propose up to three concrete, actionable next tasks for Meristem. "
        "Each should be specific enough to be taken from the agenda and "
        "executed as a single mutation.\n\n"
        "You MUST include at least one proposal of EACH kind:\n"
        "1. REPAIR: something measurably wrong -- a failing probe, a "
        "recurring rejection, a gap. Name the specific evidence (which "
        "probe, which cycle, which gap id).\n"
        "2. GROWTH: a capability you do not have but that the evidence "
        "suggests is worth having. What would become possible, and what "
        "measuring stick would prove it works?\n\n"
        "The constitution says 'Spiral, not circular': a loop that only "
        "ever repairs converges on a fixed point and stops. A loop that "
        "only grows without repairing accumulates debt. Both kinds "
        "are required every pass.\n\n"
        "Reply with ONLY a JSON object:\n"
        '{"proposals": ["task 1", "task 2", "task 3"]}'
    )
    # Feed existing proposals to prevent regeneration.
    open_proposals = []
    _pp = REPO / "state" / "proposals.md"
    if _pp.exists():
        for _line in _pp.read_text(encoding="utf-8").splitlines():
            _s = _line.strip()
            if _s.startswith("- [ ] "):
                open_proposals.append(_s[6:].strip()[:80])
    _ap = REPO / "control" / "agenda.md"
    if _ap.exists():
        for _line in _ap.read_text(encoding="utf-8").splitlines():
            _s = _line.strip()
            if _s.startswith("- [ ] "):
                open_proposals.append(_s[6:].strip()[:80])
    if pressure:
        # Under mandate, replace the generic ask with a focused relief request.
        breakdown = "\n".join(
            f"  {q.relative_to(REPO).as_posix()}: "
            f"{len(q.read_text(encoding='utf-8').splitlines())} lines"
            for q in sorted((REPO / "meristem").rglob("*.py"))
        )
        _ck = sorted([(closure_mod._estimate_tokens(closure_mod.organ_closure(o.id).paths), o.id) for o in germline.registry()] + [(closure_mod._estimate_tokens([q]), q.name) for q in sorted((REPO / "state").glob("*.md"))], key=lambda x: -x[0])[:3]
        _cb = closure_mod.compute([]).tokens
        digest = (
            f"## Kernel LOC by file\n{breakdown}\n\n"
            f"## Pressures\ncore {deterministic.kernel_loc()}/{deterministic.KERNEL_LOC_CAP}; closure {_cb}/{deterministic.CLOSURE_TOKEN_CAP} (headroom {deterministic.CLOSURE_TOKEN_CAP - _cb}; one mutation adds the whole organ dir plus every file it touches; largest chunks: "
            + ", ".join(f"{n} {t}" for t, n in _ck) + ")\n\n"
            f"## Gaps\n{gaps_text[-8000:]}\n\n{PRESSURE_MANDATE_ASK}"
        )
    # MEMORY IS APPENDED TO BOTH BASES (P-021): the mandate replaces the
    # question, not what was already learned. Without already-proposed and
    # refused-cap-case context, the mandate re-proposed existing work and
    # never converged on a complete cap case.
    if open_proposals:
        digest += (
            "\n\n## Already proposed (DO NOT repeat these)\n"
            + "\n".join(f"- {p}" for p in open_proposals)
        )
    _ref = [r for r in read_jsonl(JOURNAL)
            if r.get("kind") == "cap_case_refused"][-3:]
    if _ref:
        digest += ("\n\n## Cap cases refused as incomplete (fix, do not repeat)\n"
                   + "\n".join(f"- {r.get('why','')[:100]} -- {r.get('reason','')}"
                               for r in _ref))
    report_text = read_text(REPO / "REPORT.md")
    if report_text.strip():
        digest += "\n\n## Self-report (last heartbeat)\n" + report_text[:2000]
    messages = [
        {
            "role": "system",
            "content": (
                "You are the reflection step of Meristem, a self-modifying "
                "kernel. You are given a digest of stale knowledge, known "
                "gaps, and recurring patterns. You must propose up to three "
                "concrete next tasks.\n\n"
                "You MUST include at least one REPAIR proposal (something "
                "measurably wrong -- a failing probe, a recurring rejection, "
                "a gap) and at least one GROWTH proposal (a capability you "
                "do not have but that the evidence suggests is worth having). "
                "The constitution's phrase is 'Spiral, not circular': a loop "
                "that only ever repairs converges on a fixed point and stops. "
                "Both kinds are required every time.\n\n"
                "Reply with ONLY a JSON object: "
                '{"proposals": ["...", "...", "..."]}'
            ),
        },
        {"role": "user", "content": digest},
    ]

    completion = llm_mod.complete("score", messages, config=models)
    try:
        ledger_mod.drain_attempts(cycle, models)
    except Exception:
        pass
    ledger_mod.check(cycle)

    # 4. Parse proposals.
    proposals: list = []
    try:
        data = engine_mod._parse(completion.text)
        raw = data.get("proposals", [])
        if isinstance(raw, list):
            proposals = raw
    except Exception:
        pass

    # 5. Append to state/proposals.md, never to control/agenda.md.
    proposals_path = REPO / "state" / "proposals.md"
    mailbox_path = REPO / "state" / "mailbox.md"
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = proposals_path.read_text(encoding="utf-8").splitlines() if proposals_path.exists() else []
    mailbox_lines = mailbox_path.read_text(encoding="utf-8").splitlines() if mailbox_path.exists() else []
    agenda_path = REPO / "control" / "agenda.md"
    agenda_lines = agenda_path.read_text(encoding="utf-8").splitlines() if agenda_path.exists() else []
    all_dedup_lines = existing_lines + [
        f"- [ ] {line.split(': ', 1)[1]}" for line in mailbox_lines
        if line.strip().startswith("- PROPOSAL") and ": " in line
    ] + [line for line in agenda_lines if line.strip().startswith(("- [ ] ", "- [x] "))]
    queued = held = skipped = refused = 0
    for proposal in proposals[:3]:
        text = str(proposal).strip()
        if not text:
            continue
        route = route_proposal(text)
        if _is_duplicate_proposal(text, all_dedup_lines):
            skipped += 1
        elif route == "refused":
            append_jsonl(JOURNAL, {
                "kind": "cap_case_refused", "cycle": cycle,
                "why": text[:200],
                "reason": "incomplete cap case, missing "
                          + ", ".join(cap_case_missing(text))})
            refused += 1
        elif route == "mailbox":
            hit = (m.group(0) if (m := PROPOSAL_GUARDED.search(CAP_CITE.sub("", text.lower()))) else "?")
            with mailbox_path.open("a", encoding="utf-8") as handle:
                handle.write(f"- PROPOSAL (held, names guarded ground {hit!r}): {text}\n")
            all_dedup_lines.append(f"- [ ] {text}")
            held += 1
        else:
            with proposals_path.open("a", encoding="utf-8") as handle:
                handle.write(f"- [ ] {text}\n")
            all_dedup_lines.append(f"- [ ] {text}")
            queued += 1

    print(f"appended {queued} proposal(s) to state/proposals.md; "
          f"{held} held for human review in state/mailbox.md"
          + (f"; {skipped} duplicate(s) skipped" if skipped else "")
          + (f"; {refused} unargued cap case(s) refused" if refused else ""))
    return 0


def generate_report() -> int:
    """Write REPORT.md via body/tools/reporter.py."""
    rows = read_jsonl(JOURNAL)
    cycles = [r for r in rows if r.get("kind") == "cycle"]
    last_rc = max((r.get("cycle", 0) for r in rows if r.get("kind") == "report"), default=0)
    recent = [c for c in cycles if c.get("cycle", 0) > last_rc]
    core_p, closure_p = deterministic.kernel_loc() / deterministic.KERNEL_LOC_CAP, closure_mod.compute([]).tokens / deterministic.CLOSURE_TOKEN_CAP
    last_rep = next((r for r in reversed(rows) if r.get("kind") == "report"), None)
    proposals = read_text(REPO / "state" / "proposals.md")
    pt = {r.get("task", "")[:200] for r in rows if r.get("kind") == "auto_promote"}
    n_acc = sum(1 for c in cycles if c.get("outcome") == "candidate")
    agr = f"{sum(1 for c in cycles if c.get('outcome') == 'candidate' and c.get('why', '')[:200] in pt)}/{n_acc}" if n_acc else "0/0"
    data = {"workdir": str(REPO), "generated_at": utc_now(), "core_pressure": core_p,
            "closure_pressure": closure_p, "core_cap": deterministic.KERNEL_LOC_CAP,
            "closure_cap": deterministic.CLOSURE_TOKEN_CAP, "kernel_loc": deterministic.kernel_loc(),
            "outcomes": {o: sum(1 for c in recent if c.get("outcome", "unknown") == o) for o in {c.get("outcome", "unknown") for c in recent}},
            "recent_count": len(recent), "last_report_cycle": last_rc, "agr": agr,
            "open_proposals": sum(1 for l in proposals.splitlines() if l.strip().startswith("- [ ] ")),
            "parked": list(journal.parked_tasks(JOURNAL, REPO)),
            "mailbox_items": [l.strip() for l in read_text(REPO / "state" / "mailbox.md").splitlines() if l.strip()],
            "prev_core_pressure": last_rep.get("core_pressure") if last_rep else None,
            "prev_closure_pressure": last_rep.get("closure_pressure") if last_rep else None,
            "probe_scores": {r.get("probe_id", "?"): float(r.get("score", 0.0)) for r in read_jsonl(SCOREBOARD) if r.get("kind") == "probe"},
            "organs": [{"id": o.id, "lifecycle": o.lifecycle} for o in germline.registry()]}
    reporter = type(REPO)(__file__).resolve().parent.parent / "body" / "tools" / "reporter.py"
    result = subprocess.run(["python3", str(reporter)], input=json.dumps(data), capture_output=True, text=True)
    if result.returncode != 0: raise MeristemError(f"reporter.py failed: {result.stderr[:400]}")
    append_jsonl(JOURNAL, {"kind": "report", "cycle": journal.next_cycle(JOURNAL),
                           "core_pressure": round(core_p, 4), "closure_pressure": round(closure_p, 4)})
    print(str(REPO / "REPORT.md"))
    return 0


def _try_aggregate_failures(cycle: int) -> None:
    """Call failure-aggregator to detect patterns and write to state/patterns.md.
    Purely additive: breaker_mod.should_park still runs every cycle. All
    signals are printed (G-006). Every outcome journals kind='aggregation' (G-008)."""
    organ = REPO / "body" / "organs" / "failure-aggregator" / "main.py"
    if not organ.exists():
        return
    payload = json.dumps({"op": "aggregate", "journal_path": str(JOURNAL),
                          "repo_path": str(REPO), "cycle": cycle})
    outcome, exit_code, data, signals_list, error = "exception", None, None, [], ""
    try:
        r = subprocess.run(["python3", str(organ)], input=payload,
                           capture_output=True, text=True, timeout=30)
        exit_code = r.returncode
        if r.returncode != 0:
            outcome = "failure"
            error = r.stderr[:400]
        else:
            try:
                data = json.loads(r.stdout or "{}")
                signals_list = (data.get("signals") or []) if isinstance(data, dict) else []
                outcome = "success"
            except json.JSONDecodeError as exc:
                outcome = "bad_output"
                error = f"JSONDecodeError: {exc}"[:300]
                data = r.stdout[:400]
    except subprocess.TimeoutExpired:
        outcome = "timeout"
    except Exception as exc:
        outcome = "exception"
        error = f"{type(exc).__name__}: {exc}"[:300]

    patterns = [s.get("class", "?") for s in signals_list]
    append_jsonl(JOURNAL, {"kind": "aggregation", "cycle": cycle,
                           "outcome": outcome, "exit_code": exit_code,
                           "return_value": data, "patterns": patterns,
                           "error": error})

    if outcome == "success":
        for s in signals_list:
            action = s.get("action", "surface")
            print(f"  pattern [{action}]: {s.get('class', '?')} on "
                  f"'{s.get('task', '')[:60]}' "
                  f"({s.get('count', 0)} rejections)")
    elif outcome == "timeout":
        print("failure-aggregator: timeout after 30s", file=sys.stderr)
    else:
        print(f"failure-aggregator: {outcome}: {error[:200]}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="meristem", description="Meristem evolution loop")
    parser.add_argument("command", choices=["cycle", "status", "selftest", "gaps", "body",
                                            "spend", "probe-proposals", "agenda", "reflect",
                                            "utility", "report"])
    parser.add_argument("--task", help="override the task instead of taking one from the agenda")
    parser.add_argument("--pressure", action="store_true",
                        help="reflect under a pressure mandate: propose ONE concrete "
                             "relief for a kernel budget that is running out")
    parser.add_argument("--scan", action="store_true",
                        help="reflect sub-mode: scan for unexercised capabilities "
                             "(no model call)")
    args = parser.parse_args(argv)

    if args.command == "selftest":
        failures = golden_fixtures()
        for failure in failures:
            print(f"FAIL {failure}")
        print("immune self-test:", "FAILED" if failures else "ok")
        return 1 if failures else 0

    if args.command == "agenda":
        return journal.print_agenda(JOURNAL, REPO, CONTROL)

    if args.command == "gaps":
        for line in read_text(REPO / "state" / "gaps.md").splitlines():
            if line.startswith("## "):
                print(f"  {line[3:]}")
        return 0

    if args.command == "body":
        return print_body()

    if args.command == "spend":
        return print_spend()

    if args.command == "utility":
        return print_utility()

    if args.command == "probe-proposals":
        return print_probe_proposals()

    if args.command == "reflect":
        if args.scan:
            return scan_unexercised()
        return run_reflect(pressure=args.pressure)

    if args.command == "report":
        return generate_report()

    if args.command == "status":
        rows = read_jsonl(JOURNAL)
        cycles = [r for r in rows if r.get("kind") == "cycle"]
        accepted = [c for c in cycles if c.get("outcome") == "candidate"]
        tiers = {}
        for c in accepted:
            tiers[c.get("tier", "A")] = tiers.get(c.get("tier", "A"), 0) + 1
        slot_stats: dict[str, dict[str, int]] = {}
        for c in cycles:
            for slot, verdict in (c.get("slot_votes") or {}).items():
                tally = slot_stats.setdefault(slot, {"approve": 0, "reject": 0})
                tally["approve" if verdict == "approve" else "reject"] += 1
        organs = germline.registry()
        ahead = git("rev-list", "--count", "origin/main..HEAD", check=False) or "?"

        print(f"cycles run      : {len(cycles)}")
        print(f"  accepted      : {len(accepted)}")
        print(f"  rejected      : {sum(1 for c in cycles if c.get('outcome') == 'rejected')}")
        print(f"  faults        : {sum(1 for r in rows if r.get('kind') == 'fault')}")
        loc = deterministic.kernel_loc()
        core_pressure = loc / deterministic.KERNEL_LOC_CAP
        closure_now = closure_mod.compute([]).tokens
        closure_pressure = closure_now / deterministic.CLOSURE_TOKEN_CAP
        print(f"kernel LOC      : {loc} / {deterministic.KERNEL_LOC_CAP}")
        print(f"  core pressure : {core_pressure:.2f}"
              + ("  <- externalize capability into an organ" if core_pressure >= 0.9 else ""))
        print(f"  closure press : {closure_pressure:.2f} ({closure_now} tokens); headroom {deterministic.CLOSURE_TOKEN_CAP - closure_now} for everything a mutation touches")
        print(f"organs (body)   : {len(organs)}"
              + (f"  [{', '.join(f'{o.id}:{o.lifecycle}' for o in organs)}]" if organs else ""))
        print(f"MCR by tier     : {tiers or '(none accepted yet)'}")
        for slot, tally in sorted(slot_stats.items()):
            print(f"  reviewer {slot:20s} approve={tally['approve']} reject={tally['reject']}")
        print(f"spent (USD)     : {ledger_mod.spent():.4f}  calls: {ledger_mod.calls()}")
        print(f"unpublished     : {ahead} commit(s) ahead of origin/main")
        print(f"open agenda item: {take_task() or '(none)'}")
        return 0

    # Core-pressure monitor: suggest externalization when kernel exceeds the cap.
    _loc = deterministic.kernel_loc()
    if _loc > deterministic.KERNEL_LOC_CAP:
        _ag = CONTROL / "agenda.md"
        if "EXTERNALIZE: kernel at" not in read_text(_ag):
            with _ag.open("a", encoding="utf-8") as _h:
                _h.write(f"- [ ] EXTERNALIZE: kernel at {_loc}/{deterministic.KERNEL_LOC_CAP} lines -- externalize capability from meristem/ into an organ.\n")

    task = args.task or take_task()
    if not task:
        print("agenda is empty; nothing to do")
        return 0

    # Circuit breaker: park a task that has been rejected too many times
    # before any model call is made.
    if breaker_mod.should_park(task):
        reason_str = journal.park_task(task, JOURNAL, REPO)
        print(f"task parked: {task} ({reason_str})")
        _notify_park(task, reason_str)
        return 0

    # Pre-proposal closure estimate (FA-016): park before spending a model call.
    est = estimate_closure(task)
    if est > deterministic.CLOSURE_TOKEN_CAP:
        reason = f"closure estimate ~{est} > {deterministic.CLOSURE_TOKEN_CAP}"
        journal.park_task(task, JOURNAL, REPO)
        print(f"task parked: {task} ({reason})")
        _notify_park(task, reason)
        return 0

    cycle = journal.next_cycle(JOURNAL)
    try:
        result = run_cycle(task, cycle)
    except Exception as exc:
        append_jsonl(JOURNAL, {"kind": "fault", "cycle": cycle, "task": task,
                               "error": f"{type(exc).__name__}: {exc}"[:400]})
        print(f"cycle {cycle} FAULT: {type(exc).__name__}: {exc}", file=sys.stderr)
        _try_aggregate_failures(cycle)
        return 2
    print(f"cycle {cycle}: {result.outcome} -- {result.reason}")
    if result.outcome == "candidate":
        print(f"candidate on branch {result.branch}; run substrate/supervisor.py promote")
    _try_aggregate_failures(cycle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
