"""Soil-owned rollback contract for bounded H1-preflight runs.

This module deliberately plans and validates rollback receipts; it does not
perform git mutation. A root/manual operator must execute the reviewed plan
through the runbook and record the resulting receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


ROLLBACK_PHASES: FrozenSet[str] = frozenset({
    "candidate_unmerged",
    "merged_pre_accept",
    "accepted_pre_commit",
    "promoted_bad_candidate",
})


@dataclass(frozen=True)
class RollbackPlan:
    task_id: str
    attempt_id: str
    from_commit: str
    restore_commit: str
    phase: str
    reason: str
    authority: str = "root_manual"

    def __post_init__(self) -> None:
        if self.phase not in ROLLBACK_PHASES:
            raise ValueError(f"unsupported rollback phase: {self.phase!r}")
        if self.authority != "root_manual":
            raise ValueError("rollback authority must be root_manual")
        for name in ("task_id", "attempt_id", "from_commit", "restore_commit", "reason"):
            if not getattr(self, name).strip():
                raise ValueError(f"rollback field {name} is required")
        if self.from_commit == self.restore_commit and self.phase != "candidate_unmerged":
            raise ValueError("state-changing rollback must restore a different commit")


def build_plan(*, task_id: str, attempt_id: str, from_commit: str,
               restore_commit: str, phase: str, reason: str) -> RollbackPlan:
    return RollbackPlan(task_id, attempt_id, from_commit, restore_commit, phase, reason)


def validate_receipt(plan: RollbackPlan, receipt: dict) -> None:
    """Fail closed unless receipt proves the rollback boundary was restored."""
    required = {"task_id", "attempt_id", "restored_commit", "generation",
                "soil_cycle", "ledger_tail_hash", "task_state", "status"}
    missing = sorted(required - receipt.keys())
    if missing:
        raise ValueError(f"rollback receipt missing fields: {missing}")
    if receipt["status"] != "rolled_back":
        raise ValueError("rollback receipt status is not rolled_back")
    if receipt["task_id"] != plan.task_id or receipt["attempt_id"] != plan.attempt_id:
        raise ValueError("rollback receipt identity does not match plan")
    if receipt["restored_commit"] != plan.restore_commit:
        raise ValueError("rollback restored unexpected commit")
    if not isinstance(receipt["soil_cycle"], int) or isinstance(receipt["soil_cycle"], bool):
        raise ValueError("soil_cycle must be an integer")
    if not str(receipt["ledger_tail_hash"]).strip():
        raise ValueError("ledger_tail_hash is required")
    if receipt["task_state"] not in {"open", "unfulfilled", "parked", "blocked", "fulfilled"}:
        raise ValueError("invalid task_state in rollback receipt")
