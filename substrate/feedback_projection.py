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

from substrate import diagnostic_review, reflection as reflection_module, strategy_memory, task_state


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
        "attempt_id": event.get("attempt_id"),
        "soil_cycle": event.get("soil_cycle"),
        "candidate": str(event.get("commit", ""))[:12] or None,
        "primary_probe": event.get("primary_probe"),
    }
    if primary:
        for key in ("before", "after", "delta", "status"):
            if key in primary:
                result[key] = primary[key]
    return {k: v for k, v in result.items() if v is not None}


def _safe_reason(*, outcome: str | None = None, failure_reason: str | None = None,
                 delta: float | None = None) -> str:
    """Return a closed vocabulary; never project free-form ledger text."""
    allowed = {
        "path_violation", "propose_failed", "prompt_over_budget", "provider_error",
        "rate_limited", "gateway_error", "worker_error", "measurement_error",
        "no_candidate", "empty_mutation", "unfulfilled", "fulfilled",
    }
    if failure_reason in allowed:
        return failure_reason
    if outcome == "UNFULFILLED":
        return "delta_below_threshold" if isinstance(delta, (int, float)) else "unfulfilled"
    if outcome:
        return outcome.lower()
    return "no_candidate"


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
            match = next((o for o in observed
                          if o.get("commit") == event.get("commit")
                          and o.get("attempt_id") == event.get("attempt_id")), None)
            if match is None and not event.get("attempt_id"):
                match = next((o for o in observed if o.get("commit") == event.get("commit")), None)
            if match:
                item = _observed_summary(match)
                shape = event.get("strategy_shape") or strategy_memory.diff_shape(repo, event["commit"])
                item["strategy_shape"] = shape
                if shape.get("patch_sha256") is None and event.get("strategy_fingerprint"):
                    item["strategy_fingerprint"] = event["strategy_fingerprint"]
                else:
                    item["strategy_fingerprint"] = strategy_memory.strategy_fingerprint(
                        event.get("changed_paths", []), shape)
                item["changed_paths"] = [strategy_memory.path_family(p)
                                         for p in event.get("changed_paths", [])]
                matching = [o for o in outcomes if o.get("source") == match.get("event_id")]
                if matching:
                    item["outcome"] = matching[-1].get("outcome")
                    item["reason"] = _safe_reason(
                        outcome=item.get("outcome"), delta=item.get("delta"))
                recent.append(item)
                continue
        recent.append({k: v for k, v in {
            "task_id": event.get("task_id"),
            "attempt_id": event.get("attempt_id"),
            "soil_cycle": event.get("soil_cycle"),
            "candidate": None,
            "outcome": "NO_CANDIDATE",
            "reason": _safe_reason(failure_reason=event.get("failure_reason")),
        }.items() if v is not None})
    last = recent[-1] if recent else {}
    observed_by_event = {r.get("event_id"): r for r in observed if r.get("event_id")}
    normalized_rows = []
    for row in rows:
        if row.get("kind") == "promotion_outcome" and not row.get("task_id"):
            source = row.get("source")
            source_event = observed_by_event.get(source)
            if source_event and source_event.get("task_id"):
                row = {**row, "task_id": source_event["task_id"],
                       "attempt_id": row.get("attempt_id") or source_event.get("attempt_id")}
        normalized_rows.append(row)
    state_fields = task_state.projection_fields(normalized_rows)
    strategy_rows = [item for item in recent if item.get("strategy_fingerprint")]
    strategy_summary = strategy_memory.summarize_strategies(strategy_rows, task_id=task_id)
    for item in recent:
        fingerprint = item.get("strategy_fingerprint")
        repeated = bool(fingerprint and strategy_summary.get(fingerprint, {}).get("repeated_failure"))
        diagnosis = diagnostic_review.diagnose_failure(
            failure_class=item.get("reason", "").split(":", 1)[0].lower(),
            changed_paths=item.get("changed_paths", []),
            repeated_strategy=repeated,
            delta=item.get("delta"),
        )
        item["diagnosis_class"] = diagnosis["diagnosis_class"]
        item["mechanism_status"] = diagnosis["mechanism_status"]
        item["next_experiment_constraint"] = diagnosis["next_experiment_constraint"]
    reflection = reflection_module.build_reflection({
        "recent_attempts": recent,
        "source_attempt_ids": [r.get("attempt_id") for r in recent if r.get("attempt_id")],
        "source_ledger_tail_hash": _tail_hash(ledger_path),
    })
    facts = {
        **state_fields,
        "core_pressure": 0.0,
        "last_attempt": last,
        "recent_attempts": recent,
        "strategy_memory": strategy_summary,
        "reflection": reflection,
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
