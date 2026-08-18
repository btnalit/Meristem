# failure-aggregator

Queries the journal for rejection records grouped by task text, classifies
rejection reasons into structured failure classes, and appends entries to
`state/patterns.md` when a task has been rejected 2+ times. Emits graded
signals (surface/escalate/block at 2/3/4 rejections) that the kernel can
use for circuit-breaker input (G-006) and self-detection (G-007).
