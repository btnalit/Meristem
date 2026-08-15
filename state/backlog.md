# Improvement backlog

Structural improvements identified but not yet scheduled. Distinct from
`control/agenda.md` (what runs next) and `gaps.md` (what cannot yet be done).

- Move the vault seed generator out of the repository (gaps.md G-003).
- Measure Tier-A whole-file-replacement reliability once real cycles have run;
  build Tier B only if the data demands it.
- Record the golden-fixture catch rate per reviewer slot — that is the
  instrument for testing whether heterogeneous cheap review actually beats one
  strong reviewer. Right now that claim is a hypothesis wearing a config file.
- Track Tier-A/B/C resolution ratio (MCR): if the A share falls while kernel
  LOC stays flat, the kernel is getting harder to understand even though it is
  not getting bigger.

## BL-001 — Reviewers see diffs but not final file content

**Problem.** `review.build_prompt` includes the raw `git diff HEAD~1 HEAD` and
the closure file list (names only), but never the final content of the files
that actually changed. Under Tier A — whole-file replacement, the architecture's
default and only tier in P0 – `git diff` shows the entire old file as deletions
and the entire new file as additions. Every line appears changed, so a subtle
weakening (>= becoming >, a broadened except, a dropped assertion, a threshold
constant nudged by one) is buried in noise. The reviewer is asked to mentally
reconstruct the final state of each file from a diff that provides zero
signal-to-noise for whole-file replacement.

**Why it matters.** Principle 1 says a gate that cannot see the whole of what
it judges is not a gate. The review gate can see the diff, but for Tier A the
diff is the worst possible representation of the change: it shows everything as
changed, which means it shows nothing as changed. This is the specific failure
mode the project was founded to avoid – claiming coverage that is not real. The
reviewers are nominally checking the code, but they are actually checking a
representation that hides exactly the class of error (quiet relaxation) the
checklist's Section 1 exists to catch.

**Structural fix (not discipline).** Include the complete final content of
each changed file as a labelled section in the review prompt, alongside the
existing diff and closure list. Reviewers can then read the actual code they
are approving – the real thresholds, the real exception handlers, the real
gate logic – rather than reconstructing it from a diff that treats every line
as new. The changed-files list already exists in `build_prompt`; this extends
it from names to contents. The cost is prompt tokens proportional to changed
file size, which is bounded by the closure budget already in force.

**Blocked on.** Nothing structural – this is a change to
`meristem/gates/review.py`'s `build_prompt` and the loop's call to `review.run`,
reading the final file content from the worktree. It should be gated by a test
that verifies the final content of changed files appears in the prompt, and by
a golden fixture that confirms a subtle weakening (e.g., >= to >) is visible in
the prompt even when the diff is maximally noisy.

## BL-003 — text-stats does not subsume word-count (dependency prevents pruning)

**Judgement:** text-stats does NOT subsume word-count. Pruning word-count is
not safe at this time.

**Evidence:**
- text-stats declares `dependencies: ["word-count"]` in its organ.json,
  meaning it calls word-count over the germline ABI to obtain the word count
  rather than implementing its own word-counting logic.
- A dependent organ cannot subsume the organ it depends on: pruning
  word-count would break text-stats's dependency chain, and the germline
  validator would reject text-stats's manifest for referencing a non-active
  (archived) dependency.
- The utility figures confirm word-count has active utility: it is called by
  text-stats (transitively), so it is not idle.
- text-stats's output schema (words, chars, lines) is a superset of
  word-count's (words), but functional superset ≠ architectural subsumption.
  Subsumption requires that the subsuming organ can stand alone without the
  one it replaces.

**What would close this:** text-stats would need to implement its own
word-counting logic (or inline the same algorithm), remove `word-count` from
its dependencies list, and then word-count could be pruned through the
deprecating → archive lifecycle. Until then, word-count is load-bearing.

**Principle invoked:** Principle 3 (Growth requires metabolism) — every organ
carries a retirement path, but retirement must rest on evidence that nothing
depends on what is being retired.
