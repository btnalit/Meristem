#!/usr/bin/env python3
"""Pattern detector organ: reads the journal, clusters rejections by failure
class, and appends new P-NNN entries to state/patterns.md.

This is the loop's own failure-class detection -- the mechanism by which
Meristem begins to find its own failure classes before anyone else does
(Principle 2: "Detecting one's own failure classes is a duty").

Unlike the failure-aggregator, which groups rejections by TASK text, this
organ groups them by FAILURE CLASS -- the P-NNN level of abstraction. A
class like "kernel-loc-cap-exceeded" may manifest across many different
tasks; detecting the class rather than the instance is what makes this
Loop B (growth) rather than Loop A (optimisation).

The organ is deterministic: no model calls. Classification is keyword-based,
matching rejection reasons against known failure-class signatures. This is
deliberate -- a model call to classify failures would be expensive, slow,
and itself subject to the failure modes this organ exists to detect.
"""

from __future__ import annotations

import json
import re
import sys
import pathlib

# Ensure the organ's own directory is on sys.path so `from classify import ...`
# works regardless of the caller's cwd.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from classify import classify_rejection, FAILURE_CLASSES


def read_journal(journal_path):
    """Read JSONL journal entries."""
    path = pathlib.Path(journal_path)
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


def extract_rejections(entries):
    """Extract rejection and fault records from the journal.

    A rejection is a cycle record with outcome='rejected'. We extract both
    the overall reason and individual reviewer reasons. We also extract
    fault records (mechanism failures) and canary rejections, since these
    are also failure classes worth detecting.
    """
    rejections = []
    for row in entries:
        kind = row.get("kind", "")

        if kind == "cycle" and row.get("outcome") == "rejected":
            task = row.get("why", "")
            cycle = row.get("cycle", 0)
            reason = row.get("reason", "")
            if reason:
                rejections.append({
                    "task": task, "cycle": cycle,
                    "reason": reason, "source": "overall",
                })
            for rej in row.get("rejected_by") or []:
                slot = rej.get("slot", "unknown")
                for r in rej.get("reasons") or []:
                    rejections.append({
                        "task": task, "cycle": cycle,
                        "reason": f"{slot}: {r}", "source": "reviewer",
                    })

        elif kind == "canary_reject":
            task = row.get("why", "")
            reason = row.get("reason", "")
            if reason:
                rejections.append({
                    "task": task, "cycle": row.get("cycle", 0),
                    "reason": reason, "source": "canary",
                })

        elif kind == "fault":
            task = row.get("task", "")
            error = row.get("error", "")
            if error:
                rejections.append({
                    "task": task, "cycle": row.get("cycle", 0),
                    "reason": error, "source": "fault",
                })

    return rejections


def cluster_by_class(rejections):
    """Group rejections by failure class.

    Returns a dict: class_name -> {class, description, count, tasks, cycles,
    representative_reason}.
    """
    clusters = {}
    for rej in rejections:
        cls = classify_rejection(rej["reason"])
        if cls is None:
            continue
        if cls not in clusters:
            clusters[cls] = {
                "class": cls,
                "description": FAILURE_CLASSES[cls]["description"],
                "count": 0,
                "tasks": set(),
                "cycles": [],
                "representative_reason": rej["reason"][:300],
            }
        clusters[cls]["count"] += 1
        clusters[cls]["tasks"].add(rej["task"])
        clusters[cls]["cycles"].append(rej["cycle"])
    return clusters


def read_existing_classes(patterns_path):
    """Read patterns.md and extract existing failure class tags.

    Entries written by this organ include a machine-readable tag:
        **Class:** <class-name>

    Returns a set of class names already recorded.
    """
    path = pathlib.Path(patterns_path)
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"\*\*Class:\*\*\s*(\S+)", text))


def next_pattern_id(patterns_path):
    """Find the next available P-NNN number."""
    path = pathlib.Path(patterns_path)
    if not path.exists():
        return 1
    text = path.read_text(encoding="utf-8")
    ids = re.findall(r"^##\s+P-(\d+)", text, re.M)
    if not ids:
        return 1
    return max(int(n) for n in ids) + 1


