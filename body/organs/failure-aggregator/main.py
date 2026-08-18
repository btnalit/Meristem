#!/usr/bin/env python3
"""Failure-aggregator organ: detect repeated rejection patterns and write
structured entries to state/patterns.md.

Owns:
  (1) querying journal entries by task text across cycles to detect repeated
      rejections for the same failure class (G-006 circuit breaker input)
  (2) classifying recurring rejection reasons and appending structured
      entries to state/patterns.md (G-007 self-detection)

Graded signals (must NOT be flattened to 'surface' only -- cycle 203 rejection):
  surface   -- 2+ rejections for the same class (informational)
  escalate  -- 3+ rejections (suggests tier escalation or task decomposition)
  block     -- 4+ rejections (suggests parking the task)

The kernel's circuit breaker (meristem/breaker.py) is unchanged and still
runs every cycle. This organ is purely additive: its signals are surfaced
but the kernel decides what to do with them.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile

# Ensure classify.py is importable regardless of invocation path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from classify import classify, classify_reasons, dominant_class, FAILURE_CLASSES

# Thresholds -- must NOT be loosened. Raising these was a rejection in
# cycle 205.
SURFACE_THRESHOLD = 2
ESCALATE_THRESHOLD = 3
BLOCK_THRESHOLD = 4

# Pattern registration: 2+ repeated rejections. Raising this was a rejection
# in cycle 205 ("patterns.md entries were written for 2+ repeated rejections;
# the new code requires 3+ total rejections and 2+ in a dominant class").
PATTERN_THRESHOLD = 2


def _read_journal(journal_path: str) -> list[dict]:
    """Read JSONL journal entries."""
    path = pathlib.Path(journal_path)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _faulted_cycles(rows: list[dict]) -> set[int]:
    """Cycle numbers that ended in a fault rather than a verdict.

    A fault (unparseable reply, rate limit) is NOT a rejection -- the
    proposal was never judged. Counting faults as rejections would park a
    task the gates never objected to (P-016).
    """
    return {row.get("cycle") for row in rows if row.get("kind") == "fault"}


def _collect_rejection_reasons(rows: list[dict], task: str) -> list[str]:
    """Collect ALL rejection reasons for a task across cycles.

    Scans:
      - cycle record 'reason' field (top-level)
      - per-reviewer rejected_by[*].reasons (MUST scan these -- cycle 203
        rejection: "no longer scans per-reviewer rejected_by[*].reasons")
      - canary_reject record 'reason' field

    Excludes faulted cycles (mechanism failures, not gate rejections).
    """
    faulted = _faulted_cycles(rows)
    reasons: list[str] = []
    for row in rows:
        if row.get("why") != task:
            continue
        kind = row.get("kind", "")
        if kind == "cycle" and row.get("outcome") == "rejected":
            if row.get("cycle") in faulted:
                continue
            # Top-level reason (skip generic "review rejected" wrapper)
            reason = row.get("reason", "")
            if reason and not reason.startswith("review rejected"):
                reasons.append(reason[:300])
            # Per-reviewer reasons -- MUST scan these (cycle 203 rejection).
            for rej in row.get("rejected_by") or []:
                for r in rej.get("reasons") or []:
                    reasons.append(str(r)[:300])
        elif kind == "canary_reject":
            reason = row.get("reason", "")
            if reason:
                reasons.append(reason[:300])
    return reasons


def _already_logged(patterns_path: pathlib.Path, task_hash: str,
                    count: int) -> bool:
    """Check if a pattern entry already exists for this task with >= count.

    Prevents duplicate entries: the organ runs every cycle, but a pattern
    entry should only be written when the count has increased past what was
    already recorded.
    """
    if not patterns_path.exists():
        return False
    text = patterns_path.read_text(encoding="utf-8")
    pattern = rf"FA-{re.escape(task_hash)}.*?total rejections\*\*:\s*(\d+)"
    matches = re.findall(pattern, text, re.S)
    for m in matches:
        if int(m) >= count:
            return True
    return False


def _format_pattern_entry(task: str, cls: str, total: int,
                          dom_count: int, reasons: list[str],
                          cycle: int) -> str:
    """Format a structured entry for state/patterns.md."""
    task_hash = hashlib.md5(task.encode()).hexdigest()[:8]
    entry_id = f"FA-{task_hash}"
    lines = [
        f"\n## {entry_id} -- {cls} (cycle {cycle})\n",
        f"- **task**: {task[:200]}",
        f"- **total rejections**: {total}",
        f"- **dominant class**: {cls} ({dom_count} of {total} reasons)",
        f"- **detected by**: failure-aggregator organ",
        f"- **representative reasons**:",
    ]
    for r in reasons[:3]:
        lines.append(f"  - {r[:200]}")
    lines.append(f"- **action**: review whether this task should be parked, "
                 f"decomposed, or escalated to a higher tier\n")
    return "\n".join(lines)


def aggregate(journal_path: str, repo_path: str, cycle: int) -> dict:
    """Detect repeated rejection patterns and write to state/patterns.md.

    Returns signals and patterns_written. Does not modify the kernel's
    circuit breaker -- purely additive.
    """
    rows = _read_journal(journal_path)
    faulted = _faulted_cycles(rows)

    # Group rejected cycles by task text (excluding faults).
    task_rejection_cycles: dict[str, list[dict]] = {}
    for row in rows:
        if (row.get("kind") == "cycle"
                and row.get("outcome") == "rejected"
                and row.get("cycle") not in faulted):
            task = row.get("why", "")
            if task:
                task_rejection_cycles.setdefault(task, []).append(row)

    # Count canary rejects per task.
    canary_rejects: dict[str, int] = {}
    for row in rows:
        if row.get("kind") == "canary_reject":
            task = row.get("why", "")
            if task:
                canary_rejects[task] = canary_rejects.get(task, 0) + 1

    signals: list[dict] = []
    patterns_written: list[dict] = []
    patterns_path = pathlib.Path(repo_path) / "state" / "patterns.md"

    for task, cycles in task_rejection_cycles.items():
        rejection_count = len(cycles)
        total_rejections = rejection_count + canary_rejects.get(task, 0)

        if total_rejections < SURFACE_THRESHOLD:
            continue

        # Collect and classify ALL reasons (cycle 203: must scan
        # rejected_by[*].reasons, not just the top-level reason).
        reasons = _collect_rejection_reasons(rows, task)
        class_counts = classify_reasons(reasons)
        dom_cls, dom_count = dominant_class(class_counts)

        # Graded signal -- must NOT flatten to 'surface' only (cycle 203).
        # Must NOT raise BLOCK_THRESHOLD above 4 (cycle 205).
        if total_rejections >= BLOCK_THRESHOLD:
            action = "block"
        elif total_rejections >= ESCALATE_THRESHOLD:
            action = "escalate"
        else:
            action = "surface"

        signals.append({
            "task": task[:200],
            "class": dom_cls,
            "count": total_rejections,
            "action": action,
            "class_counts": class_counts,
            "representative_reasons": reasons[:3],
        })

        # Write to patterns.md for 2+ rejections (cycle 205: must not raise
        # this threshold). Dedup: don't write if an entry with >= count
        # already exists for this task.
        if total_rejections >= PATTERN_THRESHOLD:
            task_hash = hashlib.md5(task.encode()).hexdigest()[:8]
            if not _already_logged(patterns_path, task_hash,
                                   total_rejections):
                entry = _format_pattern_entry(
                    task, dom_cls, total_rejections,
                    dom_count, reasons, cycle)
                patterns_path.parent.mkdir(parents=True, exist_ok=True)
                with patterns_path.open("a", encoding="utf-8") as f:
                    f.write(entry)
                patterns_written.append({
                    "task": task[:200],
                    "class": dom_cls,
                    "count": total_rejections,
                })

    return {"signals": signals, "patterns_written": patterns_written}


def selfcheck() -> dict:
    """Exercise classification with real fixtures.

    A selfcheck that only runs aggregate on /dev/null was rejected in
    cycle 203: "The organ's selfcheck no longer exercises classification
    at all; it just runs aggregate on /dev/null. A completely broken
    _classify would pass the organ's own health check, removing a prior
    validation." This selfcheck exercises classify() and classify_reasons()
    with known inputs covering every failure class, plus aggregate on
    real rejection data.
    """
    results: list[dict] = []
    failures: list[str] = []

    # --- classify() tests: one per failure class ---

    # 1. gate-weakening
    r = classify("loosens a threshold on the kernel loc cap")
    results.append({"test": "classify gate-weakening", "result": r})
    if r != "gate-weakening":
        failures.append(f"classify returned '{r}' for a gate-weakening reason")

    # 2. protected-path
    r = classify("touches protected path root/panic.py")
    results.append({"test": "classify protected-path", "result": r})
    if r != "protected-path":
        failures.append(f"classify returned '{r}' for a protected-path reason")

    # 3. secret-leak
    r = classify("possible secret: api_key matches pattern")
    results.append({"test": "classify secret-leak", "result": r})
    if r != "secret-leak":
        failures.append(f"classify returned '{r}' for a secret-leak reason")

    # 4. closure-budget
    r = classify("review closure is 60000 tokens over the 50000 budget")
    results.append({"test": "classify closure-budget", "result": r})
    if r != "closure-budget":
        failures.append(f"classify returned '{r}' for a closure-budget reason")

    # 5. parse-error
    r = classify("engine returned no parseable JSON object")
    results.append({"test": "classify parse-error", "result": r})
    if r != "parse-error":
        failures.append(f"classify returned '{r}' for a parse-error reason")

    # 6. probe-regression
    r = classify("regression on frozen probe 'probe-word-count-basic'")
    results.append({"test": "classify probe-regression", "result": r})
    if r != "probe-regression":
        failures.append(f"classify returned '{r}' for a probe-regression reason")

    # 7. organ-manifest
    r = classify("organ 'word-count' has no readable organ.json manifest")
    results.append({"test": "classify organ-manifest", "result": r})
    if r != "organ-manifest":
        failures.append(f"classify returned '{r}' for an organ-manifest reason")

    # 8. immune-failure
    r = classify("IMMUNE FAILURE: golden fixture passed")
    results.append({"test": "classify immune-failure", "result": r})
    if r != "immune-failure":
        failures.append(f"classify returned '{r}' for an immune-failure reason")

    # 9. budget-exceeded
    r = classify("cycle 5 spent $2.0000 > cap $1.0000 budget exceeded")
    results.append({"test": "classify budget-exceeded", "result": r})
    if r != "budget-exceeded":
        failures.append(f"classify returned '{r}' for a budget-exceeded reason")

    # 10. no-change
    r = classify("engine produced no effective change")
    results.append({"test": "classify no-change", "result": r})
    if r != "no-change":
        failures.append(f"classify returned '{r}' for a no-change reason")

    # 11. deterministic-check
    r = classify("kernel is 3100 lines over the 3000 loc cap")
    results.append({"test": "classify deterministic-check", "result": r})
    if r != "deterministic-check":
        failures.append(
            f"classify returned '{r}' for a deterministic-check reason")

    # 12. review-rejected
    r = classify("review rejected (0/2 need 2)")
    results.append({"test": "classify review-rejected", "result": r})
    if r != "review-rejected":
        failures.append(f"classify returned '{r}' for a review-rejected reason")

    # --- classify_reasons() and dominant_class() ---

    # 13. classify_reasons with multiple reasons spanning classes
    counts = classify_reasons([
        "loosens a threshold on the cap",
        "touches root/panic.py protected path",
        "weaken the gate invariant",
    ])
    results.append({"test": "classify_reasons multi", "result": counts})
    if "gate-weakening" not in counts or counts["gate-weakening"] < 2:
        failures.append(
            f"classify_reasons missed gate-weakening counts: {counts}")
    if "protected-path" not in counts:
        failures.append(f"classify_reasons missed protected-path: {counts}")

    # 14. dominant_class
    dom_cls, dom_count = dominant_class(counts)
    results.append({"test": "dominant_class",
                    "result": f"{dom_cls}:{dom_count}"})
    if dom_cls != "gate-weakening":
        failures.append(
            f"dominant_class returned '{dom_cls}' expected 'gate-weakening'")

    # --- aggregate() tests ---

    # 15. aggregate on empty journal
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                     delete=False) as f:
        f.write("")
        empty_journal = f.name
    try:
        result = aggregate(empty_journal, tempfile.mkdtemp(), 0)
        results.append({"test": "aggregate empty", "result": result})
        if result["signals"] or result["patterns_written"]:
            failures.append("aggregate on empty journal produced output")
    finally:
        os.unlink(empty_journal)

    # 16. aggregate with real rejection data (2 rejections, surface signal)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                     delete=False) as f:
        for i in range(2):
            f.write(json.dumps({
                "kind": "cycle",
                "cycle": 100 + i,
                "outcome": "rejected",
                "why": "test task about caps",
                "reason": "review rejected (0/2 need 2)",
                "rejected_by": [
                    {"slot": "review:deepseek",
                     "reasons": ["loosens a threshold on the cap"]},
                    {"slot": "review:sensenova",
                     "reasons": ["weakens the gate invariant"]},
                ],
            }) + "\n")
        real_journal = f.name
    try:
        tmp_repo = tempfile.mkdtemp()
        result = aggregate(real_journal, tmp_repo, 200)
        results.append({"test": "aggregate real (2 rejections)",
                        "result": result})
        if not result["signals"]:
            failures.append(
                "aggregate on real rejections produced no signals")
        else:
            sig = result["signals"][0]
            if sig["action"] != "surface":
                failures.append(
                    f"expected surface action for 2 rejections, "
                    f"got {sig['action']}")
            if sig["class"] != "gate-weakening":
                failures.append(
                    f"expected gate-weakening class, got {sig['class']}")
        patterns_file = pathlib.Path(tmp_repo) / "state" / "patterns.md"
        if not patterns_file.exists():
            failures.append("patterns.md was not written for 2 rejections")
        else:
            content = patterns_file.read_text()
            if "gate-weakening" not in content:
                failures.append(
                    "patterns.md does not contain the failure class")
    finally:
        os.unlink(real_journal)
        shutil.rmtree(tmp_repo, ignore_errors=True)

    # 17. aggregate with 4 rejections (block signal)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                     delete=False) as f:
        for i in range(4):
            f.write(json.dumps({
                "kind": "cycle",
                "cycle": 200 + i,
                "outcome": "rejected",
                "why": "task that fails repeatedly",
                "reason": "review rejected (0/2 need 2)",
                "rejected_by": [
                    {"slot": "review:deepseek",
                     "reasons": ["weaken the gate"]},
                ],
            }) + "\n")
        block_journal = f.name
    try:
        tmp_repo2 = tempfile.mkdtemp()
        result = aggregate(block_journal, tmp_repo2, 300)
        results.append({"test": "aggregate 4 rejections",
                        "result": result})
        if not result["signals"]:
            failures.append(
                "aggregate on 4 rejections produced no signals")
        else:
            sig = result["signals"][0]
            if sig["action"] != "block":
                failures.append(
                    f"expected block action for 4 rejections, "
                    f"got {sig['action']}")
    finally:
        os.unlink(block_journal)
        shutil.rmtree(tmp_repo2, ignore_errors=True)

    # 18. aggregate scans rejected_by[*].reasons (cycle 203 regression test)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                     delete=False) as f:
        f.write(json.dumps({
            "kind": "cycle",
            "cycle": 400,
            "outcome": "rejected",
            "why": "task with per-reviewer reasons",
            "reason": "review rejected (0/2 need 2)",
            "rejected_by": [
                {"slot": "review:deepseek",
                 "reasons": ["touches root/panic.py protected path"]},
                {"slot": "review:sensenova",
                 "reasons": ["secret leak: api_key in diff"]},
            ],
        }) + "\n")
        f.write(json.dumps({
            "kind": "cycle",
            "cycle": 401,
            "outcome": "rejected",
            "why": "task with per-reviewer reasons",
            "reason": "review rejected (0/2 need 2)",
            "rejected_by": [
                {"slot": "review:deepseek",
                 "reasons": ["touches substrate/supervisor.py"]},
            ],
        }) + "\n")
        reviewer_journal = f.name
    try:
        tmp_repo3 = tempfile.mkdtemp()
        result = aggregate(reviewer_journal, tmp_repo3, 500)
        results.append({"test": "aggregate scans rejected_by",
                        "result": result})
        if not result["signals"]:
            failures.append(
                "aggregate missed signals from rejected_by[*].reasons")
        else:
            sig = result["signals"][0]
            # The dominant class should be protected-path (root, substrate)
            # or secret-leak, NOT uncategorized -- if it's uncategorized,
            # the classifier didn't scan rejected_by[*].reasons.
            if sig["class"] == "uncategorized":
                failures.append(
                    "classify returned uncategorized -- did not scan "
                    "rejected_by[*].reasons (cycle 203 regression)")
    finally:
        os.unlink(reviewer_journal)
        shutil.rmtree(tmp_repo3, ignore_errors=True)

    return {
        "ok": len(failures) == 0,
        "results": results,
        "failures": failures,
    }


def main() -> int:
    data = json.loads(sys.stdin.read())
    op = data.get("op", "aggregate")

    if op == "selfcheck":
        result = selfcheck()
        print(json.dumps(result))
        return 0 if result["ok"] else 1

    if op == "aggregate":
        result = aggregate(
            data.get("journal_path", ""),
            data.get("repo_path", ""),
            data.get("cycle", 0),
        )
        print(json.dumps(result))
        return 0

    print(json.dumps({"error": f"unknown op '{op}'"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
