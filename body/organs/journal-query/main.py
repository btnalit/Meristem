#!/usr/bin/env python3
"""Journal-query organ: query the journal for failure patterns.

Operations:
  query                - aggregated rejection counts for a single task
  aggregate_by_task    - per-task rejection summaries
  aggregate_cross_task - cross-task failure class aggregation
  selfcheck            - exercise all operations with tiny fixtures

The query op returns flat counts: {"ok": true, "rejections": N, "faults": N,
"canary_rejects": N}. The aggregate_by_task op returns per-task summaries
under the "tasks" key. The aggregate_cross_task op returns per-class
summaries under the "classes" key, grouping rejections by failure class
across different tasks to detect systemic patterns.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _resolve_journal(journal_path):
    """Resolve journal path relative to REPO_ROOT.

    Relative paths resolve against REPO_ROOT, not the organ's cwd, to
    prevent silently reading the wrong journal -- the bug class that
    silenced the failure-aggregator when cwd was the organ directory.
    """
    p = pathlib.Path(journal_path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def _read_journal(journal_path):
    """Read journal entries from a JSONL file."""
    path = _resolve_journal(journal_path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _faulted_cycles(entries):
    """Cycle numbers that ended in a fault rather than a verdict (P-016).

    A fault and a rejection look identical in the outcome field (both land
    as 'rejected') but mean opposite things. A rejection is the gates
    working; a fault is the mechanism failing. Counting them together
    parks a task the gates never objected to.
    """
    return {
        row.get("cycle") for row in entries
        if row.get("kind") == "fault"
    }


def query_task(entries, task):
    """Aggregate rejection counts for a single task.

    Returns the same counts the circuit breaker uses:
    rejections (judged, excluding faults), faults, canary_rejects.
    Task matching is case-insensitive (G-007).
    """
    faulted = _faulted_cycles(entries)
    task_lower = task.strip().lower()

    rejections = 0
    faults = 0
    canary_rejects = 0

    for row in entries:
        kind = row.get("kind", "")
        why = row.get("why", "")

        if why.strip().lower() != task_lower:
            continue

        if kind == "cycle" and row.get("outcome") == "rejected":
            if row.get("cycle") in faulted:
                faults += 1
            else:
                rejections += 1
        elif kind == "canary_reject":
            canary_rejects += 1

    return {
        "rejections": rejections,
        "faults": faults,
        "canary_rejects": canary_rejects,
    }


def aggregate_by_task(entries, task_filter=None):
    """Aggregate rejections grouped by task.

    Returns per-task summaries with rejection reasons (with slot
    attribution), cycle counts, outcomes, and timestamps. Faults are
    excluded from rejection counts (P-016). Task matching is
    case-insensitive (G-007).
    """
    faulted = _faulted_cycles(entries)
    filter_lower = task_filter.strip().lower() if task_filter else None

    tasks = {}

    for row in entries:
        kind = row.get("kind", "")
        why = row.get("why", "")

        if not why:
            continue

        if filter_lower and why.strip().lower() != filter_lower:
            continue

        if why not in tasks:
            tasks[why] = {
                "task": why,
                "rejections": 0,
                "faults": 0,
                "canary_rejects": 0,
                "rejection_reasons": [],
                "total_cycles": 0,
                "outcomes": {},
                "timestamps": [],
                "first_seen": None,
                "last_seen": None,
            }

        entry = tasks[why]

        if kind == "cycle":
            entry["total_cycles"] += 1
            outcome = row.get("outcome", "unknown")
            entry["outcomes"][outcome] = entry["outcomes"].get(outcome, 0) + 1

            if outcome == "rejected" and row.get("cycle") not in faulted:
                entry["rejections"] += 1
                for rej in row.get("rejected_by") or []:
                    slot = rej.get("slot", "unknown")
                    for r in rej.get("reasons") or []:
                        entry["rejection_reasons"].append(
                            f"{slot}: {r[:200]}")
            elif row.get("cycle") in faulted:
                entry["faults"] += 1

        elif kind == "canary_reject":
            entry["canary_rejects"] += 1
            reason = row.get("reason", "")
            if reason:
                entry["rejection_reasons"].append(f"canary: {reason[:200]}")

        ts = row.get("ts", "")
        if ts:
            entry["timestamps"].append(ts)
            if entry["first_seen"] is None or ts < entry["first_seen"]:
                entry["first_seen"] = ts
            if entry["last_seen"] is None or ts > entry["last_seen"]:
                entry["last_seen"] = ts

    return list(tasks.values())


def _classify_failure(reason_text):
    """Classify a rejection reason into a failure class.

    Conservative keyword-based classifier: when no class matches, returns
    'unclassified' rather than guessing. The classes correspond to common
    failure patterns in Meristem's review rejections.
    """
    text = reason_text.lower()
    if any(kw in text for kw in
           ["gate", "weaken", "immune", "monotonic", "narrow", "disable"]):
        return "gate-weakening"
    if any(kw in text for kw in
           ["closure", "budget", "token", "review context"]):
        return "closure-budget"
    if any(kw in text for kw in
           ["probe", "regression", "score", "frozen"]):
        return "probe-regression"
    if any(kw in text for kw in
           ["secret", "credential", "api key", "private key"]):
        return "secret-leak"
    if any(kw in text for kw in
           ["protected", "root/", "substrate/"]):
        return "protected-path"
    if any(kw in text for kw in
           ["append-only", "memory", "register", "erased", "entry"]):
        return "memory-integrity"
    if any(kw in text for kw in
           ["manifest", "germline", "lifecycle"]):
        return "organ-manifest"
    if any(kw in text for kw in
           ["syntax", "import", "parse", "traceback"]):
        return "syntax-error"
    if any(kw in text for kw in
           ["cap", "loc", "line count", "kernel size"]):
        return "kernel-cap"
    if any(kw in text for kw in
           ["dependency", "undeclared"]):
        return "undeclared-dependency"
    return "unclassified"


def aggregate_cross_task(entries, min_tasks=2):
    """Aggregate rejections across different tasks by failure class.

    Unlike aggregate_by_task (which groups by task), this groups by
    failure class across tasks to detect systemic patterns -- e.g.,
    'closure-budget' rejections appearing across multiple unrelated tasks
    indicate a structural problem, not a task-specific one.

    Only classes appearing across >= min_tasks different tasks are
    returned, so the result surfaces systemic patterns rather than
    task-specific ones.

    Faults are excluded (P-016: faults are not judged rejections).
    Canary rejections are included, classified by their reason text.
    """
    faulted = _faulted_cycles(entries)
    by_class = {}

    for row in entries:
        kind = row.get("kind", "")
        task = row.get("why", "")
        if not task:
            continue

        reasons = []
        ts = row.get("ts", "")
        cycle = row.get("cycle", "?")

        if kind == "cycle" and row.get("outcome") == "rejected":
            if row.get("cycle") in faulted:
                continue  # P-016: faults are not judged rejections
            for rej in row.get("rejected_by") or []:
                slot = rej.get("slot", "unknown")
                for r in rej.get("reasons") or []:
                    reasons.append(f"{slot}: {r}")
            if not reasons:
                reason_text = row.get("reason", "")
                if reason_text:
                    reasons.append(reason_text)
        elif kind == "canary_reject":
            reason_text = row.get("reason", "")
            if reason_text:
                reasons.append(f"canary: {reason_text}")
        else:
            continue

        for reason_text in reasons:
            failure_class = _classify_failure(reason_text)
            if failure_class not in by_class:
                by_class[failure_class] = {
                    "class": failure_class,
                    "count": 0,
                    "tasks": set(),
                    "cycles": [],
                    "representative_reasons": [],
                    "first_seen": None,
                    "last_seen": None,
                }
            entry = by_class[failure_class]
            entry["count"] += 1
            entry["tasks"].add(task)
            entry["cycles"].append(cycle)
            if len(entry["representative_reasons"]) < 5:
                entry["representative_reasons"].append(
                    reason_text[:200])
            if ts:
                if entry["first_seen"] is None or ts < entry["first_seen"]:
                    entry["first_seen"] = ts
                if entry["last_seen"] is None or ts > entry["last_seen"]:
                    entry["last_seen"] = ts

    result = []
    for cls_name in sorted(by_class.keys()):
        data = by_class[cls_name]
        if len(data["tasks"]) >= min_tasks:
            result.append({
                "class": data["class"],
                "count": data["count"],
                "task_count": len(data["tasks"]),
                "tasks": sorted(data["tasks"]),
                "cycles": data["cycles"],
                "representative_reasons": data["representative_reasons"],
                "first_seen": data["first_seen"],
                "last_seen": data["last_seen"],
            })
    return result


def op_query(data):
    """Query operation: return aggregated counts for a single task.

    Returns flat counts: {"ok": true, "rejections": N, "faults": N,
    "canary_rejects": N}.
    """
    journal_path = data.get("journal_path", "state/journal.jsonl")
    task = data.get("task", "")
    if not task:
        return {"ok": False, "error": "missing 'task' field"}
    entries = _read_journal(journal_path)
    counts = query_task(entries, task)
    return {"ok": True, **counts}


def op_aggregate_by_task(data):
    """Aggregate by task operation: per-task rejection summaries.

    Returns {"ok": true, "tasks": [...]} with per-task summaries
    including rejection_reasons, total_cycles, outcomes, timestamps,
    first_seen, last_seen.
    """
    journal_path = data.get("journal_path", "state/journal.jsonl")
    task_filter = data.get("task_filter")
    entries = _read_journal(journal_path)
    tasks = aggregate_by_task(entries, task_filter)
    return {"ok": True, "tasks": tasks}


def op_aggregate_cross_task(data):
    """Cross-task aggregation: group rejections by failure class across tasks.

    Returns {"ok": true, "classes": [...]} with per-class summaries
    including count, task_count, tasks, cycles, representative_reasons,
    first_seen, last_seen.

    Only classes appearing across >= min_tasks different tasks are
    returned, surfacing systemic patterns rather than task-specific ones.
    """
    journal_path = data.get("journal_path", "state/journal.jsonl")
    min_tasks = data.get("min_tasks", 2)
    entries = _read_journal(journal_path)
    classes = aggregate_cross_task(entries, min_tasks)
    return {"ok": True, "classes": classes}


def op_selfcheck(data):
    """Selfcheck: exercise all operations with tiny fixtures.

    Verifies:
    - rejection-reason collection with slot attribution
    - total_cycles/outcomes tracking
    - task-filter behavior
    - cross-task aggregation across two distinct tasks
    - fault exclusion (P-016)
    - case-insensitive task matching (G-007)
    - canary rejection tracking
    - _resolve_journal relative path resolution against REPO_ROOT
    """
    results = []
    failures = []

    try:
        # Build a tiny journal with two tasks sharing a failure class
        journal_entries = [
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "cycle",
             "cycle": 1, "outcome": "rejected", "why": "Task A",
             "rejected_by": [{"slot": "review:deepseek",
                              "reasons": ["closure budget exceeded"]}],
             "reason": "review rejected"},
            {"ts": "2026-01-02T00:00:00+00:00", "kind": "cycle",
             "cycle": 2, "outcome": "rejected", "why": "Task B",
             "rejected_by": [{"slot": "review:sensenova",
                              "reasons": ["closure budget too large"]}],
             "reason": "review rejected"},
            {"ts": "2026-01-03T00:00:00+00:00", "kind": "fault",
             "cycle": 3, "error": "timeout"},
            {"ts": "2026-01-04T00:00:00+00:00", "kind": "cycle",
             "cycle": 3, "outcome": "rejected", "why": "Task A",
             "rejected_by": [{"slot": "review:deepseek",
                              "reasons": ["closure budget exceeded"]}],
             "reason": "review rejected"},
            {"ts": "2026-01-05T00:00:00+00:00", "kind": "cycle",
             "cycle": 4, "outcome": "rejected", "why": "Task A",
             "rejected_by": [{"slot": "review:deepseek",
                              "reasons": ["gate weakening: removed a check"]}],
             "reason": "review rejected"},
            {"ts": "2026-01-06T00:00:00+00:00", "kind": "canary_reject",
             "why": "Task A", "reason": "canary boot failed"},
        ]

        fd, tmp_path = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w") as f:
                for entry in journal_entries:
                    f.write(json.dumps(entry) + "\n")

            # Test 1: query op returns correct counts
            q = op_query({"op": "query", "journal_path": tmp_path,
                          "task": "Task A"})
            if not q.get("ok"):
                failures.append(f"query op failed: {q.get('error')}")
            elif q["rejections"] != 2:
                failures.append(
                    f"query rejections: expected 2, got {q['rejections']}")
            elif q["faults"] != 1:
                failures.append(
                    f"query faults: expected 1, got {q['faults']}")
            elif q["canary_rejects"] != 1:
                failures.append(
                    f"query canary_rejects: expected 1, "
                    f"got {q['canary_rejects']}")
            else:
                results.append(
                    "query: counts correct (P-016 fault exclusion, "
                    "canary tracking)")

            # Test 2: case-insensitive task matching (G-007)
            ql = op_query({"op": "query", "journal_path": tmp_path,
                           "task": "task a"})
            if not ql.get("ok") or ql.get("rejections") != 2:
                failures.append(
                    f"case-insensitive matching failed: "
                    f"expected 2, got {ql.get('rejections')}")
            else:
                results.append("query: case-insensitive matching (G-007)")

            # Test 3: aggregate_by_task returns per-task summaries
            agg = op_aggregate_by_task(
                {"op": "aggregate_by_task", "journal_path": tmp_path})
            if not agg.get("ok"):
                failures.append(
                    f"aggregate_by_task failed: {agg.get('error')}")
            else:
                tasks = agg.get("tasks", [])
                ta = next((t for t in tasks if t["task"] == "Task A"),
                          None)
                if ta is None:
                    failures.append("aggregate_by_task: Task A not found")
                else:
                    if ta["rejections"] != 2:
                        failures.append(
                            f"aggregate_by_task rejections: expected 2, "
                            f"got {ta['rejections']}")
                    if ta["total_cycles"] != 3:
                        failures.append(
                            f"aggregate_by_task total_cycles: expected 3, "
                            f"got {ta['total_cycles']}")
                    if not ta["rejection_reasons"]:
                        failures.append(
                            "aggregate_by_task: no rejection_reasons "
                            "collected")
                    elif "review:deepseek" not in \
                            ta["rejection_reasons"][0]:
                        failures.append(
                            "aggregate_by_task: rejection_reasons "
                            "missing slot attribution")
                    if "rejected" not in ta["outcomes"]:
                        failures.append(
                            "aggregate_by_task: outcomes missing "
                            "'rejected'")
                    if not ta["first_seen"] or not ta["last_seen"]:
                        failures.append(
                            "aggregate_by_task: missing timestamps")
                    results.append(
                        "aggregate_by_task: per-task summaries with "
                        "slot attribution, outcomes, timestamps")

            # Test 4: task_filter behavior
            ft = op_aggregate_by_task(
                {"op": "aggregate_by_task", "journal_path": tmp_path,
                 "task_filter": "Task B"})
            filtered = ft.get("tasks", [])
            if len(filtered) != 1:
                failures.append(
                    f"aggregate_by_task filter: expected 1 task, "
                    f"got {len(filtered)}")
            elif filtered[0]["task"] != "Task B":
                failures.append(
                    "aggregate_by_task filter: wrong task returned")
            else:
                results.append("aggregate_by_task: task_filter behavior")

            # Test 5: aggregate_cross_task across two distinct tasks
            ct = op_aggregate_cross_task(
                {"op": "aggregate_cross_task",
                 "journal_path": tmp_path, "min_tasks": 2})
            if not ct.get("ok"):
                failures.append(
                    f"aggregate_cross_task failed: {ct.get('error')}")
            else:
                classes = ct.get("classes", [])
                closure = next((c for c in classes
                                if c["class"] == "closure-budget"),
                               None)
                if closure is None:
                    failures.append(
                        "aggregate_cross_task: closure-budget class "
                        "not found across tasks")
                else:
                    if closure["task_count"] != 2:
                        failures.append(
                            f"aggregate_cross_task: expected 2 tasks, "
                            f"got {closure['task_count']}")
                    if ("Task A" not in closure["tasks"] or
                            "Task B" not in closure["tasks"]):
                        failures.append(
                            "aggregate_cross_task: missing tasks in "
                            "closure-budget class")
                    if 3 in closure.get("cycles", []):
                        failures.append(
                            "aggregate_cross_task: faulted cycle 3 "
                            "should not be counted")
                    results.append(
                        "aggregate_cross_task: aggregates across two "
                        "distinct tasks with fault exclusion (P-016)")

                # gate-weakening should NOT appear (only 1 task)
                gate = next((c for c in classes
                             if c["class"] == "gate-weakening"), None)
                if gate is not None:
                    failures.append(
                        "aggregate_cross_task: gate-weakening should "
                        "not appear (only 1 task)")

            # Test 6: _resolve_journal resolves relative paths
            resolved = _resolve_journal("state/journal.jsonl")
            if not resolved.exists():
                failures.append(
                    f"_resolve_journal: relative path does not resolve "
                    f"to existing file: {resolved}")
            else:
                results.append(
                    "_resolve_journal: relative path resolution against "
                    "REPO_ROOT")

        finally:
            os.unlink(tmp_path)

    except Exception as exc:
        failures.append(
            f"selfcheck exception: {type(exc).__name__}: {exc}")

    return {
        "ok": len(failures) == 0,
        "results": results,
        "failures": failures,
    }


OPS = {
    "query": op_query,
    "aggregate_by_task": op_aggregate_by_task,
    "aggregate_cross_task": op_aggregate_cross_task,
    "selfcheck": op_selfcheck,
}


def main():
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps(
            {"ok": False, "error": f"invalid JSON input: {exc}"}))
        sys.exit(1)

    op = data.get("op", "")
    handler = OPS.get(op)
    if handler is None:
        print(json.dumps(
            {"ok": False, "error": f"unknown op: {op}"}))
        sys.exit(1)

    try:
        result = handler(data)
    except Exception as exc:
        print(json.dumps(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("ok", True) else 1)


if __name__ == "__main__":
    main()