def format_pattern_entry(pid, cluster, cycle):
    """Format a P-NNN entry for patterns.md."""
    tasks = sorted(cluster["tasks"])
    cycles = sorted(set(cluster["cycles"]))
    return (
        f"\n## {pid} \u2014 {cluster['description']}\n\n"
        f"**Class:** {cluster['class']}\n"
        f"**Detected by:** pattern-detector organ (cycle {cycle})\n"
        f"**Count:** {cluster['count']} rejection(s) across "
        f"{len(tasks)} task(s)\n"
        f"**Tasks:** {', '.join(t[:80] for t in tasks[:5])}"
        + (" ..." if len(tasks) > 5 else "") + "\n"
        f"**Cycles:** {', '.join(str(c) for c in cycles[:10])}"
        + (" ..." if len(cycles) > 10 else "") + "\n"
        f"**Representative reason:** {cluster['representative_reason']}\n\n"
        f"This failure class was detected by the pattern-detector organ, "
        f"which clusters rejection reasons across tasks to identify "
        f"recurring structural failure modes. The class "
        f"'{cluster['class']}' appeared {cluster['count']} time(s) "
        f"across {len(tasks)} different task(s), indicating a structural "
        f"pattern rather than an isolated instance.\n"
    )


MIN_COUNT = 2  # Minimum rejections of a class before writing a pattern.


