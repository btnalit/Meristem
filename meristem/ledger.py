"""Cost ledger and budget gate.

Every model call lands here. Budget caps are deterministic checks: when a
cycle or campaign exceeds its cap the loop stops and raises it to the human
mailbox rather than silently burning money.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from . import CONTROL, JOURNAL, MeristemError, append_jsonl, read_jsonl


@dataclass
class Budget:
    cycle_usd: float = 1.0
    campaign_usd: float = 25.0
    #: Call caps matter independently of money: a quota-limited endpoint is
    #: rate-limited by REQUESTS, so a USD budget over an unpriced model is an
    #: inert gate. A deterministic check that cannot fire is decoration.
    cycle_calls: int = 40
    campaign_calls: int = 1000


def load_budget() -> Budget:
    try:
        with open(CONTROL / "models.toml", "rb") as handle:
            data = tomllib.load(handle).get("budget", {})
    except FileNotFoundError:
        data = {}
    return Budget(
        cycle_usd=float(data.get("cycle_usd", 1.0)),
        campaign_usd=float(data.get("campaign_usd", 25.0)),
        cycle_calls=int(data.get("cycle_calls", 40)),
        campaign_calls=int(data.get("campaign_calls", 1000)),
    )


def price(slot_id: str, models: dict) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for a slot; zeros when unpriced."""
    for role in models.get("roles", {}).values():
        for slot in role.get("slots") or [role]:
            if slot.get("id") == slot_id or slot.get("model") == slot_id:
                return float(slot.get("usd_in", 0.0)), float(slot.get("usd_out", 0.0))
    return 0.0, 0.0


def record(cycle: int, role: str, completion, models: dict | None = None,
           ok: bool = True) -> float:
    """Append one usage row; return its estimated USD cost."""
    usd_in, usd_out = price(completion.slot or completion.model, models or {})
    cost = (
        completion.prompt_tokens * usd_in + completion.completion_tokens * usd_out
    ) / 1_000_000
    append_jsonl(
        JOURNAL,
        {
            "kind": "usage",
            "cycle": cycle,
            "role": role,
            "slot": completion.slot,
            "model": completion.model,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
            "reasoning_tokens": getattr(completion, "reasoning_tokens", 0),
            "usd": round(cost, 6),
            "ok": ok,
        },
    )
    return cost


#: Campaign calls bind over a rolling window of cycles, never over all time.
CAMPAIGN_WINDOW = 300


def _rows(cycle: int | None = None) -> list[dict]:
    return [r for r in read_jsonl(JOURNAL) if r.get("kind") == "usage"
            and (cycle is None or r.get("cycle") == cycle)]


def spent(cycle: int | None = None) -> float:
    """Total USD spent -- for one cycle when given, otherwise all time."""
    return sum(float(row.get("usd", 0.0)) for row in _rows(cycle))


def calls(cycle: int | None = None, window: int = 0) -> int:
    """Model calls made -- the binding constraint on quota-limited endpoints."""
    rows = _rows(cycle)
    last = max((r.get("cycle", 0) for r in rows), default=0)
    return len([r for r in rows if not window or r.get("cycle", 0) > last - window])


def drain_attempts(cycle: int, models: dict | None = None) -> float:
    """Bill every model attempt made since the last drain, successful or not.

    P-015: usage was recorded only where a caller succeeded, so the two cycles
    that faulted on an empty proposal have no usage rows at all -- their real
    cost is simply unknown. A budget gate that cannot see failures is blind to
    precisely the runaway it exists to stop, and a retry storm is the cheapest
    way to burn a quota.

    Returns accumulated USD cost of all drained attempts.  Callers must use
    this as the SOLE billing path -- calling record() separately double-bills
    (P-029).
    """
    from . import llm as llm_mod

    cost = 0.0
    for attempt in llm_mod.attempts_log:
        cost += record(cycle, attempt["role"], attempt["completion"],
                       models or {}, ok=attempt["ok"])
    llm_mod.attempts_log.clear()
    return cost


def check(cycle: int, budget: Budget | None = None) -> None:
    """Deterministic budget gate. Raises when any cap is exceeded."""
    budget = budget or load_budget()
    this_cycle, total = spent(cycle), spent()
    cycle_calls, total_calls = calls(cycle), calls(window=CAMPAIGN_WINDOW)
    if this_cycle > budget.cycle_usd:
        raise MeristemError(
            f"cycle {cycle} spent ${this_cycle:.4f} > cap ${budget.cycle_usd:.4f}"
        )
    if total > budget.campaign_usd:
        raise MeristemError(
            f"campaign spent ${total:.4f} > cap ${budget.campaign_usd:.4f}"
        )
    if cycle_calls > budget.cycle_calls:
        raise MeristemError(
            f"cycle {cycle} made {cycle_calls} calls > cap {budget.cycle_calls}"
        )
    if total_calls > budget.campaign_calls:
        raise MeristemError(
            f"campaign made {total_calls} calls in the last {CAMPAIGN_WINDOW} cycles > cap {budget.campaign_calls}"
        )
