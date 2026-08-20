# Probe: selfcheck consumption

## Capability being tested

When the selfcheck-runner reports a failure for an organ, the loop must
take observable action. The failure must be recorded in the journal, and
the loop must respond — either by parking the failing organ or by
surfacing the failure class to state/patterns.md.

A selfcheck failure that is detected but never acted upon is a silent
immune gap: the runner saw the problem and nothing happened. This probe
verifies the consumption half of the selfcheck loop — not that the runner
executes, but that its failure signal drives loop behavior.

## Measuring stick

The probe passes only when selfcheck failures produce observable loop
behavior — not merely when the runner executes. A cycle where the
selfcheck-runner reports a failure but the loop takes no action must
fail this probe.

## Why the rubric is not staged here

This probe tests immune behavior. Its scoring logic must not be visible
to the mutation engine, which sees the entire repository including this
staging area. If the engine could read how this probe scores, it could
tune future proposals to pass the probe without actually closing the
consumption gap.

The rubric is authored directly in the vault by the human, outside the
repository and worktree. This staging proposal records the intent and
the capability being tested; the vault holds the scoring logic.
