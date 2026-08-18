# pattern-detector

Detects failure classes from journal rejection data and appends new P-NNN
entries to `state/patterns.md`.

This is the loop's own failure-class detection mechanism (G-007). Unlike the
failure-aggregator, which groups rejections by task text, this organ clusters
them by failure CLASS — the P-NNN level of abstraction. A class like
"kernel-loc-cap-exceeded" may manifest across many different tasks; detecting
the class rather than the instance is what makes this Loop B (growth) rather
than Loop A (optimisation).

## How it works

1. Reads `state/journal.jsonl` for cycle records with `outcome: "rejected"`,
   fault records, and canary rejections.
2. Extracts rejection reasons (overall reason + individual reviewer reasons).
3. Classifies each reason using keyword-based classifiers in `classify.py`.
4. Clusters rejections by failure class.
5. Checks `state/patterns.md` for already-recorded classes (via `**Class:**`
   tags).
6. Appends new P-NNN entries for classes not yet recorded, with a minimum
   threshold of 2 instances.

## Classification

Classification is deterministic (no model calls). Each classifier maps
rejection reason text to a failure class using regex patterns. The
classifiers are ordered: specific patterns before general ones, so
"kernel-loc-cap-exceeded" matches before "deterministic-gate-failure".

## Measuring stick

After N cycles, at least one entry in `state/patterns.md` is authored by a
cycle (tagged `**Detected by:** pattern-detector organ`), and the entry
correctly identifies a failure class the human had not previously recorded.

## Retirement path

When the loop's own reflect step can identify and write failure classes
without this organ, the organ can be pruned. Until then, it is the mechanism
by which Principle 2's "detecting one's own failure classes is a duty"
becomes operational rather than aspirational.
