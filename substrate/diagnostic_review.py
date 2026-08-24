"""Non-authoritative soil diagnostic classification; never promotes."""
from __future__ import annotations


def diagnose_failure(*, failure_class: str, changed_paths: list[str],
                     repeated_strategy: bool = False,
                     delta: float | None = None) -> dict:
    if failure_class in {"provider_error", "rate_limited", "gateway_error",
                         "worker_error", "measurement_error"}:
        diagnosis = "mechanism_failure"
        mechanism = "unhealthy"
        constraint = "wait for healthy soil mechanism before interpreting model ability"
    elif failure_class == "path_violation":
        diagnosis = "mutation_contract_failure"
        mechanism = "healthy"
        constraint = "return a path-to-content map without wrapper keys"
    elif failure_class == "syntax_failure":
        diagnosis = "model_or_strategy_failure"
        mechanism = "healthy"
        constraint = "return syntactically valid Python before changing strategy"
    elif repeated_strategy and delta == 0.0:
        diagnosis = "repeated_strategy_no_effect"
        mechanism = "healthy"
        constraint = "choose a materially different strategy and target scope"
    elif failure_class == "delta_below_threshold":
        diagnosis = "model_or_strategy_failure"
        mechanism = "healthy"
        constraint = "change the classifier decision rule and target a falsifiable primary-probe improvement"
    elif failure_class in {"unfulfilled", "delta_below_threshold", "no_candidate", "empty_mutation"}:
        diagnosis = "model_or_strategy_failure"
        mechanism = "healthy"
        constraint = "form a falsifiable alternative hypothesis before retry"
    else:
        diagnosis = "unclassified"
        mechanism = "unknown"
        constraint = "collect more bounded evidence"
    return {
        "diagnosis_class": diagnosis,
        "mechanism_status": mechanism,
        "next_experiment_constraint": constraint,
        "promotion_authority": False,
        "changed_path_families": sorted(set(changed_paths)),
    }