def detect(journal_path, repo_path, cycle, patterns_path=None):
    """Main detection logic: read journal, cluster, append new patterns."""
    if patterns_path is None:
        patterns_path = str(pathlib.Path(repo_path) / "state" / "patterns.md")

    entries = read_journal(journal_path)
    rejections = extract_rejections(entries)
    clusters = cluster_by_class(rejections)
    existing = read_existing_classes(patterns_path)

    new_clusters = []
    skipped_known = []
    skipped_low = []
    for cls in sorted(clusters):
        cluster = clusters[cls]
        if cls in existing:
            skipped_known.append(cls)
        elif cluster["count"] < MIN_COUNT:
            skipped_low.append(cls)
        else:
            new_clusters.append(cluster)

    patterns_written = []
    if new_clusters:
        next_num = next_pattern_id(patterns_path)
        lines = []
        for i, cluster in enumerate(new_clusters):
            pid = f"P-{next_num + i:03d}"
            lines.append(format_pattern_entry(pid, cluster, cycle))
            patterns_written.append(pid)

        path = pathlib.Path(patterns_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    return {
        "ok": True,
        "patterns_written": patterns_written,
        "classes_detected": [
            {
                "class": c["class"],
                "count": c["count"],
                "tasks": sorted(c["tasks"]),
            }
            for c in sorted(clusters.values(), key=lambda x: -x["count"])
        ],
        "skipped_known": skipped_known,
        "skipped_low_count": skipped_low,
    }


def selfcheck():
    """Exercise classification and detection with known fixtures."""
    results = []
    failures = []

    # --- Classification tests ---
    fixtures = [
        ("deterministic: kernel is 3016 lines, over the 3000 cap",
         "kernel-loc-cap-exceeded"),
        ("deterministic: closure ~52000 > 50000 budget",
         "closure-budget-exceeded"),
        ("deterministic: touches protected path 'root/panic.py'",
         "protected-path-violation"),
        ("review rejected (1/2 (need 2))",
         "review-rejection"),
        ("HTTP Error 429: Too Many Requests",
         "engine-fault-rate-limit"),
        ("engine returned no parseable JSON object",
         "engine-fault-parse-error"),
        ("something completely unprecedented",
         None),
    ]
    for reason, expected in fixtures:
        got = classify_rejection(reason)
        if got == expected:
            results.append(f"classify '{expected}': pass")
        else:
            failures.append(
                f"classify '{expected}': got '{got}' for reason '{reason[:60]}'"
            )

    # --- Detection: empty journal ---
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        journal = pathlib.Path(tmp) / "journal.jsonl"
        journal.write_text("", encoding="utf-8")
        patterns = pathlib.Path(tmp) / "patterns.md"
        patterns.write_text(
            "## P-001 \u2014 existing\n\n**Class:** existing-class\n",
            encoding="utf-8",
        )
        result = detect(str(journal), tmp, 0, str(patterns))
        if result["ok"] and result["patterns_written"] == []:
            results.append("detect empty journal: pass")
        else:
            failures.append("detect empty journal: failed")

    # --- Detection: new class with enough instances ---
    with tempfile.TemporaryDirectory() as tmp:
        journal = pathlib.Path(tmp) / "journal.jsonl"
        entries = [
            {"kind": "cycle", "cycle": 1, "outcome": "rejected",
             "why": "task A",
             "reason": "deterministic: kernel is 3016 lines, over the 3000 cap",
             "rejected_by": []},
            {"kind": "cycle", "cycle": 2, "outcome": "rejected",
             "why": "task B",
             "reason": "deterministic: kernel is 3050 lines, over the 3000 cap",
             "rejected_by": []},
        ]
        with journal.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        patterns = pathlib.Path(tmp) / "patterns.md"
        patterns.write_text(
            "## P-001 \u2014 existing\n\n**Class:** existing-class\n",
            encoding="utf-8",
        )
        result = detect(str(journal), tmp, 5, str(patterns))
        if result["ok"] and len(result["patterns_written"]) == 1:
            results.append("detect new class: pass")
        else:
            failures.append(f"detect new class: got {result}")
        text = patterns.read_text(encoding="utf-8")
        if "kernel-loc-cap-exceeded" in text:
            results.append("pattern appended: pass")
        else:
            failures.append("pattern not appended to file")

    # --- Detection: skips already-known class ---
    with tempfile.TemporaryDirectory() as tmp:
        journal = pathlib.Path(tmp) / "journal.jsonl"
        entries = [
            {"kind": "cycle", "cycle": 1, "outcome": "rejected",
             "why": "task A",
             "reason": "deterministic: kernel is 3016 lines, over the 3000 cap",
             "rejected_by": []},
            {"kind": "cycle", "cycle": 2, "outcome": "rejected",
             "why": "task B",
             "reason": "deterministic: kernel is 3050 lines, over the 3000 cap",
             "rejected_by": []},
        ]
        with journal.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        patterns = pathlib.Path(tmp) / "patterns.md"
        patterns.write_text(
            "## P-001 \u2014 existing\n\n**Class:** kernel-loc-cap-exceeded\n",
            encoding="utf-8",
        )
        result = detect(str(journal), tmp, 5, str(patterns))
        if result["ok"] and result["patterns_written"] == []:
            results.append("skip known class: pass")
        else:
            failures.append(f"skip known class: got {result}")

    # --- Detection: skips low-count class ---
    with tempfile.TemporaryDirectory() as tmp:
        journal = pathlib.Path(tmp) / "journal.jsonl"
        entries = [
            {"kind": "cycle", "cycle": 1, "outcome": "rejected",
             "why": "task A",
             "reason": "deterministic: kernel is 3016 lines, over the 3000 cap",
             "rejected_by": []},
        ]
        with journal.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        patterns = pathlib.Path(tmp) / "patterns.md"
        patterns.write_text(
            "## P-001 \u2014 existing\n\n**Class:** existing-class\n",
            encoding="utf-8",
        )
        result = detect(str(journal), tmp, 5, str(patterns))
        if result["ok"] and result["patterns_written"] == []:
            results.append("skip low count: pass")
        else:
            failures.append(f"skip low count: got {result}")

    return {
        "ok": len(failures) == 0,
        "results": results,
        "failures": failures,
    }


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON: {exc}"}))
        sys.exit(1)

    op = payload.get("op", "detect")

    if op == "selfcheck":
        result = selfcheck()
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["ok"] else 1)

    if op == "detect":
        journal_path = payload.get("journal_path", "")
        repo_path = payload.get("repo_path", "")
        cycle = payload.get("cycle", 0)
        patterns_path = payload.get("patterns_path")

        if not journal_path:
            print(json.dumps({"ok": False, "error": "journal_path required"}))
            sys.exit(1)

        result = detect(journal_path, repo_path, cycle, patterns_path)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["ok"] else 1)

    print(json.dumps({"ok": False, "error": f"unknown op: {op}"}))
    sys.exit(1)


if __name__ == "__main__":
    main()
