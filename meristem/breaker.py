"""Circuit breaker: park a task that has been rejected too many times.

When a task is rejected repeatedly, continuing to retry it burns cycles
without progress. The breaker counts rejections from the journal and
signals when a task should be parked — set aside until a human clears it.

This is the structural complement to the agenda's retry semantics: a
rejected task stays open by design, but unbounded retry on the same task
is not progress, it is a loop. The breaker turns that loop into a stop.
"""

from __future__ import annotations

from . import JOURNAL, read_jsonl


def _cycles_for(task: str) -> list[dict]:
    return [
        row
        for row in read_jsonl(JOURNAL)
        if row.get("kind") == "cycle" and row.get("why") == task
    ]


def _faulted_cycles() -> set[int]:
    """Cycle numbers that ended in a fault rather than a verdict."""
    return {
        row.get("cycle")
        for row in read_jsonl(JOURNAL)
        if row.get("kind") == "fault"
    }


def rejections_for(task: str) -> int:
    """Rejections that were actually JUDGED -- faults do not count.

    A fault and a rejection look identical in the outcome field (both land as
    'rejected') but they mean opposite things. A rejection is the gates
    working: the change was seen and refused, and retrying the same approach
    will be refused again. A fault is the MECHANISM failing -- an unparseable
    reply, a rate limit -- where the proposal was never judged at all and a
    retry is exactly the right move.

    Counting them together parks a task the gates never objected to, which is
    what happened to memory-graph/edges.py: three unparseable replies, zero
    verdicts, parked as though it had been refused three times (P-016).
    """
    faulted = _faulted_cycles()
    return sum(
        1
        for row in _cycles_for(task)
        if row.get("outcome") == "rejected" and row.get("cycle") not in faulted
    )


def faults_for(task: str) -> int:
    """Cycles on this task where the mechanism failed before any verdict."""
    faulted = _faulted_cycles()
    return sum(1 for row in _cycles_for(task) if row.get("cycle") in faulted)


def canary_rejects_for(task: str) -> int:
    """Canary rejections: approved by reviewers but failed functional tests."""
    return sum(
        1
        for row in read_jsonl(JOURNAL)
        if row.get("kind") == "canary_reject" and row.get("why") == task
    )


def should_park(task: str, limit: int = 3, fault_limit: int = 6,
                canary_limit: int = 3) -> bool:
    """True when the task should be set aside.

    Three independent thresholds: judged rejections (tight — deterministic),
    faults (loose — transient), canary rejects (tight — approved-but-broken
    code that the seed can't fix within budget).
    """
    return (rejections_for(task) >= limit
            or faults_for(task) >= fault_limit
            or canary_rejects_for(task) >= canary_limit)
