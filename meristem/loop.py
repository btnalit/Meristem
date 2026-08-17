#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from . import (
    CONTROL,
    DECISIONS,
    JOURNAL,
    REPO,
    SCOREBOARD,
    MeristemError,
    append_jsonl,
    read_json,
    read_jsonl,
    read_text,
    utc_now,
    write_json,
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
    # FYI, not a request. The parked list rides the self-report into reflect,
    # so the loop proposes alternatives and keeps running; clearing the
    # mailbox line is only needed to retry THIS task.
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
    """Immune self-test: canned bad diffs that MUST be rejected.

    Nothing else proves the gates actually fire. If any fixture passes, the
    immune system is not working and the loop stops rather than continuing
    to trust it.
    """
    from .gates import germline_validate

    failures = []

    # 1. A manifest that skips required fields must not validate.
    if not germline_validate.validate({"id": "x"}, "x"):
        failures.append("germline validation accepted an incomplete manifest")

    # 2. An organ may not reach active with no probes.
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

    # 4. A secret must be caught.
    probe = REPO / "state" / ".fixture_secret.py"
    probe.write_text('KEY = "sk-' + "a" * 32 + '"\n', encoding="utf-8")
    try:
        if not deterministic.scan_secrets([probe]):
            failures.append("secret scanner missed an obvious key")
    finally:
        probe.unlink(missing_ok=True)

    # 5. Understating one's own closure must be refused.
    if deterministic.run([], declared_closure=1).passed:
        failures.append("deterministic gate accepted an understated closure")

    # 6. Erasing append-only memory must be refused. Tier A rewrites whole
    #    files, so "add an entry to this register" fails naturally as "replace
    #    the register with one entry" -- a real rejection, seen in cycle 4.
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

    # 7. Proposal guard: model output that names guarded ground must be held
    #    for human review, not queued. Reflect is a new way for model output
    #    to reach the work queue, so it needs the same fence the mutation path
    #    already has. If route_proposal ever stops catching guarded ground,
    #    the immune self-test fails and the loop stops -- the same proof that
    #    protected-path rejection and secret scanning actually fire.
    if route_proposal("Fix the bug in substrate/supervisor.py") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    if route_proposal("Fix the bug in root/panic.py") != "mailbox":
        failures.append("proposal guard let a guarded path reach the queue")
    # 7b. Case-variant guarded paths must also be caught. The original
    #     check was case-sensitive substring matching, so 'Substrate/' or
    #     'ROOT/' bypassed the fence entirely. Normalising to lowercase
    #     closes the class for all case paraphrases.
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
    # 7a. A cap change must arrive argued. This fixture outlives every
    #    demotion of the approval seat, because what it enforces is
    #    monotonicity -- a budget may not move on an unexamined say-so -- and
    #    not the question of whose hand signs it. An argued, approved change
    #    stays possible at every rung; a silent one never does.
    silent = "raise KERNEL_LOC_CAP to 6000"
    if not cap_case_missing(silent):
        failures.append("cap-case check accepted an unargued budget change")
    if route_proposal(silent) != "mailbox":
        failures.append("a cap-change proposal escaped human review")
    # The fixture above builds its input from the marker list, so it could
    # only ever pass -- P-001 inside the immune system itself. The case that
    # actually slipped was the WELL-ARGUED one: every required element, no
    # marker, routed to the agenda that rung 2 auto-promotes.
    argued = ("Per-file LOC: loop.py 757. Core pressure 0.88, closure "
              "pressure 0.65. Already externalized the view commands; "
              "insufficient. Proposed new cap 3400. Expected closure "
              "impact: none.")
    if cap_case_missing(argued):
        failures.append("cap-case check called a complete case incomplete")
    if route_proposal(argued) != "mailbox":
        failures.append("a complete cap case escaped the fence to the agenda")

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

        # The candidate tree, not this checkout. A gate handed the wrong tree
        # inspects unmodified code and passes everything (P-009).
        # The candidate tree AND the state that predates the mutation. The
        # mutation is already committed here, so HEAD is the change itself --
        # comparing against it would compare the change with itself (P-013).
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
        # Bill every attempt this cycle made, including the ones that
        # failed -- their tokens were spent all the same (P-015).
        try:
            ledger_mod.drain_attempts(cycle, models)
        except Exception:
            pass
        # The six questions, by construction -- not aspiration, schema.
        # The rationale summary travels here so a reviewer can answer all
        # six from the journal alone, without opening decisions.jsonl.
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
            # A rejection that records no reason teaches nothing: the candidate
            # is discarded with its branch, so if the objection is not captured
            # here it is gone. Rejections are the raw material of the pattern
            # register -- they must outlive the worktree.
            "rejected_by": [
                {"slot": v.get("slot"), "weakens_gate": v.get("weakens_gate"),
                 "reasons": [str(r)[:200] for r in (v.get("reasons") or [])[:3]]}
                for v in result.votes if v.get("verdict") != "approve"
            ],
            # MCR (mutation compression ratio): which tier resolved this task.
            # Recorded explicitly even while only Tier A exists, so the ratio
            # is measurable the moment Tier B appears. A falling Tier-A share
            # with flat kernel LOC means the kernel is getting harder to
            # understand without getting bigger.
            "tier": result.tier,
            # Per-slot agreement: the instrument for testing whether cheap
            # heterogeneous reviewers actually beat one strong reviewer.
            "slot_votes": {v.get("slot"): v.get("verdict") for v in result.votes},
            "reason": result.reason,
            "branch": branch if keep else "",
        })
        drop_worktree(workdir, branch, keep)


