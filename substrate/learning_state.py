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


def new_attempt_id() -> str:
    return "att-" + uuid.uuid4().hex


def validate_attempt_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ATTEMPT_RE.fullmatch(value))


def classify_attempt(*, failure_reason: str | None,
                      provider_status: str | None,
                      candidate: bool,
                      measured: bool = False,
                      fulfilled: bool = False) -> dict[str, str]:
    reason = failure_reason or ""
    if provider_status == "deferred" or reason == "rate_limited":
        mechanism = "rate_limited"
        task = "blocked"
        fault = "rate_limited"
    elif provider_status == "refused" or reason in {"provider_error", "gateway_error"}:
        mechanism = reason if reason in {"provider_error", "gateway_error"} else "gateway_error"
        task = "blocked"
        fault = mechanism
    elif reason in {"measurement_error", "unmeasured"} or (candidate and not measured):
        mechanism = "measurement_error"
        task = "blocked"
        fault = "measurement_error"
    elif fulfilled:
        mechanism = "healthy"
        task = "fulfilled"
        fault = "fulfilled"
    elif candidate:
        mechanism = "healthy"
        task = "unfulfilled"
        fault = "unfulfilled"
    else:
        mechanism = "healthy"
        task = "no_candidate"
        fault = reason if reason in FAULT_CLASSES else "no_candidate"
    return {"mechanism_status": mechanism, "task_status": task, "fault_class": fault}
