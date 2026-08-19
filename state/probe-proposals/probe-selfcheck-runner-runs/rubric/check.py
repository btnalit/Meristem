#!/usr/bin/env python3
"""Rubric: verify the selfcheck-runner actually runs each cycle.

Receives {"workdir": "...", "probe": "..."} on stdin.
Returns {"score": float, "detail": "..."} on stdout.

Score 100 when at least one journal entry with kind='selfcheck-runner'
exists within the last N cycles. Score 0 otherwise.

This probe does NOT inspect source code — it queries the journal, which
is the authoritative record of what actually happened. A runner that is
wired but never invoked, or invoked but never journaled, both fail here.
That is the point: the probe tests behaviour, not structure.
"""

from __future__ import annotations

import json
import pathlib
import sys


def read_jsonl(path: pathlib.Path) -> list[dict]:
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


def main() -> int:
    payload = json.loads(sys.stdin.read())
    workdir = pathlib.Path(payload.get("workdir", ".")).resolve()
    probe_id = payload.get("probe", "")

    # Load N from probe.json if available; default to 5.
    n_cycles = 5
    probe_meta = workdir / "state" / "probe-proposals" / probe_id / "probe.json"
    if probe_meta.exists():
        try:
            meta = json.loads(probe_meta.read_text(encoding="utf-8"))
            n_cycles = int(meta.get("n_cycles", 5))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    journal_path = workdir / "state" / "journal.jsonl"
    rows = read_jsonl(journal_path)

    # Determine the latest cycle number from all cycle entries.
    cycle_numbers = [
        row.get("cycle", 0)
        for row in rows
        if row.get("kind") == "cycle" and isinstance(row.get("cycle"), int)
    ]
    if not cycle_numbers:
        # No cycles have run at all — the runner certainly hasn't either.
        result = {
            "score": 0.0,
            "detail": "no cycle entries in journal; selfcheck-runner cannot "
                      "have run",
        }
        print(json.dumps(result))
        return 0

    latest_cycle = max(cycle_numbers)
    threshold = latest_cycle - n_cycles

    # Find selfcheck-runner entries within the last N cycles.
    runner_entries = [
        row
        for row in rows
        if row.get("kind") == "selfcheck-runner"
        and isinstance(row.get("cycle"), int)
        and row.get("cycle", 0) >= threshold
    ]

    if runner_entries:
        cycles_seen = sorted(
            {row.get("cycle") for row in runner_entries}, reverse=True
        )
        result = {
            "score": 100.0,
            "detail": f"selfcheck-runner ran in cycle(s) "
                      f"{', '.join(str(c) for c in cycles_seen[:5])} "
                      f"(within last {n_cycles} cycles; latest={latest_cycle})",
        }
    else:
        # Also check if ANY selfcheck-runner entry exists at all, for a
        # more informative failure message.
        any_runner = [
            row for row in rows if row.get("kind") == "selfcheck-runner"
        ]
        if any_runner:
            last_run = max(
                row.get("cycle", 0)
                for row in any_runner
                if isinstance(row.get("cycle"), int)
            )
            result = {
                "score": 0.0,
                "detail": f"selfcheck-runner last ran in cycle {last_run}, "
                          f"which is outside the last {n_cycles} cycles "
                          f"(latest={latest_cycle}); runner may have stopped",
            }
        else:
            result = {
                "score": 0.0,
                "detail": f"no selfcheck-runner entries in journal at all "
                          f"(latest cycle={latest_cycle}); runner was never "
                          f"invoked or is not journaling",
            }

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