def print_utility() -> int:
    """Per-organ utility report. Logic in journal.print_utility."""
    return journal.print_utility(JOURNAL)


def print_body() -> int:
    """List every organ in the registry: id, version, lifecycle, capability.

    The body is as inspectable as the agenda. Reuses germline.registry() so
    the command and the closure calculator see the same source of truth.
    """
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


#: A proposal naming any of these is a proposal to change something the seed
#: may not change, or may change only under human review. It is routed to the
#: mailbox instead of the agenda queue. Data from a model passes through the
#: same protected-path scanning as a mutation from a model -- the reflect step
#: is a new way for model output to reach the work queue, so it needs the
#: same fence the mutation path already has.
PROPOSAL_GUARDED = (
    "root/", "substrate/", "meristem/gates/",
    "control/constitution.md", "control/checklists.md",
)

#: Changing the kernel's budget -- in EITHER direction -- is a contract-tier
#: act while the approval seat sits at rung 1. Raising it loosens what
#: reviewers must be able to see. Shrinking it can be a weakening wearing the
#: costume of metabolism: lines removed by deleting a check are not lines
#: saved. Both directions therefore need a complete case and an approval;
#: neither may ride in as an ordinary layer-1 mutation.
#:
#: The SEAT is a ladder position, not a permanent feature (v3.1 6.1): it
#: demotes on evidence like any other prosthetic, and the criteria are written
#: into decisions.jsonl alongside this. What never demotes is the requirement
#: that the change be argued -- that is monotonicity, not the human.
CAP_PROPOSAL_MARKERS = (
    "KERNEL_LOC_CAP", "kernel_loc_cap", "loc cap", "LOC cap",
    "raise the cap", "increase the cap", "lower the cap", "\u5185\u6838\u4e0a\u9650", "\u6269\u5bb9",
    "\u4e0a\u9650",  # bare: "\u63d0\u9ad8\u4e0a\u9650\u52304000" matches neither the 4-char marker nor \bcaps?\b
)

#: A cap case is incomplete without every one of these. Deterministic, so an
#: unargued proposal costs nothing to refuse.
CAP_CASE_REQUIRED = (
    "per-file", "core pressure", "closure pressure",
    "already externalized", "proposed", "expected",
)


def mentions_cap_change(text: str) -> bool:
    """Case-insensitive: a paraphrase like 'Raise The Cap' must not bypass
    the fence. Normalising both text and markers to lowercase closes the
    class for all case variants, not just the ones enumerated above."""
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in CAP_PROPOSAL_MARKERS):
        return True
    # Markers match PHRASING, CAP_CASE_REQUIRED matches SUBSTANCE, and they
    # were never aligned: a complete case ("Proposed new cap 3400.") names no
    # marker, so the fence caught sloppy proposals and let polished ones reach
    # the agenda -- which rung 2 auto-promotes. The word boundary catches
    # phrasings nobody enumerated without matching 'capability'.
    return re.search(r"\bcaps?\b", lowered) is not None


