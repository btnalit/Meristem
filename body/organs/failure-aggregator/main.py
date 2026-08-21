#!/usr/bin/env python3
"""Failure aggregator organ.

Reads the journal, aggregates rejections per task, classifies failure
reasons into known classes, writes patterns to state/patterns.md when
a threshold is crossed, and emits graded signals (surface/escalate/block)
for the circuit breaker.

ABI: stdin JSON -> stdout JSON, exit 0 on success, non-zero on failure.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

# Resolve paths relative to the repository root, not cwd.
# The organ runs with its own directory as cwd, so a relative JOURNAL
# path would resolve to a non-existent file (cycle 206 bug).
_ORGAN_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _ORGAN_DIR.parents[2]  # body/organs/failure-aggregator -> repo root
_JOURNAL = _REPO_ROOT / "state" / "journal.jsonl"
_PATTERNS = _REPO_ROOT / "state" / "patterns.md"

# Pattern threshold: write to state/patterns.md when a task has >= 2 rejections.
# Raising this was a rejection in cycle 205.
PATTERN_THRESHOLD = 2

# Graded signal thresholds (cycle 205):
#   surface  at 2+ rejections
#   escalate at 3+ rejections
#   block    at 4+ rejections  -- Must NOT raise BLOCK_THRESHOLD above 4.
ESCALATE_THRESHOLD = 3
BLOCK_THRESHOLD = 4

# 12 failure classification classes. Reducing this count was a rejection
# in cycle 347 -- each class detects a specific failure mode that the organ
# is supposed to self-detect (Principle 2).
CLASSIFIERS = [
    ("gate-weakening", [
        "weakens gate", "weaken", "remove check", "narrow validation",
        "disable", "loosen", "shrink what", "gate weakening"]),
    ("protected-path", [
        "root/", "substrate/", "protected path", "root of trust"]),
    ("secret-leak", [
        "secret", "credential", "api key", "api_key", "token",
        "private key"]),
    ("immune-failure", [
        "immune", "fixture", "golden", "self-test", "immune failure"]),
    ("organ-manifest", [
        "organ.json", "manifest", "germline", "entrypoint",
        "organ manifest"]),
    ("budget-exceeded", [
        "kernel loc", "over cap", "line cap", "kernel is",
        "lines, over", "budget"]),
    ("closure-budget", [
        "closure", "review context", "50000", "50_000",
        "review closure", "closure budget"]),
    ("no-change", [
        "no effective change", "no change", "empty diff",
        "proposed nothing", "proposed no files"]),
    ("deterministic-check", [
        "deterministic", "vault reference", "append-only",
        "memory integrity", "vault-reference", "deterministic check"]),
    ("parse-error", [
        "parseable json", "unparseable", "jsondecodeerror",
        "malformed json", "no parseable", "parse error"]),
    ("review-rejected", [
        "review rejected", "quorum", "reviewer"]),
    ("probe-regression", [
        "probe", "regression", "score", "probe regression"]),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_journal(journal_path=None) -> list[dict]:
    """Read the JSONL journal. Resolves relative to repo root, not cwd."""
    path = pathlib.Path(journal_path) if journal_path else _JOURNAL
    if not path.exists():
        return []
    out = []
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
    Counting faults as rejections would park a task the gates never
    objected to (P-016)."""
    return {row.get("cycle") for row in rows if row.get("kind") == "fault"}


def _classify(reason: str) -> str:
    """Classify a rejection reason into a known failure class.
    Returns 'unclassified' when no class matches."""
    lowered = reason.lower()
    for class_name, keywords in CLASSIFIERS:
        for kw in keywords:
            if kw in lowered:
                return class_name
    return "unclassified"


