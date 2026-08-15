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


def load_budget() -> Budget:
    try:
        with open(CONTROL / "models.toml", "rb") as handle:
            data = tomllib.load(handle).get("budget", {})
    except FileNotFoundError:
        data = {}
    return Budget(
        cycle_usd=float(data.get("cycle_usd", 1.0)),
        campaign_usd=float(data.get("campaign_usd", 25.0)),
    )


def price(slot_id: str, models: dict) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for a slot; zeros when unpriced."""
    for role in models.get("roles", {}).values():
        for slot in role.get("slots") or [role]:
            if slot.get("id") == slot_id or slot.get("model") == slot_id:
                return float(slot.get("usd_in", 0.0)), float(slot.get("usd_out", 0.0))
    return 0.0, 0.0


def record(cycle: int, role: str, completion, models: dict | None = None) -> float:
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
            "usd": round(cost, 6),
        },
    )
    return cost


def spent(cycle: int | None = None) -> float:
    """Total USD spent -- for one cycle when given, otherwise all time."""
    total = 0.0
    for row in read_jsonl(JOURNAL):
        if row.get("kind") != "usage":
            continue
        if cycle is not None and row.get("cycle") != cycle:
            continue
        total += float(row.get("usd", 0.0))
    return total


def check(cycle: int, budget: Budget | None = None) -> None:
    """Deterministic budget gate. Raises when a cap is exceeded."""
    budget = budget or load_budget()
    this_cycle = spent(cycle)
    if this_cycle > budget.cycle_usd:
        raise MeristemError(
            f"cycle {cycle} spent ${this_cycle:.4f} > cap ${budget.cycle_usd:.4f}"
        )
    total = spent()
    if total > budget.campaign_usd:
        raise MeristemError(
            f"campaign spent ${total:.4f} > cap ${budget.campaign_usd:.4f}"
        )
