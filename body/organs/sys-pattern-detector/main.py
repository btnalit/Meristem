#!/usr/bin/env python3
"""Systemic pattern detector organ.

Aggregates rejection reasons ACROSS different tasks to identify systemic
failure classes. Complements the per-task failure-aggregator: that organ
asks 'why does THIS task keep failing?', while this one asks 'why do
DIFFERENT tasks keep failing the same way?'

When the same failure class (e.g., kernel-over-cap, closure-budget,
gate-weakening) appears across multiple distinct tasks, it is a systemic
issue -- not a task-specific one -- and deserves a SYS-NNN entry in
state/patterns.md.

ABI:
  stdin  : {"op": "aggregate"|"selfcheck", "journal_path": "...", "repo_path": "...", "cycle": N}
  stdout : {"ok": true, "patterns": [...], "written": [...]}  (aggregate)
           {"ok": true, "results": [...]}                      (selfcheck)
  exit 0 : success; anything else is a failure with stderr as the reason
"""
from __future__ import annotations

import json
import pathlib
import sys

# Ensure sibling imports work regardless of cwd. The loop calls organs via
# subprocess.run without setting cwd to the organ directory (the same bug
# class that broke the failure-aggregator's journal path resolution).
# Adding our directory to sys.path makes 'from classify import ...' work
# whether cwd is the repo root, the organ directory, or a worktree.
_ORGAN_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_ORGAN_DIR))

from classify import classify  # noqa: E402

# Resolve repo root from __file__:
#   body/organs/sys-pattern-detector/main.py
#   parents[0] = body/organs/
#   parents[1] = body/
#   parents[2] = repo root
_REPO_ROOT = _ORGAN_DIR.parents[2]
_DEFAULT_JOURNAL = _REPO_ROOT / "state" / "journal.jsonl"
_DEFAULT_PATTERNS = _REPO_ROOT / "state" / "patterns.md"

#: Minimum distinct tasks for a class to be considered systemic.
#: 1 task = per-task aggregation's job, not ours.
MIN_TASKS = 2


def _read_journal(journal_path: str) -> list[dict]:
    path = pathlib.Path(journal_path)
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


def _extract_rejections(rows: list[dict]) -> list[dict]:
    """Extract rejected cycles with their reasons, excluding faults.

    A fault is not a rejection: the mechanism failed before any verdict.
    Counting faults as rejections would conflate 'the gates refused this'
    with 'the system broke' -- the same lesson as breaker.py (P-016).
    """
    faulted = {r.get("cycle") for r in rows if r.get("kind") == "fault"}
    rejections: list[dict] = []
    for row in rows:
        if row.get("kind") != "cycle":
            continue
        if row.get("outcome") != "rejected":
            continue
        if row.get("cycle") in faulted:
            continue
        task = row.get("why", "")
        cycle = row.get("cycle", 0)
        # Gather reasons from both the top-level reason and reviewer reasons.
        reasons: list[str] = []
        reason_text = row.get("reason", "")
        if reason_text:
            reasons.append(reason_text)
        for rej in row.get("rejected_by") or []:
            for r in rej.get("reasons") or []:
                reasons.append(r)
        rejections.append({
            "task": task,
            "cycle": cycle,
            "reasons": reasons,
        })
    return rejections


def detect_patterns(rejections: list[dict]) -> list[dict]:
    """Detect systemic patterns: same failure class across multiple tasks.

    Returns a list of pattern dicts sorted by class name. Each pattern
    includes the class, the distinct tasks that hit it, the cycles, the
    total rejection count, and a representative reason (shortest match,
    which is usually the most precise).
    """
    # Map: class -> {task -> {cycles, reasons}}
    by_class: dict[str, dict[str, dict]] = {}
    for rej in rejections:
        for reason in rej["reasons"]:
            cls = classify(reason)
            if cls == "unclassified":
                continue
            slot = by_class.setdefault(cls, {})
            task_entry = slot.setdefault(rej["task"], {"cycles": [], "reasons": []})
            task_entry["cycles"].append(rej["cycle"])
            task_entry["reasons"].append(reason)

    patterns: list[dict] = []
    for cls, tasks in sorted(by_class.items()):
        if len(tasks) < MIN_TASKS:
            continue
        all_cycles = sorted(set(c for t in tasks.values() for c in t["cycles"]))
        all_reasons = [r for t in tasks.values() for r in t["reasons"]]
        # Shortest reason is usually the most precise (deterministic gate
        # messages are terse; reviewer prose is long).
        representative = min(all_reasons, key=len) if all_reasons else ""
        patterns.append({
            "class": cls,
            "tasks": sorted(tasks.keys()),
            "task_count": len(tasks),
            "cycles": all_cycles,
            "count": len(all_reasons),
            "representative_reason": representative[:300],
        })
    return patterns


