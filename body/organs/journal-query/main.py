#!/usr/bin/env python3
"""Journal-query organ: cross-cycle task aggregation.

Groups journal entries by task text and returns counts, rejection reasons,
and timestamps. This enables the circuit breaker to detect repeated
rejection patterns without the kernel carrying aggregation logic inline.

ABI: stdin JSON -> stdout JSON, exit 0 on success.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_JOURNAL = REPO_ROOT / "state" / "journal.jsonl"


def _resolve_journal(journal_path: str) -> pathlib.Path:
    """Resolve a journal path relative to the repo root, not the organ cwd.

    The organ runs with its own directory as cwd. A relative path resolves
    against that directory, not the repo root, so 'state/journal.jsonl'
    would point to a non-existent file. This is the same bug class that
    silenced the failure-aggregator (its journal path resolved to nowhere,
    so the breaker never tripped). Resolve against REPO_ROOT instead.
    """
    p = pathlib.Path(journal_path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    """Read a JSONL file, skipping blank and unparseable lines."""
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _faulted_cycles(rows: list[dict]) -> set:
    """Cycle numbers that ended in a fault rather than a verdict."""
    return {r.get("cycle") for r in rows if r.get("kind") == "fault"}


def aggregate_by_task(rows: list[dict], task_filter: str | None = None) -> list[dict]:
    """Group cycle entries by task text.

    Returns a list of per-task summaries, each with:
    - task: the task text
    - total_cycles: number of cycle entries for this task
    - outcomes: {outcome: count} for cycle entries
    - rejection_count: judged rejections (faults excluded)
    - fault_count: cycles where the mechanism failed before any verdict
    - canary_reject_count: canary rejections
    - rejection_reasons: [{cycle, reasons}] for each judged rejection and canary
    - timestamps: [ts] for each entry
    - first_seen, last_seen: boundary timestamps

    Faulted cycles are excluded from rejection_count: a fault is the mechanism
    failing (rate limit, unparseable reply), not a gate verdict. Counting them
    together would park a task the gates never objected to (P-016).

    Task matching is case-insensitive and whitespace-trimmed, so rejections
    for the same task with minor phrasing differences are still grouped (G-007).
    """
    faulted = _faulted_cycles(rows)
    tasks: dict[str, dict] = {}

    def _entry_for(why: str) -> dict:
        if why not in tasks:
            tasks[why] = {
                "task": why,
                "total_cycles": 0,
                "outcomes": {},
                "rejection_count": 0,
                "fault_count": 0,
                "canary_reject_count": 0,
                "rejection_reasons": [],
                "timestamps": [],
                "first_seen": None,
                "last_seen": None,
            }
        return tasks[why]

    def _matches(why: str) -> bool:
        if not task_filter:
            return True
        return why.strip().lower() == task_filter.strip().lower()

    for row in rows:
        kind = row.get("kind", "")
        why = row.get("why", "")
        if not why or not _matches(why):
            continue
        ts = row.get("ts", "")

        if kind == "cycle":
            entry = _entry_for(why)
            entry["total_cycles"] += 1
            outcome = row.get("outcome", "unknown")
            entry["outcomes"][outcome] = entry["outcomes"].get(outcome, 0) + 1
            cycle = row.get("cycle")

            if cycle in faulted:
                entry["fault_count"] += 1
            elif outcome == "rejected":
                entry["rejection_count"] += 1
                reasons: list[str] = []
                reason_text = row.get("reason", "")
                if reason_text and not reason_text.startswith("review rejected"):
                    reasons.append(reason_text[:500])
                for rej in row.get("rejected_by") or []:
                    slot = rej.get("slot", "unknown")
                    for r in rej.get("reasons") or []:
                        reasons.append(f"{slot}: {r[:500]}")
                if reasons:
                    entry["rejection_reasons"].append({
                        "cycle": cycle,
                        "reasons": reasons,
                    })

        elif kind == "canary_reject":
            entry = _entry_for(why)
            entry["canary_reject_count"] += 1
            entry["outcomes"]["canary_reject"] = entry["outcomes"].get("canary_reject", 0) + 1
            reason_text = row.get("reason", "")
            if reason_text:
                entry["rejection_reasons"].append({
                    "cycle": "canary",
                    "reasons": [reason_text[:500]],
                })

        if ts:
            entry = _entry_for(why)
            entry["timestamps"].append(ts)
            if entry["first_seen"] is None or ts < entry["first_seen"]:
                entry["first_seen"] = ts
            if entry["last_seen"] is None or ts > entry["last_seen"]:
                entry["last_seen"] = ts

    return list(tasks.values())


def query_task(rows: list[dict], task: str) -> dict:
    """Basic query: rejection counts for a single task.

    Returns the same counts the circuit breaker uses (rejections, faults,
    canary rejects), delegated to the organ so the kernel stays thin.
    The breaker applies its own thresholds to these counts.
    """
    agg = aggregate_by_task(rows, task_filter=task)
    if not agg:
        return {
            "ok": True,
            "task": task,
            "rejections": 0,
            "faults": 0,
            "canary_rejects": 0,
        }
    t = agg[0]
    return {
        "ok": True,
        "task": t["task"],
        "rejections": t["rejection_count"],
        "faults": t["fault_count"],
        "canary_rejects": t["canary_reject_count"],
    }


def _selfcheck() -> dict:
    """Exercise aggregation with a known fixture.

    Verifies:
    - Multiple tasks are grouped separately
    - Faulted cycles are excluded from rejection_count (P-016)
    - Canary rejects are counted
    - Rejection reasons are collected with slot attribution
    - Timestamps and first/last_seen are tracked
    - Task filter works, including case-insensitive (G-007)
    - query_task returns correct counts
    """
    fixture = [
        {"kind": "cycle", "cycle": 1, "outcome": "rejected", "why": "Fix bug A",
         "ts": "2026-01-01T00:00:00+00:00", "reason": "gate weakening",
         "rejected_by": [{"slot": "review:deepseek", "reasons": ["removes a check"]}]},
        {"kind": "cycle", "cycle": 2, "outcome": "rejected", "why": "Fix bug A",
         "ts": "2026-01-02T00:00:00+00:00", "reason": "closure budget",
         "rejected_by": [{"slot": "review:sensenova", "reasons": ["too large"]}]},
        {"kind": "fault", "cycle": 3, "why": "Fix bug A",
         "ts": "2026-01-03T00:00:00+00:00", "error": "rate limit"},
        {"kind": "cycle", "cycle": 3, "outcome": "rejected", "why": "Fix bug A",
         "ts": "2026-01-03T00:00:00+00:00", "reason": "",
         "rejected_by": [{"slot": "review:deepseek", "reasons": ["unreachable"]}]},
        {"kind": "cycle", "cycle": 4, "outcome": "candidate", "why": "Fix bug A",
         "ts": "2026-01-04T00:00:00+00:00"},
        {"kind": "canary_reject", "why": "Fix bug A",
         "ts": "2026-01-04T12:00:00+00:00", "reason": "boot failure"},
        {"kind": "cycle", "cycle": 1, "outcome": "rejected", "why": "Fix bug B",
         "ts": "2026-01-01T00:00:00+00:00", "reason": "gate weakening",
         "rejected_by": [{"slot": "review:deepseek", "reasons": ["removes a check"]}]},
    ]

    result = aggregate_by_task(fixture)
    checks: list[tuple[str, bool]] = []

    checks.append(("two_tasks", len(result) == 2))

    task_a = next((t for t in result if t["task"] == "Fix bug A"), None)
    checks.append(("task_a_found", task_a is not None))
    if task_a:
        checks.append(("task_a_cycles", task_a["total_cycles"] == 4))
        checks.append(("task_a_rejections", task_a["rejection_count"] == 2))
        checks.append(("task_a_faults", task_a["fault_count"] == 1))
        checks.append(("task_a_canary", task_a["canary_reject_count"] == 1))
        checks.append(("task_a_candidate", task_a["outcomes"].get("candidate") == 1))
        checks.append(("task_a_reasons", len(task_a["rejection_reasons"]) >= 2))
        checks.append(("task_a_timestamps", len(task_a["timestamps"]) >= 4))
        checks.append(("task_a_first", task_a["first_seen"] == "2026-01-01T00:00:00+00:00"))
        checks.append(("task_a_last", task_a["last_seen"] == "2026-01-04T12:00:00+00:00"))

    task_b = next((t for t in result if t["task"] == "Fix bug B"), None)
    checks.append(("task_b_found", task_b is not None))
    if task_b:
        checks.append(("task_b_rejections", task_b["rejection_count"] == 1))

    filtered = aggregate_by_task(fixture, task_filter="Fix bug A")
    checks.append(("filter_count", len(filtered) == 1))

    filtered_ci = aggregate_by_task(fixture, task_filter="fix bug a")
    checks.append(("filter_ci", len(filtered_ci) == 1))

    qr = query_task(fixture, "Fix bug A")
    checks.append(("query_rejections", qr["rejections"] == 2))
    checks.append(("query_faults", qr["faults"] == 1))
    checks.append(("query_canary", qr["canary_rejects"] == 1))

    failures = [name for name, ok in checks if not ok]
    return {"ok": not failures, "checks": checks, "failures": failures}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON input: {exc}"}))
        return 1

    op = payload.get("op", "")
    journal_path = payload.get("journal_path") or str(DEFAULT_JOURNAL)

    if op == "aggregate_by_task":
        path = _resolve_journal(journal_path)
        rows = _read_jsonl(path)
        task_filter = payload.get("task")
        result = aggregate_by_task(rows, task_filter)
        print(json.dumps({"ok": True, "tasks": result}))
        return 0

    if op == "query":
        path = _resolve_journal(journal_path)
        rows = _read_jsonl(path)
        task = payload.get("task", "")
        result = query_task(rows, task)
        print(json.dumps(result))
        return 0

    if op == "selfcheck":
        result = _selfcheck()
        print(json.dumps(result))
        return 0 if result["ok"] else 1

    print(json.dumps({"ok": False, "error": f"unknown op: {op}"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
