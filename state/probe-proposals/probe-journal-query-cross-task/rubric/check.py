#!/usr/bin/env python3
"""Probe: verify cross-task failure aggregation across two distinct tasks.

Creates a temporary journal with two tasks sharing a failure class
(closure-budget), invokes the journal-query organ's aggregate_cross_task
op, and verifies the result correctly identifies the shared class across
both tasks while excluding classes that appear in only one task.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile


def main():
    data = json.loads(sys.stdin.read())
    workdir = pathlib.Path(data.get("workdir", "."))
    organ = workdir / "body" / "organs" / "journal-query" / "main.py"

    if not organ.exists():
        print(json.dumps({"score": 0.0,
                          "detail": f"organ not found at {organ}"}))
        return

    # Build a journal with two distinct tasks sharing a failure class
    entries = [
        {"ts": "2026-01-01T00:00:00+00:00", "kind": "cycle",
         "cycle": 1, "outcome": "rejected", "why": "Task Alpha",
         "rejected_by": [{"slot": "review:deepseek",
                          "reasons": ["closure budget exceeded 50000 tokens"]}],
         "reason": "review rejected"},
        {"ts": "2026-01-02T00:00:00+00:00", "kind": "cycle",
         "cycle": 2, "outcome": "rejected", "why": "Task Beta",
         "rejected_by": [{"slot": "review:sensenova",
                          "reasons": ["closure budget too large for review"]}],
         "reason": "review rejected"},
        {"ts": "2026-01-03T00:00:00+00:00", "kind": "cycle",
         "cycle": 3, "outcome": "rejected", "why": "Task Alpha",
         "rejected_by": [{"slot": "review:deepseek",
                          "reasons": ["gate weakening: removed a check"]}],
         "reason": "review rejected"},
    ]

    fd, tmp_journal = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        payload = json.dumps({
            "op": "aggregate_cross_task",
            "journal_path": tmp_journal,
            "min_tasks": 2,
        })

        result = subprocess.run(
            [sys.executable, str(organ)],
            input=payload, capture_output=True, text=True, timeout=30,
        )

        if result.returncode != 0:
            print(json.dumps({"score": 0.0,
                "detail": f"organ exited {result.returncode}: "
                          f"{result.stderr[:200]}"}))
            return

        try:
            resp = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            print(json.dumps({"score": 0.0,
                "detail": f"non-JSON output: {exc}"}))
            return

        if not resp.get("ok"):
            print(json.dumps({"score": 0.0,
                "detail": f"organ returned ok=false: "
                          f"{resp.get('error', '')}"}))
            return

        classes = resp.get("classes", [])

        # Verify closure-budget class appears across both tasks
        closure = next((c for c in classes
                        if c["class"] == "closure-budget"), None)
        if closure is None:
            print(json.dumps({"score": 0.0,
                "detail": "closure-budget class not found in "
                          "cross-task results"}))
            return

        if closure["task_count"] != 2:
            print(json.dumps({"score": 0.0,
                "detail": f"expected 2 tasks, got "
                          f"{closure['task_count']}"}))
            return

        if ("Task Alpha" not in closure["tasks"] or
                "Task Beta" not in closure["tasks"]):
            print(json.dumps({"score": 0.0,
                "detail": f"missing tasks: {closure['tasks']}"}))
            return

        # Verify gate-weakening does NOT appear (only 1 task)
        gate = next((c for c in classes
                     if c["class"] == "gate-weakening"), None)
        if gate is not None:
            print(json.dumps({"score": 0.0,
                "detail": "gate-weakening should not appear "
                          "(only 1 task)"}))
            return

        print(json.dumps({"score": 100.0,
            "detail": "cross-task aggregation correctly identified "
                      "closure-budget across Task Alpha and Task Beta"}))
    finally:
        os.unlink(tmp_journal)


if __name__ == "__main__":
    main()
