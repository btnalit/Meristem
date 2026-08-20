"""Cross-task failure classification.

Classifies rejection reasons into systemic failure classes that can appear
across multiple different tasks. This complements the per-task classifier
in the failure-aggregator organ: per-task classification asks 'why does THIS
task keep failing?', while cross-task classification asks 'why do DIFFERENT
tasks keep failing the same way?'

A systemic pattern is not 'this task was rejected 3 times for the same
reason' -- that is per-task aggregation's job. A systemic pattern is
'DIFFERENT tasks, asking for different things, all hit the same wall'.
That distinction is what makes a SYS-NNN entry worth a separate register
prefix from the per-task P-NNN entries.
"""
from __future__ import annotations

import re

#: Each class maps to a list of compiled patterns. The first match wins,
#: so order within a class does not matter but order across classes does:
#: more specific classes should appear before more general ones when
#: patterns could overlap.
SYSTEMIC_CLASSES: dict[str, list[re.Pattern]] = {
    "kernel-over-cap": [
        re.compile(r"kernel.{0,20}(over|exceed).{0,20}(cap|3000)", re.I),
        re.compile(r"(over|exceed).{0,20}(cap|3000).{0,20}kernel", re.I),
        re.compile(r"kernel.{0,10}\d{4}.{0,10}(over|cap|exceed)", re.I),
        re.compile(r"LOC.{0,20}(over|exceed).{0,20}(cap|3000)", re.I),
        re.compile(r"kernel_loc.{0,20}(over|>|exceed)", re.I),
        re.compile(r"over.the.\d+.cap", re.I),
        re.compile(r"line.cap.{0,10}(over|exceed)", re.I),
    ],
    "closure-budget": [
        re.compile(r"closure.{0,20}(over|exceed|budget)", re.I),
        re.compile(r"(over|exceed).{0,20}closure", re.I),
        re.compile(r"closure.{0,10}\d{4,6}.{0,10}(over|budget|exceed)", re.I),
        re.compile(r"review.context.{0,20}(over|exceed|budget|fit)", re.I),
    ],
    "gate-weakening": [
        re.compile(r"gate.{0,20}(weaken|narrow|disable|remove|relax)", re.I),
        re.compile(r"(weaken|narrow|disable|remove|relax).{0,20}gate", re.I),
        re.compile(r"immune.{0,20}(weaken|bypass|circumvent)", re.I),
    ],
    "rate-limit": [
        re.compile(r"\b429\b"),
        re.compile(r"rate.limit", re.I),
        re.compile(r"too.many.requests", re.I),
        re.compile(r"quota.{0,10}(exhaust|exceed|limit)", re.I),
    ],
    "probe-regression": [
        re.compile(r"probe.{0,20}(regress|fail|score.{0,10}drop)", re.I),
        re.compile(r"(regress|fail).{0,20}probe", re.I),
    ],
    "protected-path": [
        re.compile(r"protected.path", re.I),
        re.compile(r"(root|substrate).{0,10}(touch|forbidden|protected)", re.I),
    ],
    "secret-leak": [
        re.compile(r"secret.{0,10}(found|detect|leak)", re.I),
        re.compile(r"(api.key|token|credential).{0,10}(found|detect|leak)", re.I),
    ],
    "memory-integrity": [
        re.compile(r"(append.only|register).{0,20}(erase|drop|lose|lost)", re.I),
        re.compile(r"memory.integrity", re.I),
    ],
    "undeclared-dependency": [
        re.compile(r"undeclared.dependency", re.I),
        re.compile(r"dependency.{0,10}(missing|undeclared|absent)", re.I),
    ],
}


def classify(reason: str) -> str:
    """Classify a rejection reason into a systemic failure class.

    Returns the class name, or 'unclassified' if no pattern matches.
    The classifier is deliberately conservative: a reason that does not
    match any known pattern is 'unclassified', not silently dropped.
    Unclassified rejections are still counted in the aggregate but do
    not contribute to a systemic pattern -- a pattern with no class is
    noise, not signal.
    """
    for cls, patterns in SYSTEMIC_CLASSES.items():
        for pat in patterns:
            if pat.search(reason):
                return cls
    return "unclassified"
