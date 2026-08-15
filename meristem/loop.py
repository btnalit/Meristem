"""The evolution loop: one cycle, end to end.

    task -> mutation -> candidate commit -> gates -> hand to substrate

The loop never promotes. It produces a candidate and hands it to the
substrate, which owns promotion and runs its own independent protected-path
check. That separation is the point: the code being reviewed must not be the
code that decides what gets in.

A cycle is a git transaction. The worktree is the boundary; a crash discards
the branch and the step is simply rerun. That is why this file contains no
recovery machinery -- at seed scale, branch isolation dissolves the problem.

Every accepted mutation answers six questions, by construction (the journal
entry schema below): why, what, which probe proves it better, which old
capabilities are shown not to have regressed, what it cost, who approved.
The rationale summary travels with the journal entry so the six questions can
be answered from the journal alone, without opening decisions.jsonl.
"""

from __future__ import annotations

import argparse
import json
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
    read_json,
    read_jsonl,
    read_text,
    write_json,
)
from . import engine as engine_mod
from . import ledger as ledger_mod
from . import llm as llm_mod
from .gates import closure as closure_mod
from .gates import deterministic, probes, review

CANDIDATE_REF = "meristem/candidate"


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


def next_cycle() -> int:
    return 1 + max(
        (row.get("cycle", 0) for row in read_jsonl(JOURNAL) if row.get("kind") == "cycle"),
        default=0,
    )


def done_tasks() -> set[str]:
    """Tasks that already produced a candidate, read from the journal.

    Completion is a RECORD, not an edit. Marking the agenda file in place made
    the loop dirty its own checkout every cycle without ever committing --
    which blocked git operations and left the tree in a state no transaction
    owned. The journal already knows what succeeded; ask it.
    """
    return {
        row.get("why", "")
        for row in read_jsonl(JOURNAL)
        if row.get("kind") == "cycle" and row.get("outcome") == "candidate"
    }


def take_task() -> str | None:
    """P0: the human is the first reflect. Tasks come from control/agenda.md,
    which stays pure human-authored source -- the loop never writes to it.

    P1 grows reflect, which proposes its own -- but the seed is not handed a
    parts list; it is handed the capability gap and proposes the decomposition.
    A rejected task stays open, so it is retried.
    """
    done = done_tasks()
    for line in read_text(CONTROL / "agenda.md").splitlines():
        line = line.strip()
        if line.startswith("- [ ] ") and line[6:].strip() not in done:
            return line[6:].strip()
    return None


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
            register.write_text("# Pattern Register\n\n## Z-999 — only entry\n",
                                encoding="utf-8")
            if not deterministic.memory_integrity(["state/patterns.md"]):
                failures.append("memory-integrity check missed an erased register")
        finally:
            register.write_text(original, encoding="utf-8")

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
        mutation = engine_mod.propose(task, config=models)
        result.rationale = mutation.rationale
        result.usd += ledger_mod.record(cycle, "mutate", mutation.completion, models)
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
        verdict = deterministic.run(result.changed, root=workdir)
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
        review_result = review.run(diff, task, computed.files, config=models)
        result.votes = review_result.votes
        for completion in review_result.completions:
            result.usd += ledger_mod.record(cycle, "review", completion, models)
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
    finally:
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
            "reason": result.reason,
            "branch": branch if keep else "",
        })
        drop_worktree(workdir, branch, keep)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="meristem", description="Meristem evolution loop")
    parser.add_argument("command", choices=["cycle", "status", "selftest"])
    parser.add_argument("--task", help="override the task instead of taking one from the agenda")
    args = parser.parse_args(argv)

    if args.command == "selftest":
        failures = golden_fixtures()
        for failure in failures:
            print(f"FAIL {failure}")
        print("immune self-test:", "FAILED" if failures else "ok")
        return 1 if failures else 0

    if args.command == "status":
        cycles = [r for r in read_jsonl(JOURNAL) if r.get("kind") == "cycle"]
        print(f"cycles run     : {len(cycles)}")
        print(f"candidates      : {sum(1 for c in cycles if c['outcome'] == 'candidate')}")
        print(f"kernel LOC      : {deterministic.kernel_loc()} / {deterministic.KERNEL_LOC_CAP}")
        print(f"spent (USD)     : {ledger_mod.spent():.4f}")
        print(f"open agenda item: {take_task() or '(none)'}")
        return 0

    task = args.task or take_task()
    if not task:
        print("agenda is empty; nothing to do")
        return 0
    cycle = next_cycle()
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
