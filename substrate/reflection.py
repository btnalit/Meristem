"""Bounded, explicitly non-authoritative reflection facts."""
from __future__ import annotations


def build_reflection(facts: dict) -> dict:
    attempts = facts.get("recent_attempts", []) if isinstance(facts, dict) else []
    syntax_failures = [a for a in attempts if a.get("reason") == "syntax_failure"]
    repeated = [a for a in attempts if a.get("diagnosis_class") == "repeated_strategy_no_effect"]
    cycles = [a.get("soil_cycle") for a in attempts if a.get("soil_cycle") is not None]
    if syntax_failures:
        hypothesis = "candidate_syntax_failure_prevented_measurement"
        next_strategy = "preserve valid Python syntax and run a compile-safe minimal mutation before changing strategy"
    elif repeated:
        hypothesis = "repeated_strategy_is_not_improving_primary_probe"
        next_strategy = "select a materially different target scope and implementation approach"
    elif attempts:
        hypothesis = "current_strategy_has_insufficient_evidence"
        next_strategy = "form a falsifiable alternative hypothesis before the next mutation"
    else:
        hypothesis = "insufficient_evidence"
        next_strategy = "collect one bounded attempt before changing strategy"
    return {
        "schema_version": 1,
        "facts": ["soil_projection_only"],
        "hypothesis": hypothesis,
        "next_strategy": next_strategy,
        "confidence": "low",
        "source_cycles": cycles[-8:],
        "source_attempt_ids": [x for x in facts.get("source_attempt_ids", []) if x][-8:],
        "source_ledger_tail_hash": facts.get("source_ledger_tail_hash"),
        "authoritative": False,
    }
