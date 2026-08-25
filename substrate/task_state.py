"""Soil-derived task lifecycle; seed cannot write these states."""
from __future__ import annotations

from collections import defaultdict

from substrate.learning_state import MECHANISM_FAILURE_REASONS as _MECHANISM_FAILURES


def derive_task_states(rows: list[dict], *, threshold: int = 3) -> dict[str, dict]:
    data = defaultdict(lambda: {
        "state": "open", "attempts": 0, "semantic_failures": 0,
        "mechanism_failures": 0, "promotion_gated_attempts": 0, "fulfilled": False,
    })
    for row in rows:
        task_id = row.get("task_id")
        if not task_id:
            continue
        item = data[task_id]
        if row.get("kind") == "cycle":
            item["attempts"] += 1
            reason = row.get("failure_reason")
            if reason in _MECHANISM_FAILURES:
                item["mechanism_failures"] += 1
                item["state"] = "blocked"
            elif item["state"] == "blocked":
                # blocked decays: a mechanism failure is an operator-diagnosed
                # environment fault, not a task property. The next
                # task-attributed cycle row (mechanism healthy again)
                # recomputes state normally instead of staying stuck.
                item["state"] = "unfulfilled" if item["semantic_failures"] > 0 else "open"
        elif row.get("kind") == "candidate_preflight":
            if row.get("failure_reason") == "syntax_failure":
                item["semantic_failures"] += 1
                if item["semantic_failures"] >= threshold:
                    item["state"] = "parked"
                else:
                    item["state"] = "unfulfilled"
        elif row.get("kind") == "promotion_outcome":
            outcome = row.get("outcome")
            preflight_gated = outcome == "PREFLIGHT_GATED" or (
                outcome == "REJECTED" and str(row.get("why", "")) ==
                "H1-preflight: promotion disabled")
            if preflight_gated:
                item["promotion_gated_attempts"] += 1
                # parked/fulfilled stay sticky; blocked no longer defers here.
                if item["state"] not in {"fulfilled", "parked"}:
                    item["state"] = "promotion_gated"
            elif row.get("counts_against_task_quota"):
                item["semantic_failures"] += 1
                if item["semantic_failures"] >= threshold:
                    item["state"] = "parked"
                else:
                    item["state"] = "unfulfilled"
        elif row.get("kind") == "accepted_fitness":
            item["fulfilled"] = True
            item["state"] = "fulfilled"
    return dict(data)


def projection_fields(rows: list[dict], *, threshold: int = 3) -> dict:
    states = derive_task_states(rows, threshold=threshold)
    return {
        "done_task_ids": sorted(k for k, v in states.items() if v["state"] == "fulfilled"),
        "parked_task_ids": sorted(k for k, v in states.items() if v["state"] == "parked"),
        "blocked_task_ids": sorted(k for k, v in states.items() if v["state"] == "blocked"),
        "task_states": states,
    }
