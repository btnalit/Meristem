# sys-pattern-detector

Detects systemic failure classes by aggregating rejection reasons across
different tasks. Complements the per-task failure-aggregator: that organ
asks "why does THIS task keep failing?", while this one asks "why do
DIFFERENT tasks keep failing the same way?"

When the same failure class (e.g., kernel-over-cap, closure-budget,
gate-weakening) appears across multiple distinct tasks, it is a systemic
issue and deserves a SYS-NNN entry in state/patterns.md.

## Operations

- `aggregate`: read the journal, detect cross-task patterns, append new
  SYS-NNN entries to state/patterns.md.
- `selfcheck`: exercise classification and detection with known fixtures.

## Measuring stick

The selfcheck verifies that the detector correctly identifies the
'kernel over 3000 cap' pattern across tasks G-002 and G-001 (cycles
172-185). Synthetic journal entries for both tasks, rejected for
kernel-over-cap, are fed through the full pipeline and the output is
asserted to contain the class with both tasks present.

## Classification

Rejection reasons are classified into systemic classes:

- `kernel-over-cap`: kernel LOC exceeds the 3000-line cap
- `closure-budget`: review closure exceeds the 50000-token budget
- `gate-weakening`: change weakens a gate, check, or invariant
- `rate-limit`: API rate limit or quota exhaustion
- `probe-regression`: probe score dropped below baseline
- `protected-path`: mutation touched root/ or substrate/
- `secret-leak`: possible secret detected in the diff
- `memory-integrity`: append-only register entries were erased
- `undeclared-dependency`: dependency edge not in manifest

## Retirement path

If the per-task failure-aggregator grows to include cross-task detection,
this organ can be archived. Its classification patterns would be
internalized into the aggregator's classify module.
