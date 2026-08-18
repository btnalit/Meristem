"""Failure-class classification for the pattern-detector organ.

Each classifier maps rejection reason text to a failure class. The classes
are deliberately coarse-grained -- they represent structural failure modes,
not individual symptoms. A class like "kernel-loc-cap-exceeded" may appear
across many different tasks; detecting the class is the point.

Classification is keyword-based, not model-based. This is deliberate:
- A model call to classify failures would be expensive and slow.
- The classifier itself would be subject to the failure modes it detects.
- Deterministic classification is reviewable: a reviewer can read the
  patterns and verify they match the reasons.
- The classifier can be extended by adding new entries to CLASSIFIERS,
  which is itself a structural improvement (meta-over-patch).

Ordering: specific classifiers before general ones. "kernel-loc-cap-exceeded"
must be checked before "deterministic-gate-failure" (a catch-all), so that
"deterministic: kernel is 3016 lines..." matches the specific class.
"""

import re


CLASSIFIERS = [
    {
        "class": "kernel-loc-cap-exceeded",
        "description": "Kernel exceeded its line cap",
        "patterns": [r"kernel is \d+ lines, over the \d+ cap"],
    },
    {
        "class": "closure-budget-exceeded",
        "description": "Review closure exceeded token budget",
        "patterns": [r"closure ~\d+ > \d+ budget"],
    },
    {
        "class": "protected-path-violation",
        "description": "Mutation touched protected paths (root/ or substrate/)",
        "patterns": [r"touches protected path"],
    },
    {
        "class": "output-budget-exhausted",
        "description": "Engine proposed no files -- output budget exhausted",
        "patterns": [r"engine proposed no files", r"output budget exhausted"],
    },
    {
        "class": "undeclared-dependency",
        "description": "Undeclared dependency found in closure",
        "patterns": [r"undeclared dependency"],
    },
    {
        "class": "vault-reference-leak",
        "description": "Ordinary kernel code referenced the vault",
        "patterns": [r"vault-reference invariant"],
    },
    {
        "class": "secret-detected",
        "description": "Possible secret found in diff",
        "patterns": [r"possible secret"],
    },
    {
        "class": "memory-erasure",
        "description": "Append-only register lost entries",
        "patterns": [r"drops append-only entries"],
    },
    {
        "class": "organ-manifest-invalid",
        "description": "Organ manifest failed validation",
        "patterns": [
            r"organ.*has no readable organ\.json",
            r"organ.*missing required field",
            r"organ.*lifecycle must be",
        ],
    },
    {
        "class": "probe-regression",
        "description": "Probe score regressed or did not improve",
        "patterns": [r"regression on frozen probe", r"probes:"],
    },
    {
        "class": "review-rejection",
        "description": "Reviewers rejected the change",
        "patterns": [r"review rejected"],
    },
    {
        "class": "gate-weakening-flagged",
        "description": "Reviewer flagged a gate weakening",
        "patterns": [r"weakens.*gate", r"gate.*weaken"],
    },
    {
        "class": "canary-rejection",
        "description": "Canary boot rejected the candidate",
        "patterns": [r"canary", r"REFUSED", r"tests failed"],
    },
    {
        "class": "engine-fault-rate-limit",
        "description": "Engine faulted due to rate limiting (HTTP 429)",
        "patterns": [r"HTTP Error 429", r"429", r"Too Many Requests"],
    },
    {
        "class": "engine-fault-parse-error",
        "description": "Engine returned unparseable output",
        "patterns": [r"no parseable JSON", r"unparseable"],
    },
    {
        "class": "engine-fault-empty-content",
        "description": "Engine returned empty content (reasoning consumed budget)",
        "patterns": [r"empty content", r"reasoning consumed the budget"],
    },
    {
        "class": "engine-fault-truncation",
        "description": "Engine reply was truncated at token limit",
        "patterns": [r"reply truncated", r"finish_reason=length"],
    },
    {
        "class": "deterministic-gate-failure",
        "description": "Deterministic gate failed (unclassified)",
        "patterns": [r"deterministic:"],
    },
]

FAILURE_CLASSES = {c["class"]: c for c in CLASSIFIERS}


def classify_rejection(reason):
    """Classify a rejection reason into a failure class.

    Returns the class name string, or None if no classifier matches.
    The first matching classifier wins, so order matters: more specific
    classifiers should come before more general ones.
    """
    if not reason:
        return None
    for classifier in CLASSIFIERS:
        for pattern in classifier["patterns"]:
            if re.search(pattern, reason, re.IGNORECASE):
                return classifier["class"]
    return None
