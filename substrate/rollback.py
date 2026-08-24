"""Soil-owned rollback contract and live receipt verification.

Autonomous H1 executes only an explicit, soil-owned rollback plan. The
root/manual authority remains available for infrastructure emergency recovery,
but it is not the formal candidate decision authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet


ROLLBACK_PHASES: FrozenSet[str] = frozenset({
    "candidate_unmerged",
    "merged_pre_accept",
    "accepted_pre_commit",
    "promoted_bad_candidate",
})
ROLLBACK_AUTHORITIES: FrozenSet[str] = frozenset({"root_manual", "soil-autonomous"})


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
        if self.authority not in ROLLBACK_AUTHORITIES:
            raise ValueError("rollback authority must be root_manual or soil-autonomous")
        for name in ("task_id", "attempt_id", "from_commit", "restore_commit", "reason"):
            if not getattr(self, name).strip():
                raise ValueError(f"rollback field {name} is required")
        if self.from_commit == self.restore_commit and self.phase != "candidate_unmerged":
            raise ValueError("state-changing rollback must restore a different commit")


def build_plan(*, task_id: str, attempt_id: str, from_commit: str,
               restore_commit: str, phase: str, reason: str,
               authority: str = "root_manual") -> RollbackPlan:
    return RollbackPlan(task_id, attempt_id, from_commit, restore_commit, phase,
                        reason, authority)


def execute_autonomous_rollback(repo: Path, plan: RollbackPlan) -> str:
    """Execute a soil-owned rollback against the explicitly bound candidate.

    This mutates only the supplied repository. It refuses a moved HEAD, an
    unresolved restore commit, or a non-autonomous plan, and returns the live
    restored HEAD for receipt construction. Ledger/projection updates remain
    explicit caller-owned facts and must be verified with ``verify_receipt_state``.
    """
    if plan.authority != "soil-autonomous":
        raise ValueError("autonomous rollback requires soil-autonomous authority")
    repo = Path(repo)
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                       text=True).strip()
        subprocess.check_call(["git", "cat-file", "-e", plan.from_commit + "^{commit}"],
                              cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "cat-file", "-e", plan.restore_commit + "^{commit}"],
                              cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("rollback commit identity is not resolvable") from exc
    if head != plan.from_commit:
        raise ValueError("rollback source HEAD moved before autonomous execution")
    ledger_path = repo / "state" / "soil-ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def append_event(event: dict) -> None:
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    append_event({"kind": "rollback_intent", "task_id": plan.task_id,
                  "attempt_id": plan.attempt_id, "from_commit": plan.from_commit,
                  "restore_commit": plan.restore_commit, "phase": plan.phase,
                  "authority": plan.authority})
    subprocess.check_call(["git", "reset", "--hard", plan.restore_commit],
                          cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    restored = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                       text=True).strip()
    if restored != plan.restore_commit:
        raise ValueError("autonomous rollback did not restore the planned commit")
    append_event({"kind": "rollback_committed", "task_id": plan.task_id,
                  "attempt_id": plan.attempt_id, "from_commit": plan.from_commit,
                  "restored_commit": restored, "phase": plan.phase,
                  "authority": plan.authority})
    return restored


def validate_receipt_contract(plan: RollbackPlan, receipt: dict) -> None:
    required = {"task_id", "attempt_id", "from_commit", "phase", "authority",
                "restored_commit", "generation", "soil_cycle", "ledger_tail_hash",
                "task_state", "status"}
    missing = sorted(required - receipt.keys())
    if missing:
        raise ValueError(f"rollback receipt missing fields: {missing}")
    if receipt["status"] != "rolled_back":
        raise ValueError("rollback receipt status is not rolled_back")
    if receipt["task_id"] != plan.task_id or receipt["attempt_id"] != plan.attempt_id:
        raise ValueError("rollback receipt identity does not match plan")
    if receipt["from_commit"] != plan.from_commit or receipt["phase"] != plan.phase:
        raise ValueError("rollback receipt plan boundary does not match")
    if receipt["authority"] != plan.authority:
        raise ValueError("rollback receipt authority does not match")
    if receipt["restored_commit"] != plan.restore_commit:
        raise ValueError("rollback restored unexpected commit")
    if not isinstance(receipt["soil_cycle"], int) or isinstance(receipt["soil_cycle"], bool):
        raise ValueError("soil_cycle must be an integer")
    if len(str(receipt["ledger_tail_hash"])) != 64:
        raise ValueError("ledger_tail_hash must be a sha256 digest")
    if receipt["task_state"] not in {"open", "unfulfilled", "parked", "blocked",
                                     "fulfilled", "promotion_gated"}:
        raise ValueError("invalid task_state in rollback receipt")


def verify_receipt_state(repo: Path, plan: RollbackPlan, receipt: dict) -> None:
    """Verify the receipt against the live repository after operator rollback."""
    validate_receipt_contract(plan, receipt)
    repo = Path(repo)
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        subprocess.check_call(["git", "cat-file", "-e", receipt["restored_commit"] + "^{commit}"],
                              cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("rollback restored commit is not live and resolvable") from exc
    if head != receipt["restored_commit"]:
        raise ValueError("current HEAD does not match rollback restored_commit")
    try:
        generation = json.loads((repo / "root" / "generations.json").read_text())["live"]
        ledger_path = repo / "state" / "soil-ledger.jsonl"
        ledger_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()
                       if line.strip()]
        ledger_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        feedback = json.loads((repo / "seed" / "feedback.json").read_text())
        task_state = feedback["facts"]["task_states"][receipt["task_id"]]["state"]
        cycles = [json.loads(line)["soil_cycle"] for line in ledger_path.read_text().splitlines()
                  if line.strip() and isinstance(json.loads(line).get("soil_cycle"), int)]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("rollback live-state evidence is unavailable") from exc
    if generation != receipt["generation"]:
        raise ValueError("rollback generation does not match live state")
    if ledger_hash != receipt["ledger_tail_hash"]:
        raise ValueError("rollback ledger tail does not match live state")
    intent = any(row.get("kind") == "rollback_intent"
                 and row.get("task_id") == plan.task_id
                 and row.get("attempt_id") == plan.attempt_id
                 and row.get("from_commit") == plan.from_commit
                 and row.get("restore_commit") == plan.restore_commit
                 and row.get("phase") == plan.phase
                 and row.get("authority") == plan.authority
                 for row in ledger_rows)
    committed = any(row.get("kind") == "rollback_committed"
                    and row.get("task_id") == plan.task_id
                    and row.get("attempt_id") == plan.attempt_id
                    and row.get("from_commit") == plan.from_commit
                    and row.get("restored_commit") == receipt["restored_commit"]
                    and row.get("phase") == plan.phase
                    and row.get("authority") == plan.authority
                    for row in ledger_rows)
    if not intent or not committed:
        raise ValueError("rollback ledger intent/commit evidence is unavailable")
    if task_state != receipt["task_state"]:
        raise ValueError("rollback task state does not match live projection")
    if not cycles or max(cycles) != receipt["soil_cycle"]:
        raise ValueError("rollback soil cycle does not match live ledger")


def validate_receipt(plan: RollbackPlan, receipt: dict, *, repo: Path | None = None) -> None:
    """Validate a receipt and, when repo is supplied, verify live state too."""
    if repo is None:
        raise ValueError("live repo is required; use validate_receipt_contract for schema-only checks")
    verify_receipt_state(repo, plan, receipt)
