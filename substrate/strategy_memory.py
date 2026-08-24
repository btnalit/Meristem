"""Bounded, soil-derived strategy metadata; never stores mutation bodies."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from typing import Iterable


def diff_shape(repo, commit: str) -> dict:
    """Return bounded soil-side diff metadata; never return patch content."""
    try:
        patch = subprocess.run(
            ["git", "diff", "--no-ext-diff", f"{commit}^", commit],
            cwd=str(repo), capture_output=True, text=True, check=True).stdout
        numstat = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--numstat", f"{commit}^", commit],
            cwd=str(repo), capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {"files": 0, "families": [], "patch_sha256": None}
    families = defaultdict(lambda: {"files": 0, "added": 0, "deleted": 0})
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if not added.isdigit() or not deleted.isdigit():
            continue
        item = families[path_family(path)]
        item["files"] += 1
        item["added"] += int(added)
        item["deleted"] += int(deleted)
    return {
        "files": sum(item["files"] for item in families.values()),
        "families": [{"family": family, **families[family]}
                     for family in sorted(families)],
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
    }


def strategy_fingerprint(changed_paths: Iterable[str], diff_shape: dict | None = None) -> str:
    families = sorted({path_family(path) for path in changed_paths if path})
    payload = {"families": families, "diff_shape": diff_shape or {}}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "strat-" + hashlib.sha256(encoded).hexdigest()[:24]


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
