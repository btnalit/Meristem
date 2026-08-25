"""Loop-level futility breaker (P0-c). Soil-owned; trips the panic latch.

Futility is a property of the *environment/loop* -- is the loop producing
candidates at all -- not a property of any one task (that quota lives in
`substrate/task_state.py`). A candidate that later gets rejected is still
evidence the loop produced something; the breaker only cares whether beats
are producing candidates at all. Shipped disabled: nothing calls
`substrate.supervisor.manual_cycle(autonomous=True)` unattended until the
pulse timer is enabled (see docs/MERISTEM-P0C-RUNBOOK.md) -- that enabling
is a separate owner decision, not part of this wave.
"""
from __future__ import annotations

import pathlib

from substrate import soil_state

#: Consecutive futile task-attributed `cycle` rows, counted from the ledger
#: tail backwards, that trip the breaker.
FUTILE_BEAT_THRESHOLD = 5


def futile_streak(rows: list[dict]) -> int:
    """Count the trailing run of futile task-attributed `cycle` rows.

    Futile = the beat produced no candidate commit, whatever the
    `failure_reason` (`propose_failed`, `path_violation`, `worker_error`,
    `task_guarded`, ...). Walk the ledger from the tail backwards; a row
    with a truthy `commit` stops the count immediately -- it resets the
    streak to 0 at that point even if the candidate was later rejected,
    because a rejection is `task_state.py`'s quota concern, not the
    breaker's futility measure. Only the trailing-consecutive run from the
    tail counts: historical futility deeper in the ledger never does.
    """
    streak = 0
    for row in reversed(rows):
        if row.get("kind") != "cycle" or not row.get("task_id"):
            continue
        if row.get("commit"):
            break
        streak += 1
    return streak


def check(repo) -> int:
    """Read `state/soil-ledger.jsonl` (missing file -> 0) and return the streak."""
    ledger = soil_state.Ledger(pathlib.Path(repo) / "state" / "soil-ledger.jsonl")
    return futile_streak(ledger.read())