def cap_case_missing(text: str) -> list[str]:
    """Which mandatory elements a cap-change case fails to supply.

    Empty means the case is complete enough to be judged -- not that it is
    right. Judging is the reviewer's job; this only refuses to spend a
    reviewer on a proposal that has not done its homework.
    """
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
    """'agenda' for an ordinary proposal, 'mailbox' when it needs a human.

    Guarded ground routes to the mailbox. So does any proposal to change the
    kernel budget, in either direction, at EVERY rung -- the seed may make the
    case; it may not also grant it. The constitution is explicit ("raising
    either cap is a human decision, never a mutation's own"), so this fence
    does not lapse when the approval seat is promoted.

    Path matching is case-insensitive: 'Substrate/supervisor.py' and
    'ROOT/panic.py' are caught just as their lowercase forms are. The
    original case-sensitive substring check could be bypassed by any case
    paraphrase, which is the same class of failure as naming a guarded path
    with different casing to dodge the fence.
    """
    lowered = text.lower()
    if any(p in lowered for p in PROPOSAL_GUARDED) or mentions_cap_change(text):
        return "mailbox"
    return "agenda"


def print_probe_proposals() -> int:
    """List every probe proposal under state/probe-proposals/ with its id
    and whether it carries both a statement/ and a rubric/.

    A proposal is the staging form of a probe: the seed authors it, but the
    gates promote a validated proposal into the vault. The seed never writes
    to the vault directly (Principle 4: rubrics are physically invisible).
    """
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


PRESSURE_MANDATE_ASK = """The kernel budget is running out. This is not a
routine reflection: propose ONE concrete relief, and nothing else.

The constitution ranks the options, and the order is not a preference:

  1. externalize -- move a capability out of the kernel into an organ. Name
     which capability leaves, and to which organ. This is first because it
     lowers BOTH pressures at once.
  2. prune -- delete something that has stopped earning its lines.
  3. compress, or internalize with equal deletion -- budget-neutral only.
  4. change the cap -- LAST. If you propose this you must supply the full
     case: per-file LOC breakdown, core pressure, closure pressure, what was
     already externalized or pruned and why it was insufficient, the proposed
     new value, and the expected closure impact. An unargued cap proposal is
     refused before it is read, and a cap proposal is always held for a human.

Precedent from the constitution: if a review pack must cut modules to fit,
that is a signal to refactor the repo, not to reduce scope. Growth pressure
resolves by restructuring first; the budget moves last.

Reply with ONLY: {"proposals": ["one concrete relief"]}"""


