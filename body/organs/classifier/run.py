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

# category -> substrings that identify it. The current implementation checks
# categories in a fixed order and returns the first substring match. This is a
# deliberately small baseline, not the long-term organ contract.
#
# A future mutation that handles anchor A4 must choose the category whose
# condition actually blocks the gate; a condition explicitly described as
# under-budget/okay must not win merely because its keyword appears first.
# Until such a mutation is accepted, first-match-wins remains measurable and
# may score A4 as unclassified.
#
# ORDERING COUPLING -- measured as a baseline limitation, not a specification
# guarantee.
#
# First-match-wins means a careless widening CAN regress a passing check even
# though the lists themselves are independent. Measured example: adding the
# bare token "budget" to closure-budget fixes c1 and simultaneously steals c4
# from prompt-budget, because closure-budget is consulted first. Net score
# stays 40. Adding "closure" instead fixes c1 alone and scores 60.
#
# The baseline is intentionally retained to give the seed a visible,
# reproducible gradient. The successful A4 contract is the root-cause rule
# above, not preservation of dictionary order.
KEYWORD_TABLE = {
    "protected-path": [
        "substrate/", "soil/", "state/", "vault",
    ],
    "closure-budget": [
        "closure over budget", "closure exceeds cap",
    ],
    "prompt-budget": [
        "prompt surface", "prompt budget",
    ],
    "contract-budget": [
        "contract surface", "contract budget",
    ],
    "probe-regressed": [
        "probe regressed", "internal regressed",
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
