# Probe: failure-aggregator classification

Feed representative rejection reasons (one per known failure class) through
the failure-aggregator's classify function and assert the returned class
matches the expected label.

## Known classes

- **gate-weakening**: rejection reasons mentioning removed, narrowed, or
  disabled checks; loosened thresholds, caps, quorums, or budgets; weakened
  gates or invariants.
- **probe-regression**: rejection reasons mentioning frozen probe score
  drops or capability regressions detected by probes.
- **closure-budget**: rejection reasons mentioning closure exceeding the
  token budget or review closure not fitting in one review context.

## Scoring

- 100: all three classes correctly identified
- 0: any class misclassified, unclassified, or classifier not invocable

No partial credit. Classification is binary: a class is either correct or
it is not. This is the measuring stick that should have existed before the
classifier was written — had it existed, the cycle 206 breakage (FA-019)
would have scored 0 immediately rather than remaining invisible.