def _classify_rejection(row: dict) -> str:
    """Classify a journal cycle row's rejection reasons.
    Scans both the 'reason' field and each rejected_by entry's reasons.
    Returns the dominant class across all reasons."""
    reasons: list[str] = []
    reason_text = row.get("reason", "")
    if reason_text and not reason_text.startswith("review rejected"):
        reasons.append(reason_text)
    for rej in row.get("rejected_by") or []:
        for r in rej.get("reasons") or []:
            reasons.append(r)
    class_counts: dict[str, int] = {}
    for r in reasons:
        cls = _classify(r)
        class_counts[cls] = class_counts.get(cls, 0) + 1
    if not class_counts:
        return "unclassified"
    return max(class_counts, key=class_counts.get)


def _aggregate(rows: list[dict]) -> list[dict]:
    """Aggregate rejections per task, classify, and emit graded signals.

    Counts canary rejects toward total rejections.
    Excludes faulted cycles from rejection counts (P-016).
    Pattern threshold is 2 (cycle 205).
    Block threshold is 4 (cycle 205).
    """
    faulted = _faulted_cycles(rows)

    task_rejections: dict[str, list[dict]] = {}
    task_classes: dict[str, dict[str, int]] = {}

    for row in rows:
        kind = row.get("kind", "")
        why = row.get("why", "")
        if not why:
            continue

        if (kind == "cycle"
                and row.get("outcome") == "rejected"
                and row.get("cycle") not in faulted):
            task_rejections.setdefault(why, []).append(row)
            cls = _classify_rejection(row)
            task_classes.setdefault(why, {})
            task_classes[why][cls] = task_classes[why].get(cls, 0) + 1

        elif kind == "canary_reject":
            task_rejections.setdefault(why, []).append(row)
            reason_text = row.get("reason", "")
            cls = _classify(reason_text) if reason_text else "unclassified"
            task_classes.setdefault(why, {})
            task_classes[why][cls] = task_classes[why].get(cls, 0) + 1

    signals = []
    for task, rejs in sorted(task_rejections.items()):
        count = len(rejs)
        if count < PATTERN_THRESHOLD:
            continue

        classes = task_classes.get(task, {})
        dominant_class = (max(classes, key=classes.get)
                          if classes else "unclassified")

        # Graded signal actions (cycle 205):
        # surface at 2+, escalate at 3+, block at 4+
        if count >= BLOCK_THRESHOLD:
            action = "block"
        elif count >= ESCALATE_THRESHOLD:
            action = "escalate"
        else:
            action = "surface"

        signals.append({
            "task": task,
            "class": dominant_class,
            "count": count,
            "action": action,
        })

    return signals


def _write_patterns(signals, rows, patterns_path=None):
    """Write pattern entries to state/patterns.md for tasks that crossed
    the pattern threshold. Returns list of pattern classes written.

    Dedup: does not write a pattern that already exists for the same
    task and class.
    """
    path = pathlib.Path(patterns_path) if patterns_path else _PATTERNS
    if not signals:
        return []

    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    entries_to_write = []
    written = []

    for s in signals:
        task = s.get("task", "")
        cls = s.get("class", "unclassified")
        count = s.get("count", 0)
        task_snippet = task[:80]

        # Dedup: skip if an entry for this task+class already exists.
        dedup_key = f"Task: {task_snippet}\nClass: {cls}"
        if dedup_key in existing_text:
            continue

        entry = (
            f"\n## FA-{cls} — Repeated rejection\n\n"
            f"Task: {task_snippet}\n"
            f"Class: {cls}\n"
            f"Rejected {count} times.\n"
        )
        entries_to_write.append((entry, cls))
        existing_text += entry  # prevent dupes within this batch

    if entries_to_write:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for entry, cls in entries_to_write:
                f.write(entry)
                written.append(cls)

    return written


