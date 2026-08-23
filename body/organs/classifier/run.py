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

This module runs inside an isolated worker with no inherited environment
variables, no filesystem access beyond its own stdin, no vault access, no
ledger access, and no network. It must not assume any of those are
available: it is a pure text -> label function and must stay that way.
"""
import json
import sys

# category -> substrings that identify it. Checked in this fixed order; the
# first category whose keyword list contains a substring of the (lowercased)
# input text wins. Each category's list is looked up independently of every
# other category's list -- there is no shared parsing/extraction helper
# behind this table, so growing one category's coverage cannot change any
# other category's behavior.
#
# ORDERING COUPLING -- measured, and deliberately left in place.
#
# First-match-wins means a careless widening CAN regress a passing check even
# though the lists themselves are independent. Measured example: adding the
# bare token "budget" to closure-budget fixes c1 and simultaneously steals c4
# from prompt-budget, because closure-budget is consulted first. Net score
# stays 40. Adding "closure" instead fixes c1 alone and scores 60.
#
# This is kept because it is what a real keyword table does, and because the
# loop already handles it: 40 -> 40 is judged `unfulfilled` against a declared
# score_increase, the reason lands in failure_history, and the seed retries.
#
# It is written down here for one reason. If P0-a shows no gradient across
# three cycles, the H1 falsification clause says to change model tier or
# change the ruler. Before concluding that, CHECK FOR THIS: a run that keeps
# landing on the coupled fix is an apparatus trap, not evidence about the
# seed. Diagnose by reading which keyword the candidate added.
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