def run_reflect(*, config=None, pressure: bool = False) -> int:
    """Reflect: compose the memory-graph organ with one model call to
    propose up to three concrete next tasks.

    Invokes the memory-graph organ (op "stale", threshold 0.5) for
    low-activation node ids; reads state/gaps.md and state/patterns.md;
    makes exactly ONE model call with the "score" role; appends proposals
    to state/proposals.md. Never writes to control/agenda.md -- a human
    promotes a proposal into the agenda.

    The prompt demands BOTH kinds of proposal every time: at least one
    repair (something measurably wrong) and at least one growth proposal
    (a capability not yet possessed). The constitution's phrase is
    "Spiral, not circular": a loop that only ever repairs converges on a
    fixed point and stops; a loop that only grows without repairing
    accumulates debt. Both directions are required every pass.
    """
    models = config or llm_mod.load_models()
    # Reflect writes no "cycle" record, so next_cycle() hands it the SAME
    # number every time -- and the per-cycle call cap counts every reflection
    # ever made against that one number. Twelve pressure beats reached 51
    # calls against a cap of 12 and every one of them faulted (P-021). A
    # reflection is its own accounting unit; it records that fact so the next
    # one gets a fresh number.
    cycle = journal.next_cycle(JOURNAL)
    append_jsonl(JOURNAL, {"kind": "cycle", "cycle": cycle,
                           "outcome": "reflection",
                           "why": "reflect" + (" --pressure" if pressure else ""),
                           "what": [], "reason": "reflection, not a mutation"})

    # 1. Invoke the memory-graph organ for stale (low-activation) node ids.
    #    The organ may not exist or may not be active yet; reflect still
    #    works with gaps and patterns alone.
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

    # 2. Read the registers that hold what is known to be wrong or recurring.
    gaps_text = read_text(REPO / "state" / "gaps.md")
    patterns_text = read_text(REPO / "state" / "patterns.md")

    # 3. Build a digest and make exactly ONE model call with the score role.
    #    The prompt demands both a repair and a growth proposal every time.
    #    Without this structural requirement the model collapses to whichever
    #    mode is easiest -- usually repair, because the evidence for it is
    #    already in the digest -- and the loop converges on a fixed point
    #    instead of spiralling outward (Principle 2: "Spiral, not circular").
    digest = (
        "# Reflection digest\n\n"
        "## Stale knowledge (low-activation node ids)\n"
        f"{', '.join(stale_ids) if stale_ids else '(none)'}\n\n"
        "## Gaps\n"
        f"{gaps_text}\n\n"
        "## Patterns\n"
        f"{patterns_text}\n\n"
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
    # Feed existing proposals into the digest so the model does not regenerate
    # duplicates. Dedup discards copies AFTER paying for them; only the prompt
    # stops regeneration and frees all 3 proposal slots for fresh ideas.
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
    if open_proposals:
        digest += (
            "\n\n## Already proposed (DO NOT repeat these)\n"
            + "\n".join(f"- {p}" for p in open_proposals)
        )
    # Self-observation: the seed reads its own report. REPORT.md holds the
    # aggregate evidence no single cycle can see -- acceptance rate, pressure
    # trend, parked tasks, probe scores, organ lifecycle. Without it reflect
    # sees only registers a human or a past mutation wrote, and can never
    # detect a failure CLASS (P2: meta-over-patch) from its own history.
    # Read the rendered report rather than recomputing: reporter.py is an
    # organ, and duplicating its arithmetic here would grow the core.
    report_text = read_text(REPO / "REPORT.md")
    if report_text.strip():
        digest += "\n\n## Self-report (last heartbeat)\n" + report_text[:2000]
    if pressure:
        # Under a mandate the generic ask is REPLACED, not appended to: a
        # budget about to bind is not one consideration among three, and
        # asking for a balanced spread would dilute the one answer needed.
        # The per-file breakdown is supplied because a relief must name a
        # specific capability to move, not a direction to move in.
        breakdown = "\n".join(
            f"  {q.relative_to(REPO).as_posix()}: "
            f"{len(q.read_text(encoding='utf-8').splitlines())} lines"
            for q in sorted((REPO / "meristem").rglob("*.py"))
        )
        digest = (
            f"## Kernel LOC by file\n{breakdown}\n\n"
            f"## Pressures\ncore {deterministic.kernel_loc()}/"
            f"{deterministic.KERNEL_LOC_CAP}; closure "
            f"{closure_mod.compute([]).tokens}/{deterministic.CLOSURE_TOKEN_CAP}\n\n"
            f"## Gaps\n{gaps_text}\n\n{PRESSURE_MANDATE_ASK}"
        )
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

    # 4. Parse proposals from the model reply. Unparseable is zero
    #    proposals -- the call was still recorded through the ledger.
    proposals: list = []
    try:
        data = engine_mod._parse(completion.text)
        raw = data.get("proposals", [])
        if isinstance(raw, list):
            proposals = raw
    except Exception:
        pass

    # 5. Append to state/proposals.md -- never to control/agenda.md.
    #    A human promotes a proposal into the agenda; the seed proposes but
    #    does not self-schedule.
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
    queued = held = skipped = 0
    for proposal in proposals[:3]:
        text = str(proposal).strip()
        if not text:
            continue
        if _is_duplicate_proposal(text, all_dedup_lines):
            skipped += 1
        elif route_proposal(text) == "mailbox":
            # cap_case_missing existed, was tested, and was never applied to a
            # live proposal: an unargued "raise the cap" and a complete case
            # both landed labelled "names guarded ground". The human could not
            # tell them apart; the seed was never told what its case lacked.
            # Labels must stay COLON-FREE: the dedup above recovers a proposal
            # by splitting the line on its first ': ', so a colon inside the
            # label steals the split and the key never matches -- every reflect
            # would re-append the same case, the P-031 spam loop returning
            # through the one guard that closed it.
            if mentions_cap_change(text):
                missing = cap_case_missing(text)
                why = (f"cap change; case INCOMPLETE, missing {', '.join(missing)}"
                       if missing else "cap change; case complete, awaiting human grant")
            else:
                why = "needs human review, names guarded ground"
            with mailbox_path.open("a", encoding="utf-8") as handle:
                handle.write(f"- PROPOSAL ({why}): {text}\n")
            all_dedup_lines.append(f"- [ ] {text}")
            held += 1
        else:
            with proposals_path.open("a", encoding="utf-8") as handle:
                handle.write(f"- [ ] {text}\n")
            all_dedup_lines.append(f"- [ ] {text}")
            queued += 1

    print(f"appended {queued} proposal(s) to state/proposals.md; "
          f"{held} held for human review in state/mailbox.md"
          + (f"; {skipped} duplicate(s) skipped" if skipped else ""))
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
    pt = {l.strip()[6:].strip() for l in proposals.splitlines() if l.strip().startswith("- [ ] ")}
    n_acc = sum(1 for c in cycles if c.get("outcome") == "candidate")
    agr = f"{sum(1 for c in cycles if c.get('outcome') == 'candidate' and c.get('why', '') in pt)}/{n_acc}" if n_acc else "0/0"
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="meristem", description="Meristem evolution loop")
    parser.add_argument("command", choices=["cycle", "status", "selftest", "gaps", "body",
                                            "spend", "probe-proposals", "agenda", "reflect",
                                            "utility", "report"])
    parser.add_argument("--task", help="override the task instead of taking one from the agenda")
    parser.add_argument("--pressure", action="store_true",
                        help="reflect under a pressure mandate: propose ONE concrete "
                             "relief for a kernel budget that is running out")
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
        # Core Pressure: how close the generating point is to its cap.
        # The design says externalize at >=0.9 -- proactively, not when
        # the deterministic gate finally refuses a change. Reporting it
        # is what makes "not yet needed" a measurement rather than a
        # guess about capability that was never built.
        core_pressure = loc / deterministic.KERNEL_LOC_CAP
        closure_now = closure_mod.compute([]).tokens
        closure_pressure = closure_now / deterministic.CLOSURE_TOKEN_CAP
        print(f"kernel LOC      : {loc} / {deterministic.KERNEL_LOC_CAP}")
        print(f"  core pressure : {core_pressure:.2f}"
              + ("  <- externalize capability into an organ" if core_pressure >= 0.9 else ""))
        print(f"  closure press : {closure_pressure:.2f} ({closure_now} tokens)"
              + ("  <- split an organ" if closure_pressure >= 0.9 else ""))
        print(f"organs (body)   : {len(organs)}"
              + (f"  [{', '.join(f'{o.id}:{o.lifecycle}' for o in organs)}]" if organs else ""))
        print(f"MCR by tier     : {tiers or '(none accepted yet)'}")
        for slot, tally in sorted(slot_stats.items()):
            print(f"  reviewer {slot:20s} approve={tally['approve']} reject={tally['reject']}")
        print(f"spent (USD)     : {ledger_mod.spent():.4f}  calls: {ledger_mod.calls()}")
        print(f"unpublished     : {ahead} commit(s) ahead of origin/main")
        print(f"open agenda item: {take_task() or '(none)'}")
        return 0

    task = args.task or take_task()
    if not task:
        print("agenda is empty; nothing to do")
        return 0

    # Circuit breaker: park a task that has been rejected too many times
    # before any model call is made. Unbounded retry on the same task is
    # not progress, it is a loop -- the breaker turns that loop into a stop.
    if breaker_mod.should_park(task):
        reason_str = journal.park_task(task, JOURNAL, REPO)
        print(f"task parked: {task} ({reason_str})")
        _notify_park(task, reason_str)
        return 0

    cycle = journal.next_cycle(JOURNAL)
    try:
        result = run_cycle(task, cycle)
    except Exception as exc:
        # Anything unexpected is still a cycle outcome, not a traceback. An
        # unhandled exception left the journal recording a rejection with no
        # reason -- a rejection that teaches nothing (P-012).
        append_jsonl(JOURNAL, {"kind": "fault", "cycle": cycle, "task": task,
                               "error": f"{type(exc).__name__}: {exc}"[:400]})
        print(f"cycle {cycle} FAULT: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"cycle {cycle}: {result.outcome} -- {result.reason}")
    if result.outcome == "candidate":
        print(f"candidate on branch {result.branch}; run substrate/supervisor.py promote")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
