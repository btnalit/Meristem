#!/usr/bin/env python3
"""Failure aggregator organ.

Detects repeated rejection patterns from the journal and writes structured
entries to state/patterns.md.  Purely additive to the kernel's circuit breaker
(meristem/breaker.py): the breaker decides parking; this organ records
failure CLASSES for Principle 2 (Meta-over-Patch) — the pattern register is
the memory that turns 'fix the instance' into 'make the class impossible'.

ABI: stdin JSON -> stdout JSON, exit 0 on success.
"""

from __future__ import annotations

import json
import re
import sys
import pathlib


def read_jsonl(path: str) -> list:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def classify(reasons: list[str]) -> str:
    """Extract a failure class from rejection reason text."""
    text = " ".join(reasons).lower()
    if any(w in text for w in ("weaken", "gate", "invariant")):
        return "gate-weakening"
    if any(w in text for w in ("no effective change", "no change",
                              "proposed nothing", "empty")):
        return "no-effective-change"
    if any(w in text for w in ("parse", "json", "unparseable")):
        return "parse-error"
    if any(w in text for w in ("closure", "budget", "token")):
        return "closure-budget"
    if "secret" in text:
        return "secret-leak"
    if any(w in text for w in ("protected", "root/", "substrate/")):
        return "protected-path"
    if any(w in text for w in ("probe", "regression")):
        return "probe-regression"
    if any(w in text for w in ("manifest", "organ")):
        return "organ-manifest"
    if any(w in text for w in ("immune", "fixture")):
        return "immune-failure"
    return "unclassified"


def aggregate(journal_path: str, repo_path: str, cycle: int) -> dict:
    """Query journal for rejections, detect patterns, write to patterns.md."""
    rows = read_jsonl(journal_path)
    repo = pathlib.Path(repo_path)
    patterns_path = repo / "state" / "patterns.md"

    # Ensure patterns.md exists with a header.
    if not patterns_path.exists():
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        patterns_path.write_text("# Pattern Register\n\n", encoding="utf-8")
    existing = patterns_path.read_text(encoding="utf-8")

    # Group rejection cycles by task text.
    by_task: dict[str, list] = {}
    for row in rows:
        if row.get("kind") != "cycle" or row.get("outcome") != "rejected":
            continue
        task = row.get("why", "")
        if task:
            by_task.setdefault(task, []).append(row)

    # Find next FA id.
    fa_ids = re.findall(r"## FA-(\d+)", existing)
    next_id = max((int(x) for x in fa_ids), default=0) + 1

    signals = []
    written = []

    # Split existing into sections for duplicate detection.
    sections = re.split(r"^## ", existing, flags=re.M)

    for task, cycles in sorted(by_task.items()):
        if len(cycles) < 2:
            continue

        # Extract reasons from each rejection.
        all_reasons: list[str] = []
        recent_reasons: list[tuple] = []
        for cyc in cycles:
            reasons: list[str] = []
            reason_text = cyc.get("reason", "")
            if reason_text and not reason_text.startswith("review rejected"):
                reasons.append(reason_text[:200])
            for rej in cyc.get("rejected_by") or []:
                for r in rej.get("reasons") or []:
                    reasons.append(f"{rej.get('slot', '?')}: {r[:200]}")
            if reasons:
                all_reasons.extend(reasons)
                recent_reasons.append((cyc.get("cycle", "?"), reasons))

        if not all_reasons:
            continue

        failure_class = classify(all_reasons)
        task_snippet = task[:80]

        # Skip if a pattern for this task+class already exists.
        already = any(
            task_snippet in s and failure_class in s
            for s in sections
        )
        if already:
            continue

        count = len(cycles)
        entry = f"\n## FA-{next_id:03d} — Repeated rejection: {failure_class}\n"
        entry += f"- **Task:** {task_snippet}\n"
        entry += f"- **Failure class:** {failure_class}\n"
        entry += f"- **Count:** {count} rejection(s)\n"
        entry += "- **Representative reasons:**\n"
        for cyc_num, reasons in recent_reasons[-3:]:
            for r in reasons[:2]:
                entry += f"  - cycle {cyc_num}: {r}\n"
        entry += f"- **Detected at cycle:** {cycle}\n\n"

        with patterns_path.open("a", encoding="utf-8") as f:
            f.write(entry)
        written.append(f"FA-{next_id:03d}")
        next_id += 1

        action = "surface"
        if count >= 4:
            action = "block"
        elif count >= 3:
            action = "escalate"

        signals.append({
            "task": task_snippet,
            "class": failure_class,
            "count": count,
            "action": action,
            "pattern_id": f"FA-{next_id - 1:03d}",
        })

    return {"ok": True, "signals": signals, "patterns_written": written}


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"invalid input: {exc}"}))
        sys.exit(1)

    if payload.get("op") == "selfcheck":
        results = []
        try:
            _ = read_jsonl("/dev/null")
            results.append("read_jsonl: ok")
        except Exception as e:
            results.append(f"read_jsonl: FAIL {e}")
        try:
            c = classify(["gate weakening detected"])
            results.append(f"classify: ok ({c})")
        except Exception as e:
            results.append(f"classify: FAIL {e}")
        print(json.dumps({"ok": True, "results": results}))
        return

    if payload.get("op") != "aggregate":
        print(json.dumps({"ok": False,
                          "error": f"unknown op: {payload.get('op')}"}))
        sys.exit(1)

    result = aggregate(
        payload.get("journal_path", ""),
        payload.get("repo_path", ""),
        int(payload.get("cycle", 0)),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
