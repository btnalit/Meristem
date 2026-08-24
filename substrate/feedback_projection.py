"""Soil-owned, bounded projection of learning facts into the seed surface."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import pwd
import grp
import tempfile
from typing import Any


_PUBLIC_KEYS = (
    "task_id", "soil_cycle", "candidate", "outcome", "reason",
    "primary_probe", "before", "after", "delta", "status",
)


def _tail_hash(ledger_path: pathlib.Path) -> str:
    return hashlib.sha256(ledger_path.read_bytes()).hexdigest()


def _observed_summary(event: dict[str, Any]) -> dict[str, Any]:
    records = event.get("records") or []
    primary = next((r for r in records if r.get("probe_id") == event.get("primary_probe")), None)
    result = {
        "task_id": event.get("task_id"),
        "soil_cycle": event.get("soil_cycle"),
        "candidate": str(event.get("commit", ""))[:12] or None,
        "primary_probe": event.get("primary_probe"),
    }
    if primary:
        for key in ("before", "after", "delta", "status"):
            if key in primary:
                result[key] = primary[key]
    return {k: v for k, v in result.items() if v is not None}


def projection_is_fresh(repo: pathlib.Path) -> bool:
    repo = pathlib.Path(repo)
    ledger = repo / "state" / "soil-ledger.jsonl"
    target = repo / "seed" / "feedback.json"
    if not ledger.is_file() or not target.is_file():
        return False
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return doc.get("source_ledger_tail_hash") == _tail_hash(ledger)


def write_projection(repo: pathlib.Path, *, task_id: str | None = None) -> pathlib.Path:
    """Write a bounded feedback view after a soil cycle.

    The projection is derived only from soil ledger events and contains no
    prompt, response, credential, or mutation body. It is intentionally
    replaceable and can be copied into the worker surface read-only.
    """
    repo = pathlib.Path(repo)
    ledger_path = repo / "state" / "soil-ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    observed = [r for r in rows if r.get("kind") == "observed_fitness"
                and (task_id is None or r.get("task_id") == task_id)]
    cycles = [r for r in rows if r.get("kind") == "cycle"
              and r.get("task_id") and r.get("exit_code") is not None
              and (task_id is None or r.get("task_id") == task_id)]
    outcomes = [r for r in rows if r.get("kind") == "promotion_outcome"]
    recent = []
    for event in cycles[-8:]:
        if event.get("commit"):
            match = next((o for o in observed if o.get("commit") == event.get("commit")), None)
            if match:
                item = _observed_summary(match)
                matching = [o for o in outcomes if o.get("source") == match.get("event_id")]
                if matching:
                    item["outcome"] = matching[-1].get("outcome")
                    item["reason"] = matching[-1].get("why")
                recent.append(item)
                continue
        recent.append({k: v for k, v in {
            "task_id": event.get("task_id"),
            "soil_cycle": event.get("soil_cycle"),
            "candidate": None,
            "outcome": "NO_CANDIDATE",
            "reason": event.get("failure_reason", "no_candidate"),
        }.items() if v is not None})
    last = recent[-1] if recent else {}
    facts = {
        "done_task_ids": [],
        "parked_task_ids": [],
        "core_pressure": 0.0,
        "last_attempt": last,
        "recent_attempts": recent,
    }
    payload = {
        "schema_version": 1,
        "source_ledger_tail_hash": _tail_hash(ledger_path),
        "facts": facts,
    }
    target = repo / "seed" / "feedback.json"
    fd, tmp_name = tempfile.mkstemp(prefix=".feedback.", dir=str(target.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
        os.chown(tmp_name, pwd.getpwnam("soil").pw_uid,
                 grp.getgrnam("soil").gr_gid)
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return target