def _existing_sys_classes(patterns_path: pathlib.Path) -> set[str]:
    """Read existing SYS-NNN entries from patterns.md to avoid duplicates.

    Parses headings of the form '## SYS-001 — kernel-over-cap' and returns
    the set of class names already recorded (lowercased).
    """
    if not patterns_path.exists():
        return set()
    text = patterns_path.read_text(encoding="utf-8")
    classes: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("## SYS-"):
            continue
        # Skip "## SYS-" (7 chars), then digits, then separator chars.
        rest = line[7:]
        i = 0
        while i < len(rest) and rest[i].isdigit():
            i += 1
        while i < len(rest) and not rest[i].isalnum():
            i += 1
        class_name = rest[i:].strip()
        if class_name:
            classes.add(class_name.lower())
    return classes


def _max_sys_num(patterns_path: pathlib.Path) -> int:
    """Find the highest SYS-NNN number in patterns.md."""
    if not patterns_path.exists():
        return 0
    text = patterns_path.read_text(encoding="utf-8")
    max_num = 0
    for line in text.splitlines():
        if not line.startswith("## SYS-"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                num = int(parts[1].split("-")[1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                continue
    return max_num


def write_patterns(patterns: list[dict], patterns_path: pathlib.Path) -> list[str]:
    """Append new SYS-NNN entries to patterns.md for patterns not already recorded.

    Idempotent: if a SYS entry for the same class already exists, it is
    not duplicated. Returns the list of SYS-NNN ids written.
    """
    if not patterns:
        return []
    existing = _existing_sys_classes(patterns_path)
    next_num = _max_sys_num(patterns_path)

    written: list[str] = []
    lines_to_append: list[str] = []
    for pat in patterns:
        cls_key = pat["class"].lower()
        if cls_key in existing:
            continue
        next_num += 1
        sys_id = f"SYS-{next_num:03d}"
        existing.add(cls_key)

        task_list = ", ".join(pat["tasks"][:5])
        if pat["task_count"] > 5:
            task_list += f", ... ({pat['task_count']} total)"
        cycle_range = (
            f"{pat['cycles'][0]}\u2013{pat['cycles'][-1]}"
            if pat["cycles"]
            else "?"
        )

        lines_to_append.append(f"\n## {sys_id} \u2014 {pat['class']}\n")
        lines_to_append.append(
            f"**Systemic pattern detected across {pat['task_count']} tasks**\n\n"
        )
        lines_to_append.append(f"- Tasks: {task_list}\n")
        lines_to_append.append(
            f"- Cycles: {cycle_range} ({len(pat['cycles'])} cycles)\n"
        )
        lines_to_append.append(f"- Rejections: {pat['count']}\n")
        lines_to_append.append(
            f"- Representative reason: {pat['representative_reason']}\n"
        )
        lines_to_append.append("- Detected by: sys-pattern-detector organ\n")
        written.append(sys_id)

    if lines_to_append:
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        if not patterns_path.exists():
            patterns_path.write_text("# Pattern Register\n\n", encoding="utf-8")
        with patterns_path.open("a", encoding="utf-8") as f:
            f.writelines(lines_to_append)

    return written


def op_aggregate(
    journal_path: str,
    repo_path: str | None = None,
    cycle: int = 0,
) -> dict:
    """Read journal, detect cross-task patterns, write new SYS-NNN entries."""
    rows = _read_journal(journal_path)
    rejections = _extract_rejections(rows)
    patterns = detect_patterns(rejections)

    if repo_path:
        patterns_file = pathlib.Path(repo_path) / "state" / "patterns.md"
    else:
        patterns_file = _DEFAULT_PATTERNS
    written = write_patterns(patterns, patterns_file)

    return {
        "ok": True,
        "patterns": patterns,
        "written": written,
        "cycle": cycle,
        "rejection_count": len(rejections),
    }


def op_selfcheck() -> dict:
    """Exercise classification and detection with known fixtures.

    The measuring stick: correctly identifies 'kernel over 3000 cap' across
    tasks G-002 and G-001 (cycles 172-185).
    """
    results: list[str] = []
    failures: list[str] = []

    # 1. Test classify() with known rejection reasons.
    test_cases = [
        ("deterministic: kernel is 3005 lines, over the 3000 cap", "kernel-over-cap"),
        ("kernel is 3012 lines, over the 3000 cap", "kernel-over-cap"),
        ("closure ~52000 > 50000 budget", "closure-budget"),
        ("review rejected: gate weakening detected", "gate-weakening"),
        ("429 rate limit from API", "rate-limit"),
        ("probe regression on probe-kernel-selftest", "probe-regression"),
        ("touches protected path 'root/panic.py'", "protected-path"),
        ("possible secret: sk-abc123def456", "secret-leak"),
        ("unrelated error message about formatting", "unclassified"),
    ]
    for reason, expected in test_cases:
        actual = classify(reason)
        if actual == expected:
            results.append(f"classify: '{reason[:50]}' -> {actual} OK")
        else:
            failures.append(
                f"classify: '{reason[:50]}' -> {actual}, expected {expected}"
            )
            results.append(f"classify: '{reason[:50]}' -> {actual} FAIL")

    # 2. Test detect_patterns() with synthetic journal data matching the
    #    measuring stick: kernel-over-cap across G-002 and G-001.
    synthetic_rejections = [
        {
            "task": "GROWTH (G-002): Add syscall-level dependency observation",
            "cycle": 172,
            "reasons": [
                "deterministic: kernel is 3005 lines, over the 3000 cap"
            ],
        },
        {
            "task": "GROWTH (G-002): Add syscall-level dependency observation",
            "cycle": 175,
            "reasons": [
                "deterministic: kernel is 3008 lines, over the 3000 cap"
            ],
        },
        {
            "task": "GROWTH (G-001): Implement a mailbox acknowledgment protocol",
            "cycle": 180,
            "reasons": [
                "kernel is 3012 lines, over the 3000 cap"
            ],
        },
        {
            "task": "GROWTH (G-001): Implement a mailbox acknowledgment protocol",
            "cycle": 185,
            "reasons": [
                "deterministic: kernel is 3010 lines, over the 3000 cap"
            ],
        },
        # A different task with a different failure class -- only 1 task,
        # so it should NOT produce a systemic pattern.
        {
            "task": "REPAIR: Fix the reporter formatting",
            "cycle": 190,
            "reasons": ["closure ~55000 > 50000 budget"],
        },
    ]
    patterns = detect_patterns(synthetic_rejections)

    # Assert kernel-over-cap is detected across both G-002 and G-001.
    koc_patterns = [p for p in patterns if p["class"] == "kernel-over-cap"]
    if not koc_patterns:
        failures.append("detect_patterns: kernel-over-cap not detected")
        results.append("detect_patterns: kernel-over-cap not detected FAIL")
    else:
        koc = koc_patterns[0]
        if koc["task_count"] >= 2:
            results.append(
                f"detect_patterns: kernel-over-cap across "
                f"{koc['task_count']} tasks OK"
            )
        else:
            failures.append(
                f"detect_patterns: kernel-over-cap only "
                f"{koc['task_count']} task(s)"
            )
            results.append(
                f"detect_patterns: kernel-over-cap only "
                f"{koc['task_count']} task(s) FAIL"
            )

        # Check both G-002 and G-001 are in the tasks.
        task_text = " ".join(koc["tasks"])
        if "G-002" in task_text and "G-001" in task_text:
            results.append("detect_patterns: both G-002 and G-001 present OK")
        else:
            failures.append(
                f"detect_patterns: missing G-002 or G-001 in tasks: "
                f"{koc['tasks']}"
            )
            results.append(
                "detect_patterns: missing G-002 or G-001 FAIL"
            )

        # Check cycles span the 172-185 range.
        if koc["cycles"][0] <= 172 and koc["cycles"][-1] >= 185:
            results.append(
                f"detect_patterns: cycles span "
                f"{koc['cycles'][0]}-{koc['cycles'][-1]} OK"
            )
        else:
            failures.append(
                f"detect_patterns: cycles {koc['cycles']} "
                f"don't span 172-185"
            )
            results.append(
                "detect_patterns: cycles don't span 172-185 FAIL"
            )

    # 3. Test that closure-budget is NOT detected as systemic (only 1 task).
    cb_patterns = [p for p in patterns if p["class"] == "closure-budget"]
    if not cb_patterns:
        results.append(
            "detect_patterns: closure-budget correctly not systemic "
            "(1 task) OK"
        )
    else:
        failures.append(
            "detect_patterns: closure-budget should not be systemic "
            "with 1 task"
        )
        results.append(
            "detect_patterns: closure-budget falsely systemic FAIL"
        )

    # 4. Test write_patterns() with a temp file.
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(
            "# Pattern Register\n\n## P-001 \u2014 existing entry\n\n"
            "Some text.\n"
        )
        tmp_path = pathlib.Path(tmp.name)
    try:
        written = write_patterns(patterns, tmp_path)
        if written:
            content = tmp_path.read_text(encoding="utf-8")
            if "## SYS-" in content and "kernel-over-cap" in content:
                results.append(f"write_patterns: wrote {written} OK")
            else:
                failures.append(
                    "write_patterns: SYS entry not found in output"
                )
                results.append("write_patterns: SYS entry missing FAIL")
        else:
            failures.append("write_patterns: nothing written")
            results.append("write_patterns: nothing written FAIL")

        # Test idempotency: running again should not duplicate.
        written2 = write_patterns(patterns, tmp_path)
        if not written2:
            results.append("write_patterns: idempotent (no duplicates) OK")
        else:
            failures.append(f"write_patterns: wrote duplicates {written2}")
            results.append(f"write_patterns: duplicates {written2} FAIL")
    finally:
        tmp_path.unlink(missing_ok=True)

    # 5. Test full aggregate pipeline with temp journal and temp patterns.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_journal = pathlib.Path(tmpdir) / "journal.jsonl"
        with tmp_journal.open("w", encoding="utf-8") as f:
            for rej in synthetic_rejections:
                row = {
                    "kind": "cycle",
                    "cycle": rej["cycle"],
                    "outcome": "rejected",
                    "why": rej["task"],
                    "reason": rej["reasons"][0],
                    "rejected_by": [],
                }
                f.write(json.dumps(row) + "\n")
            # Add a fault row to ensure faults are excluded.
            f.write(
                json.dumps({"kind": "fault", "cycle": 173}) + "\n"
            )
            # Add a non-rejected cycle to ensure it is excluded.
            f.write(
                json.dumps({
                    "kind": "cycle",
                    "cycle": 174,
                    "outcome": "candidate",
                    "why": "other",
                }) + "\n"
            )

        result = op_aggregate(
            journal_path=str(tmp_journal),
            repo_path=tmpdir,
            cycle=200,
        )
        if result["ok"] and result["patterns"]:
            koc = [
                p for p in result["patterns"]
                if p["class"] == "kernel-over-cap"
            ]
            if koc and koc[0]["task_count"] >= 2:
                results.append(
                    "aggregate: full pipeline detects kernel-over-cap OK"
                )
            else:
                failures.append(
                    "aggregate: full pipeline missed kernel-over-cap"
                )
                results.append(
                    "aggregate: full pipeline missed kernel-over-cap FAIL"
                )
        else:
            failures.append(
                "aggregate: no patterns detected in full pipeline"
            )
            results.append(
                "aggregate: no patterns detected FAIL"
            )

        # Check that patterns.md was written in the temp dir.
        tmp_patterns = pathlib.Path(tmpdir) / "state" / "patterns.md"
        if tmp_patterns.exists() and "SYS-" in tmp_patterns.read_text(
            encoding="utf-8"
        ):
            results.append(
                "aggregate: wrote SYS entries to patterns.md OK"
            )
        else:
            failures.append("aggregate: no SYS entries written")
            results.append("aggregate: no SYS entries written FAIL")

    return {
        "ok": len(failures) == 0,
        "results": results,
        "failures": failures,
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON: {exc}"}))
        return 1

    op = payload.get("op", "")
    try:
        if op == "aggregate":
            result = op_aggregate(
                journal_path=payload.get(
                    "journal_path", str(_DEFAULT_JOURNAL)
                ),
                repo_path=payload.get("repo_path"),
                cycle=payload.get("cycle", 0),
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        elif op == "selfcheck":
            result = op_selfcheck()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 1
        else:
            print(
                json.dumps({"ok": False, "error": f"unknown op '{op}'"})
            )
            return 1
    except Exception as exc:
        print(
            json.dumps({
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
