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


def rejections_for(task: str) -> int:
    """Count how many cycle records for this task had outcome 'rejected'."""
    return sum(
        1
        for row in read_jsonl(JOURNAL)
        if row.get("kind") == "cycle"
        and row.get("why") == task
        and row.get("outcome") == "rejected"
    )


def should_park(task: str, limit: int = 3) -> bool:
    """True when the task has been rejected enough times to park."""
    return rejections_for(task) >= limit
