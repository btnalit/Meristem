#!/usr/bin/env python3
"""Journal-query organ: answers questions about state/journal.jsonl.

Two operations, both taking {"task": "..."} in args:

  rejections_for  -- count of cycle records whose why equals the task and
                     whose outcome is "rejected" with NO matching fault
                     record. A rejection is the gates working: the change
                     was seen and refused.

  faults_for       -- count of cycle records on that task that DO have a
                     matching fault record. A fault is the mechanism
                     failing before any verdict was reached.

The distinction is load-bearing (P-016): a fault and a rejection both land
as outcome "rejected" in the journal, but they mean opposite things.
Counting them together parks a task the gates never objected to.

Workdir is taken from args (key "workdir"), defaulting to the current
directory. The journal is read from <workdir>/state/journal.jsonl.
"""

from __future__ import annotations

import json
import pathlib
import sys


def _read_jsonl(path: pathlib.Path) -> list[dict]:
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


def _cycles_for(rows: list[dict], task: str) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("kind") == "cycle" and row.get("why") == task
    ]


def _faulted_cycles(rows: list[dict]) -> set[int]:
    """Cycle numbers that ended in a fault rather than a verdict."""
    return {
        row.get("cycle")
        for row in rows
        if row.get("kind") == "fault"
    }


def rejections_for(rows: list[dict], task: str) -> int:
    """Rejections that were actually JUDGED -- faults do not count."""
    faulted = _faulted_cycles(rows)
    return sum(
        1
        for row in _cycles_for(rows, task)
        if row.get("outcome") == "rejected"
        and row.get("cycle") not in faulted
    )


def faults_for(rows: list[dict], task: str) -> int:
    """Cycles on this task where the mechanism failed before any verdict."""
    faulted = _faulted_cycles(rows)
    return sum(
        1
        for row in _cycles_for(rows, task)
        if row.get("cycle") in faulted
    )


def _handle(args: dict) -> dict:
    task = args.get("task", "")
    workdir = pathlib.Path(args.get("workdir", ".")).resolve()
    journal = workdir / "state" / "journal.jsonl"
    rows = _read_jsonl(journal)

    op = args.get("op", "")
    if op == "rejections_for":
        return {"count": rejections_for(rows, task)}
    if op == "faults_for":
        return {"count": faults_for(rows, task)}
    raise ValueError(f"unknown op '{op}'")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        op = payload.get("op", "")
        args = payload.get("args") or {}
        args["op"] = op
        result = _handle(args)
        print(json.dumps({"ok": True, "result": result}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "result": {"error": str(exc)[:400]}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
