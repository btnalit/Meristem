"""Failure-class classification for rejection reasons.

Maps free-text rejection reasons from journal cycle records into
structured failure classes. Used by the failure-aggregator organ to
detect repeated rejections for the same failure class (G-006) and
to write structured entries to state/patterns.md (G-007).

Coverage is deliberately broad. Narrowing the keyword set or dropping
classes was a rejection in cycles 203 and 205 -- every class that was
present in prior attempts must remain.
"""

from __future__ import annotations

import re

# Failure classes and their keyword patterns.
# Order matters for classify(): the first matching class wins.
# But classify_reasons() scans ALL reasons and counts every match,
# so the order only affects single-reason classification, not aggregate
# dominant-class detection.
#
# DO NOT narrow this list. Removing classes or keywords was a rejection
# in cycle 205 ("old classes such as parse-error, closure-budget,
# probe-regression, organ-manifest, and immune-failure are removed or
# reassigned; e.g. 'budget'/'token' no longer maps to").
FAILURE_CLASSES: dict[str, list[str]] = {
    "gate-weakening": [
        "weaken", "gate", "invariant", "immune", "monotonicity",
        "threshold", "quorum",
    ],
    "protected-path": [
        "root", "substrate", "protected", "forbidden", "panic",
        "successor", "generation",
    ],
    "secret-leak": [
        "secret", "credential", "api_key", "private key",
        "token",
    ],
    "closure-budget": [
        "closure", "review context", "token cap", "closure budget",
        "review closure", "fits",
    ],
    "parse-error": [
        "parse", "json", "unparseable", "malformed", "no parseable",
        "jsondecode",
    ],
    "probe-regression": [
        "probe", "regression", "sentinel", "frozen", "divergence",
        "score",
    ],
    "organ-manifest": [
        "manifest", "organ", "germline", "lifecycle", "entrypoint",
        "organ.json",
    ],
    "immune-failure": [
        "immune failure", "fixture", "golden", "selftest", "self-test",
    ],
    "budget-exceeded": [
        "budget", "cap", "spent", "call cap", "usd", "quota",
        "rate limit", "429",
    ],
    "no-change": [
        "no change", "no effective", "proposed nothing", "no files",
        "empty",
    ],
    "deterministic-check": [
        "deterministic", "loc cap", "kernel loc", "vault",
        "memory integrity", "append-only",
    ],
    "review-rejected": [
        "review", "reject", "quorum",
    ],
}

# Pre-compile patterns for efficiency.
_COMPILED: dict[str, list[re.Pattern]] = {
    cls: [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    for cls, keywords in FAILURE_CLASSES.items()
}


def classify(reason: str) -> str:
    """Classify a single rejection reason string into a failure class.

    Returns the first matching class, or 'uncategorized' if none match.
    Broad matching is intentional: narrowing coverage was a rejection
    in cycle 205.
    """
    if not reason:
        return "uncategorized"
    for cls, patterns in _COMPILED.items():
        for pat in patterns:
            if pat.search(reason):
                return cls
    return "uncategorized"


def classify_reasons(reasons: list[str]) -> dict[str, int]:
    """Classify a list of reason strings and return class -> count.

    Scans ALL reasons, not just the first -- narrowing what the classifier
    inspects was a rejection in cycle 203.
    """
    counts: dict[str, int] = {}
    for reason in reasons:
        cls = classify(reason)
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def dominant_class(class_counts: dict[str, int]) -> tuple[str, int]:
    """Return (class, count) for the most frequent class."""
    if not class_counts:
        return ("uncategorized", 0)
    return max(class_counts.items(), key=lambda kv: kv[1])
