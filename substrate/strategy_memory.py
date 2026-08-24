"""Bounded, soil-derived strategy metadata; never stores mutation bodies."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Iterable


def strategy_fingerprint(changed_paths: Iterable[str]) -> str:
    families = sorted({path_family(path) for path in changed_paths if path})
    payload = "\n".join(families).encode("utf-8")
    return "strat-" + hashlib.sha256(payload).hexdigest()[:24]


def path_family(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "body" and parts[1] == "organs":
        return "/".join(parts[:3])
    if parts and parts[0] in {"tests", "seed"}:
        return parts[0]
    return parts[0] if parts else "unknown"


def summarize_strategies(rows: list[dict], *, task_id: str | None = None) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        if task_id is not None and row.get("task_id") != task_id:
            continue
        fingerprint = row.get("strategy_fingerprint")
        if fingerprint:
            grouped[fingerprint].append(row)
    result = {}
    for fingerprint, attempts in grouped.items():
        deltas = [r.get("delta") for r in attempts if isinstance(r.get("delta"), (int, float))]
        failures = [r for r in attempts if r.get("outcome") not in {"FULFILLED", "PROMOTED"}]
        result[fingerprint] = {
            "attempts": len(attempts),
            "best_delta": max(deltas) if deltas else None,
            "last_outcome": attempts[-1].get("outcome"),
            "repeated_failure": len(failures) >= 2,
            "novel": len(attempts) == 1,
        }
    return result
