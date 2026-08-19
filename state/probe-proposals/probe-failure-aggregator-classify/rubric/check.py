#!/usr/bin/env python3
"""Rubric for probe-failure-aggregator-classify.

Verifies the failure-aggregator's classification function correctly
identifies known failure classes from representative rejection reasons.

Feeds one fixture per known class (gate-weakening, probe-regression,
closure-budget) through the classifier and asserts the returned class
matches the expected label.

Score: 100 when all classes are correctly identified.
        0 when any class is misclassified, unclassified, or the
          classifier cannot be invoked.

This probe scores by behaviour — it imports and calls the classify
function, then checks its output. It does not inspect source code.
"""

import json
import os
import sys
import pathlib

FIXTURES = [
    {
        "reason": (
            "Does this change weaken any gate, check, budget, or invariant? "
            "The diff removes the vault-reference invariant check from "
            "deterministic.py. This is a gate weakening: the check that "
            "prevents non-gate code from referencing the eval vault is "
            "being removed entirely."
        ),
        "expected": "gate-weakening",
    },
    {
        "reason": (
            "Regression on frozen probe 'probe-kernel-selftest': "
            "100.00 -> 85.00. The probe score dropped after this change, "
            "indicating a capability regression was introduced."
        ),
        "expected": "probe-regression",
    },
    {
        "reason": (
            "closure ~52000 > 50000 budget. The review closure exceeds "
            "the token budget. The mutation touches too many files for "
            "the closure to fit in one review context."
        ),
        "expected": "closure-budget",
    },
]


def main():
    payload = json.loads(sys.stdin.read())
    workdir = pathlib.Path(payload["workdir"]).resolve()
    organ_dir = workdir / "body" / "organs" / "failure-aggregator"

    if not organ_dir.is_dir():
        print(json.dumps({
            "score": 0.0,
            "detail": "failure-aggregator organ not found in workdir"
        }))
        return

    # Import the classify module from the organ's directory and invoke
    # its function. This tests behaviour (does the function return the
    # right class?) not source inspection (does the source contain
    # certain strings?). Setting cwd ensures any relative imports or
    # data paths in the module resolve correctly.
    sys.path.insert(0, str(organ_dir))
    old_cwd = os.getcwd()
    os.chdir(str(organ_dir))
    try:
        import importlib
        mod = importlib.import_module("classify")
        fn = getattr(mod, "classify", None)
        if fn is None:
            fn = getattr(mod, "_classify", None)
        if fn is None:
            print(json.dumps({
                "score": 0.0,
                "detail": "no classify or _classify function found in classify.py"
            }))
            return

        results = []
        all_correct = True
        for fixture in FIXTURES:
            result = fn(fixture["reason"])
            if isinstance(result, dict):
                actual = result.get("class", result.get("label", ""))
            else:
                actual = str(result).strip()
            results.append((fixture["expected"], actual))
            if actual != fixture["expected"]:
                all_correct = False

        score = 100.0 if all_correct else 0.0
        detail = "; ".join(
            f"expected={e} got={a}" for e, a in results
        )
        if not all_correct:
            detail = "MISCLASSIFICATION: " + detail

        print(json.dumps({"score": score, "detail": detail}))
    except Exception as exc:
        print(json.dumps({
            "score": 0.0,
            "detail": f"classifier invocation failed: {type(exc).__name__}: {exc}"
        }))
    finally:
        os.chdir(old_cwd)
        sys.path.pop(0)


if __name__ == "__main__":
    main()
