# Probe: selfcheck-runner runs each cycle

## What this probe asks

The selfcheck-runner organ is supposed to discover all multi-part organs in
`body/organs/`, invoke each organ's `selfcheck` op, and report pass/fail per
organ. This probe verifies that the runner actually executes — not that the
organs pass their selfchecks, but that the runner itself was invoked and
recorded a journal entry.

## How it scores

The rubric reads `state/journal.jsonl` from the workdir and queries for
entries with `kind` equal to `selfcheck-runner`. It determines the latest
cycle number from the journal, then checks whether at least one
`selfcheck-runner` entry exists within the last N cycles (N=5 by default,
configurable via `probe.json`).

- **Pass (score=100):** at least one `selfcheck-runner` journal entry exists
  in the last N cycles.
- **Fail (score=0):** no `selfcheck-runner` entry in the last N cycles,
  meaning the runner has silently stopped running or was never wired.

## Why this probe exists

G-009 tracks that the selfcheck-runner call site is not yet wired into the
loop. Once wired, there is no external guard that detects if the wiring
breaks — the runner could be silently dropped by an exception handler, a
refactor, or a lifecycle change, and nothing would notice. This probe is
that guard. It is the regression alarm for the class of failure where an
organ exists but its invocation is silently dropped.
