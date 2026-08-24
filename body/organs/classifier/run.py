#!/usr/bin/env python3
"""classifier organ -- P0-a ignition organ.

Capability: maps a failure-reason text to a named category.

ABI (subprocess, stdin/stdout, strict JSON -- spec C6 "minimal integrity
isolation"):

    invoked as : python run.py           (no argv used)
    stdin      : one JSON object   {"input": "<failure reason text>"}
    stdout     : one JSON object   {"category": "<label>"}   on success
    exit code  : 0 on success

    On any malformed stdin (not valid JSON, missing "input", or "input" not
    a string): nothing is written to stdout and the process exits 1. A
    caller must treat "no parseable JSON on stdout" as `unmeasured`, never
    as a classification result -- see spec 15.6 ("non-legal output ->
    unmeasured, not a score of 0").

This module is designed as a pure text -> label function and must not rely on
filesystem, vault, ledger, credentials, or network access. The current P0-a
experiment may leave a worker network path available to measure escape/side
capability, but that is an execution-policy risk and is not part of this
organ's classification contract.
"""
import json
import sys

# Decision-rule hypothesis: contract-budget and protected-path have sufficient
# surface cues to be distinguished before the open-ended "budget" / "state" terms.
#
# The old first-match-wins design meant that broad tokens such as "budget"
# leaked into closure-budget and swallowed contract-budget. The new rule adds
# *specific* high-signal phrases for both categories and intentionally omits
# the bare tokens that caused the overlap.
#
# Classification order is still a heuristic — it only matters when two rules
# genuinely conflict, which is uncommon once the above leakage is removed.
KEYWORD_TABLE = {
    "protected-path": [
        "substrate/",
        "soil/",
        "state/",
        "vault",
        "protected path",
        "vault access",
    ],
    "closure-budget": [
        "closure over budget",
        "closure exceeds cap",
    ],
    "prompt-budget": [
        "prompt surface",
        "prompt budget",
    ],
    "contract-budget": [
        "contract surface",
        "contract budget",
        "contract budget exceeded",
        "out of contract budget",
    ],
    "probe-regressed": [
        "probe regressed",
        "internal regressed",
    ],
}

DEFAULT_CATEGORY = "unclassified"


def classify(text: str) -> str:
    lowered = text.lower()
    for category, keywords in KEYWORD_TABLE.items():
        for kw in keywords:
            if kw in lowered:
                return category
    return DEFAULT_CATEGORY


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        text = payload["input"]
        if not isinstance(text, str):
            raise TypeError("input must be a string")
    except Exception:
        return 1
    sys.stdout.write(json.dumps({"category": classify(text)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