def _append_organ_run(journal_path, cycle, outcome, patterns, error=""):
    """Append a kind='organ-run' journal entry recording the run outcome."""
    record = {
        "ts": _utc_now(),
        "kind": "organ-run",
        "cycle": cycle,
        "outcome": outcome,
        "patterns": patterns,
    }
    if error:
        record["error"] = error[:400]
    path = pathlib.Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def op_aggregate(payload):
    journal_path = payload.get("journal_path") or str(_JOURNAL)
    cycle = payload.get("cycle", 0)

    rows = _read_journal(journal_path)
    signals = _aggregate(rows)
    patterns_written = _write_patterns(signals, rows)

    # Record the run outcome in the journal (kind='organ-run')
    _append_organ_run(journal_path, cycle, "success", patterns_written)

    return {"ok": True, "signals": signals, "patterns": patterns_written}


def op_selfcheck(payload):
    """Exercise classification, aggregation, signal grading, dedup,
    canary counting, fault exclusion, and integration with real
    journal data. 21 tests covering all 12 classes plus structural
    invariants."""
    results = []
    failures = []

    # --- Classification tests: 12 classes + unclassified (13 tests) ---
    test_cases = [
        ("gate-weakening",
         "Does this change weaken any gate, check, or invariant?"),
        ("protected-path",
         "touches protected path 'root/panic.py' (root of trust / substrate)"),
        ("secret-leak",
         "possible secret: state/.fixture_secret.py matches sk- pattern"),
        ("immune-failure",
         "IMMUNE FAILURE: golden fixture passed the gates"),
        ("organ-manifest",
         "organ 'foo' has no readable organ.json"),
        ("budget-exceeded",
         "kernel is 3100 lines, over the 3000 cap"),
        ("closure-budget",
         "closure ~52000 > 50000 budget"),
        ("no-change",
         "engine produced no effective change"),
        ("deterministic-check",
         "vault-reference invariant: meristem/loop.py references the vault"),
        ("parse-error",
         "engine returned no parseable JSON object"),
        ("review-rejected",
         "review rejected (1/2 need 2)"),
        ("probe-regression",
         "regression on frozen probe 'probe-word-count-basic': "
         "100.00 -> 80.00"),
    ]
    for expected, reason in test_cases:
        actual = _classify(reason)
        if actual == expected:
            results.append(f"classify '{expected}': pass")
        else:
            failures.append(
                f"classify '{expected}': expected '{expected}', "
                f"got '{actual}'")
            results.append(f"classify '{expected}': FAIL")

    actual = _classify("some random unknown rejection reason xyz123")
    if actual == "unclassified":
        results.append("classify 'unclassified': pass")
    else:
        failures.append(
            f"classify 'unclassified': expected 'unclassified', "
            f"got '{actual}'")
        results.append("classify 'unclassified': FAIL")

    # --- Rejected_by scanning test (1 test) ---
    # Verifies that _classify_rejection scans rejected_by reasons,
    # not just the top-level reason field.
    row_with_rej = {
        "kind": "cycle", "outcome": "rejected", "cycle": 999,
        "why": "test task", "reason": "",
        "rejected_by": [
            {"slot": "review:deepseek", "weakens_gate": True,
             "reasons": ["Does this change weaken any gate? "
                         "Yes, it removes a check."]}
        ],
    }
    cls = _classify_rejection(row_with_rej)
    if cls == "gate-weakening":
        results.append("classify_rejection scans rejected_by: pass")
    else:
        failures.append(
            f"classify_rejection rejected_by scan: expected "
            f"'gate-weakening', got '{cls}'")
        results.append("classify_rejection scans rejected_by: FAIL")

    # --- Signal grading tests: surface, escalate, block (3 tests) ---
    base_rows = []
    for i in range(4):
        base_rows.append({
            "kind": "cycle", "outcome": "rejected",
            "cycle": 100 + i, "why": "test-signal-task",
            "reason": "",
            "rejected_by": [
                {"slot": "review:test",
                 "reasons": ["closure ~52000 > 50000 budget"]}],
        })

    sigs = _aggregate(base_rows[:2])
    if any(s["action"] == "surface" for s in sigs):
        results.append("signal surface at 2 rejections: pass")
    else:
        failures.append("signal surface at 2 rejections: not emitted")
        results.append("signal surface at 2 rejections: FAIL")

    sigs = _aggregate(base_rows[:3])
    if any(s["action"] == "escalate" for s in sigs):
        results.append("signal escalate at 3 rejections: pass")
    else:
        failures.append("signal escalate at 3 rejections: not emitted")
        results.append("signal escalate at 3 rejections: FAIL")

    sigs = _aggregate(base_rows[:4])
    if any(s["action"] == "block" for s in sigs):
        results.append("signal block at 4 rejections: pass")
    else:
        failures.append("signal block at 4 rejections: not emitted")
        results.append("signal block at 4 rejections: FAIL")

    # --- Canary reject counting test (1 test) ---
    canary_rows = [
        {"kind": "cycle", "outcome": "rejected", "cycle": 200,
         "why": "canary-task", "reason": "",
         "rejected_by": [{"slot": "r",
                          "reasons": ["closure budget"]}]},
        {"kind": "canary_reject", "why": "canary-task",
         "reason": "canary boot failed"},
    ]
    sigs = _aggregate(canary_rows)
    if sigs and sigs[0]["count"] >= 2:
        results.append("canary reject counting: pass")
    else:
        c = sigs[0]["count"] if sigs else 0
        failures.append(
            f"canary reject counting: expected count >= 2, got {c}")
        results.append("canary reject counting: FAIL")

    # --- Fault-cycle exclusion test (1 test) ---
    fault_rows = [
        {"kind": "fault", "cycle": 300},
        {"kind": "cycle", "outcome": "rejected", "cycle": 300,
         "why": "fault-task", "reason": "",
         "rejected_by": [{"slot": "r",
                          "reasons": ["gate weakening"]}]},
    ]
    sigs = _aggregate(fault_rows)
    if not sigs:
        results.append("fault-cycle exclusion: pass")
    else:
        failures.append(
            "fault-cycle exclusion: faulted cycle counted as rejection")
        results.append("fault-cycle exclusion: FAIL")

    # --- Integration test: aggregate with real journal data (1 test) ---
    real_rows = _read_journal()
    if real_rows:
        real_sigs = _aggregate(real_rows)
        results.append(
            f"integration aggregate on {len(real_rows)} journal rows: "
            f"pass ({len(real_sigs)} signals)")
    else:
        results.append(
            "integration aggregate: skipped (no journal rows)")

    # --- Dedup check test (1 test) ---
    import tempfile
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
    try:
        dedup_signals = [{
            "task": "dedup-test-task", "class": "test-class",
            "count": 2, "action": "surface",
        }]
        _write_patterns(dedup_signals, [], patterns_path=tmp_path)
        before = tmp_path.read_text(encoding="utf-8")
        _write_patterns(dedup_signals, [], patterns_path=tmp_path)
        after = tmp_path.read_text(encoding="utf-8")
        if before == after:
            results.append("dedup pattern check: pass")
        else:
            failures.append(
                "dedup pattern check: duplicate entry was written")
            results.append("dedup pattern check: FAIL")
    finally:
        tmp_path.unlink(missing_ok=True)

    ok = len(failures) == 0
    return {"ok": ok, "results": results, "failures": failures}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps(
            {"ok": False, "error": f"invalid JSON input: {exc}"}))
        return 1

    op = payload.get("op", "")

    try:
        if op == "aggregate":
            result = op_aggregate(payload)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        elif op == "selfcheck":
            result = op_selfcheck(payload)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1
        else:
            print(json.dumps(
                {"ok": False, "error": f"unknown op '{op}'"}))
            return 1
    except Exception as exc:
        # Record the failure in the journal before exiting.
        journal_path = payload.get("journal_path") or str(_JOURNAL)
        cycle = payload.get("cycle", 0)
        try:
            _append_organ_run(
                journal_path, cycle, "failure", [], str(exc))
        except Exception:
            pass
        print(json.dumps(
            {"ok": False,
             "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
