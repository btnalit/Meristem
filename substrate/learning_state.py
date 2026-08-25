"""Soil-owned identity and fault taxonomy for one learning attempt."""
from __future__ import annotations

import re
import uuid
from typing import Any

_ATTEMPT_RE = re.compile(r"^att-[0-9a-f]{32}$")
FAULT_CLASSES = frozenset({
    "provider_error", "rate_limited", "gateway_error", "worker_error",
    "path_violation", "empty_mutation", "prompt_over_budget",
    "measurement_error", "repeated_strategy", "unfulfilled", "fulfilled",
    "no_candidate", "unknown",
})
TASK_STATES = frozenset({
    "open", "in_progress", "fulfilled", "unfulfilled", "parked",
    "blocked", "needs_reframing",
})

#: `cycle` row `failure_reason` values that are an environment/mechanism
#: fault, not a property of the task itself. Single source of truth (P2-7):
#: `substrate/task_state.py` imports this rather than keeping its own copy.
MECHANISM_FAILURE_REASONS = frozenset({
    "provider_error", "rate_limited", "gateway_error",
    "worker_error", "measurement_error", "prompt_over_budget",
})


def new_attempt_id() -> str:
    return "att-" + uuid.uuid4().hex


def validate_attempt_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ATTEMPT_RE.fullmatch(value))
