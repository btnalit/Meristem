# Proposals

Tasks the seed proposed for itself. A human promotes a line into
control/agenda.md; the loop never writes to the agenda directly.

- [ ] GROWTH (G-007): Implement the loop's own failure-class detection and pattern-register writing. state/patterns.md holds 23 entries (P-001..P-024) and git log shows zero commits authored by a cycle — every entry was human-written. The loop has the journal data and the failure-aggregator organ (once repaired) can supply per-task rejection aggregates, but nothing detects failure CLASSES (the P-NNN level of abstraction) and appends them to the register. Build a new organ body/organs/pattern-detector/ that reads the journal, clusters rejections by failure class (not just by task), and appends new P-NNN entries to state/patterns.md. Measuring stick: after N cycles, at least one entry in state/patterns.md is authored by a cycle, and the entry correctly identifies a failure class the human had not previously recorded. This is the self-detection metric the digest calls out as 'the first real test of Loop B'.
