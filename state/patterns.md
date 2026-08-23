# Pattern Register

The memory of Principle 2 (Meta-over-Patch). Not a bug list — a register of
**failure classes**, their counts, and the structural fix that made each one
impossible.

Before closing any failure, check here. A known pattern escalates to the
architectural level immediately; a second instance of the same class means the
first fix was at the wrong depth.

The class test, applied to every entry: *had this fix existed six months ago,
could today's failure still have arrived through a different surface?*

When a class recurs, the right move is Loop B — write a probe that becomes its
permanent sentinel, then fix the structure.

---

## FA-003 — Repeated rejection: probe-regression
- **Task:** Add a `utility` command to meristem/loop.py that reads state/journal.jsonl and p
- **Failure class:** probe-regression
- **Count:** 2 rejection(s)
- **Representative reasons:**
  - cycle 88: probes: regression on frozen probe 'probe-kernel-selftest': 100.00 -> 0.00
- **Detected at cycle:** 202


## P-030 — Immune-behavior probe rubrics must not be staged visibly

**Class:** A probe that verifies the immune system's own behavior (e.g.,
selfcheck consumption, gate firing, failure escalation) has its scoring
logic staged in state/probe-proposals/<id>/rubric/check.py, which is
visible to the mutation engine. The engine can read the exact pass/fail
conditions and tune future proposals to pass the probe without actually
fixing the underlying immune gap.

**Root cause:** The probe staging protocol allows rubric/check.py in the
staging area, which is visible to the engine. This is acceptable for
capability probes (the engine cannot fake capability), but dangerous for
immune-behavior probes (the engine can fake the appearance of immune
function by satisfying the visible rubric without closing the actual gap).

**Structural fix:** Immune-behavior probes stage only the statement
(what is being tested) in the repository. The rubric (how it is scored)
is authored directly in the vault by the human, outside the repository
and worktree. The staging proposal notes this explicitly.

**Evidence:** cycles 292–293 rejected probe-selfcheck-consumption twice
for the same failure class: first for staging rubric/check.py, then for
embedding the rubric logic in task.md prose. Both were the same class:
scoring logic visible to the engine.

## G-006 — Repeated rejection circuit breaker

When a task is rejected 3 consecutive times (judged rejections, not faults),
the circuit breaker parks it: the task is set aside in state/mailbox.md
until a human clears it, and a pattern entry is appended here with the
rejection reasons aggregated from failure_history() across cycles.

This is a stopgap. The full externalization (body/organs/circuit-breaker/)
will own rejection aggregation, per-task retry counting, and escalation
policy outside the kernel.

## G-006 — Repeated rejection

Task: REPAIR (P-030 enforcement): Scan state/probe-proposals/ for any rubric/check.py files in immune-behavior probes and remove them, moving rubric logic to the vault.
Rejected 5 times.
Previous attempts at this exact task were rejected:
- cycle 331: deterministic: kernel is 3002 lines, over the 3000 cap; closure ~50004 > 50000 budget. Kernel+control ~39156 always counted. Droppable: state/patterns.md ~9948, control/probe-protocol.md ~888, state/decisions.jsonl ~12
- cycle 333: deterministic: kernel is 3002 lines, over the 3000 cap
- cycle 334: review:deepseek: reviewer unavailable: review:deepseek failed after 4 attempts: empty content (finish_reason=length); raise max_tokens -- reasoning consumed the budget

Study these objections carefully. Do NOT repeat these mistakes.

## G-006 — Repeated rejection

Task: REPAIR (breaker counting): A reviewer that never read the proposal still casts a rejection vote, and that vote is counted as a judged rejection by the circuit breaker. Evidence: at cycle 334 review:de
Rejected 5 times.
Previous attempts at this exact task were rejected:
- cycle 336: review:deepseek: rejections_for() now excludes cycles whose rejected_by votes are all 'reviewer unavailable', so the circuit breaker no longer counts three such cycles toward parking. That loosens the park threshold: a state the old code caught (task parked after three unavailability-rejections) now continues silently.
- cycle 336: review:deepseek: The new unavail_only() predicate is keyed to the textual 'reviewer unavailable' prefix, adding a narrow but real bypass: any rejection whose reason begins with that prefix is erased from the judged-rejection count, reducing defence-in-depth.
- cycle 336: review:deepseek: Independent of gate weakening, the journal.py change corrupts the module docstring: `""Journal-reading...` is not a valid Python string opener, so meristem/journal.py cannot be imported and the diff does not run.
- cycle 339: review:deepseek: The breaker is a budget/threshold: a task is parked after 3 judged re

## FA-unclassified — Repeated rejection

Task: Add ONE function to body/organs/memory-graph/main.py named pipeline_check(workdi
Class: unclassified
Rejected 3 times.

## FA-secret-leak — Repeated rejection

Task: Add a `report` command to meristem/loop.py that writes REPORT.md at the reposito
Class: secret-leak
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: Add a golden fixture to meristem/loop.py golden_fixtures() covering a failure cl
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: Add an op "explain" to body/organs/memory-graph/main.py taking args {"id": "..."
Class: unclassified
Rejected 2 times.

## FA-unclassified — Repeated rejection

Task: Append one new capability gap to state/gaps.md that you observed while running, 
Class: unclassified
Rejected 10 times.

## FA-unclassified — Repeated rejection

Task: Change the cap. per-file: meristem/loop.py 904, meristem/journal.py 306, meriste
Class: unclassified
Rejected 4 times.

## FA-gate-weakening — Repeated rejection

Task: EXTERNALIZE: Move generate_report() formatting logic from meristem/loop.py into 
Class: gate-weakening
Rejected 2 times.

## FA-deterministic-check — Repeated rejection

Task: EXTERNALIZE: Move generate_report() from meristem/loop.py into a new organ body/
Class: deterministic-check
Rejected 2 times.

## FA-unclassified — Repeated rejection

Task: EXTERNALIZE: Move journal aggregation and task lifecycle (take_task, done_tasks,
Class: unclassified
Rejected 2 times.

## FA-unclassified — Repeated rejection

Task: Externalize the circuit breaker for repeated review rejections (G-006) into a ne
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: Externalize the failure-history aggregation and pattern-class detection logic fr
Class: unclassified
Rejected 2 times.

## FA-budget-exceeded — Repeated rejection

Task: GROWTH (G-001): Implement a mailbox acknowledgment protocol with timestamp, stat
Class: budget-exceeded
Rejected 2 times.

## FA-budget-exceeded — Repeated rejection

Task: GROWTH (G-002): Add syscall-level dependency observation to strengthen the closu
Class: budget-exceeded
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: GROWTH (memory-graph edges): Write body/organs/memory-graph/edges.py to compute 
Class: unclassified
Rejected 3 times.

## FA-budget-exceeded — Repeated rejection

Task: GROWTH: Add a pre-proposal closure-budget estimator to meristem/loop.py. Before 
Class: budget-exceeded
Rejected 2 times.

## FA-probe-regression — Repeated rejection

Task: GROWTH: Add a probe at state/probe-proposals/probe-selfcheck-consumption/ that v
Class: probe-regression
Rejected 2 times.

## FA-budget-exceeded — Repeated rejection

Task: GROWTH: Implement a vault-only rubric authoring mechanism for immune-behavior pr
Class: budget-exceeded
Rejected 3 times.

## FA-deterministic-check — Repeated rejection

Task: Make meristem/breaker.py delegate counting to the journal-query organ when that 
Class: deterministic-check
Rejected 3 times.

## FA-secret-leak — Repeated rejection

Task: Make meristem/breaker.py delegate to the journal-query organ when it is active, 
Class: secret-leak
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: Make the dating in body/organs/memory-graph/extract.py actually take effect: eve
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR (FA-001, FA-005, FA-018): Fix body/organs/memory-graph/main.py op_selfche
Class: unclassified
Rejected 3 times.

## FA-budget-exceeded — Repeated rejection

Task: REPAIR (FA-011): Fix meristem/breaker.py should_park to fail loud when the journ
Class: budget-exceeded
Rejected 3 times.

## FA-budget-exceeded — Repeated rejection

Task: REPAIR (FA-015/FA-016): Fix breaker.py delegation to journal-query organ. The ta
Class: budget-exceeded
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR (FA-017): Fix body/organs/memory-graph/extract.py to restore three things
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR (FA-019): Fix the failure-aggregator's selfcheck in body/organs/failure-a
Class: unclassified
Rejected 3 times.

## FA-budget-exceeded — Repeated rejection

Task: REPAIR (G-009): Install the selfcheck-runner organ call site in meristem/loop.py
Class: budget-exceeded
Rejected 3 times.

## FA-budget-exceeded — Repeated rejection

Task: REPAIR (P-030 enforcement): Scan state/probe-proposals/ for any rubric/check.py 
Class: budget-exceeded
Rejected 3 times.

## FA-gate-weakening — Repeated rejection

Task: REPAIR (breaker counting): A reviewer that never read the proposal still casts a
Class: gate-weakening
Rejected 3 times.

## FA-secret-leak — Repeated rejection

Task: REPAIR (failure-aggregator broken since cycle 206): Fix the SyntaxError in body/
Class: secret-leak
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR (failure-aggregator journal path): Fix the failure-aggregator's journal p
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR (failure-aggregator): Fix the SyntaxError in body/organs/failure-aggregat
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR: Fix the SyntaxError in body/organs/failure-aggregator/classify.py at lin
Class: unclassified
Rejected 3 times.

## FA-unclassified — Repeated rejection

Task: REPAIR: In body/organs/failure-aggregator/main.py, fix the exit code to be non-z
Class: unclassified
Rejected 2 times.

## FA-unclassified — Repeated rejection

Task: Strengthen the memory-graph selfcheck so it would have caught this. In addition 
Class: unclassified
Rejected 2 times.

## FA-unclassified — Repeated rejection

Task: REPAIR: Fix the failure-aggregator organ's classify.py SyntaxError and its main.
Class: unclassified
Rejected 2 times.

## G-006 — Repeated rejection

Task: REPAIR: Fix the failure-aggregator organ's classify.py SyntaxError and its main.py exit code, and add a journal entry kind for its runs, so that the organ passes selfcheck and its output is traceable.
Rejected 4 times.
Previous attempts at this exact task were rejected:
- cycle 354: review:deepseek: Reduction of failure classes from 12 to 4, removing detection of protected-path, secret-leak, immune-failure, organ-manifest, budget-exceeded, no-change, deterministic-check, parse-error, review-rejected
- cycle 354: review:deepseek: Removal of pattern writing to state/patterns.md, reducing traceability of recurring rejections
- cycle 354: review:deepseek: Removal of canary reject counting, narrowing detection scope
- cycle 354: review:deepseek: Removal of fault-cycle exclusion, potentially causing false positives or missed signals
- cycle 354: review:deepseek: Signal grading changes: block action removed, escalate threshold changed from 3 to 2, altering circuit breaker response
- cycle 354: review:sensenova: Classification surface narrowed from 12 failure classes to 3 (gate-weakening, probe-regression, closure-budget). Nine classes — protected-path, secret-leak, parse-error, organ-manifest, immune-failur

## FA-budget-exceeded — Repeated rejection

Task: GROWTH: Add a mechanism to allow the loop to read specific metadata files (like 
Class: budget-exceeded
Rejected 2 times.

## G-006 — Repeated rejection

Task: GROWTH: Add a mechanism to allow the loop to read specific metadata files (like organ.json) from body/organs/ without breaking the current body/ exclusion. Implement a whitelist of allowed paths (e.g.
Rejected 4 times.
Previous attempts at this exact task were rejected:
- cycle 362: deterministic: kernel is 3010 lines, over the 3000 cap
- cycle 363: review:deepseek: The diff narrows an existing exclusion: previously every path under body/ was forbidden to mutable_files(); now body/organs/*/organ.json is allowed, which is a reduction of a gate.
- cycle 363: review:deepseek: It grants the mutation engine access to files it did not have, and the task explicitly states this unblocks the loop from updating organ lifecycle fields such as advancing an organ from candidate to calibrate.
- cycle 363: review:deepseek: Allowing the mutation engine to modify organ manifests creates a path for self-promotion of organs outside the substrate's promotion authority, making a previously loud structural boundary silently permissive for a whitelisted class.
- cycle 365: deterministic: kernel is 3003 lines, over the 3000 cap; closure ~52020 > 50000 budget. Kernel+control ~39168 always counted. Droppable: state/patterns.m

## FA-unclassified — Repeated rejection

Task: GROWTH: Implement cross-task failure aggregation query in the journal-query orga
Class: unclassified
Rejected 2 times.

## FA-unclassified — Repeated rejection

Task: REPAIR (failure-aggregator SyntaxError, confirmed by selfcheck-runner): Fix the 
Class: unclassified
Rejected 2 times.

## G-006 — Repeated rejection

Task: REPAIR (failure-aggregator SyntaxError, confirmed by selfcheck-runner): Fix the SyntaxError in body/organs/failure-aggregator/classify.py at line 21. The selfcheck-runner organ (body/organs/selfcheck-
Rejected 3 times.
Previous attempts at this exact task were rejected:
- cycle 378: review:deepseek: Removes 8 of 12 failure classes (protected-path, secret-leak, parse-error, organ-manifest, immune-failure, budget-exceeded, no-change, deterministic-check, review-rejected) and their associated keywords, narrowing the classifier's detection capability
- cycle 378: review:deepseek: Deletes the `classify_reasons` and `dominant_class` functions, eliminating aggregate analysis that was critical for detecting repeated failure classes across cycles
- cycle 378: review:deepseek: Reduces the classifier from 12 classes with broad keyword coverage to 4 classes, directly contradicting the original design intent and explicit instructions in the previous code to not narrow the coverage
- cycle 378: review:deepseek: The new code only classifies single reasons, losing the ability to output structured entries to `state/patterns.md` for G-006 and G-007 detection, which was the organ's purpose
- cycle 378: review:deepseek:

## FA-protected-path — Repeated rejection

Task: REPAIR (G-008, failure-aggregator traceability): In body/organs/failure-aggregat
Class: protected-path
Rejected 2 times.

## G-006 — Repeated rejection

Task: EXTERNALIZE: Move the review gate from meristem/gates/review.py (196 LOC) into body/organs/review-gate/. The review gate evaluates candidate mutations against review criteria and returns structured pa
Rejected 3 times.
Previous attempts at this exact task were rejected:
- cycle 387: review:deepseek: The kernel no longer guarantees the full weakening-detection prompt; it only checks for the WEAKENING_QUESTION substring. The 'How to decide' criteria, budget-exception rules, and refactor-vs-relaxation guidance now live in a mutable organ, so a future mutation can strip or weaken them without tripping any kernel invariant.
- cycle 387: review:deepseek: organ.json declares `dependencies: []` while main.py reads `control/constitution.md` and `control/checklists.md` and is consumed by `meristem/gates/review.py`; this is a hidden dependency and can shrink future review closures.
- cycle 387: review:deepseek: The new `_read_text` silently returns '' on FileNotFoundError, so a missing constitution or checklist would produce a review prompt without those documents. The old kernel `read_text` would fail loudly; this converts a hard failure into a silent default-continue.
- cycle 387: review:deepseek: Fail-closed

## P-025 — Immune-tier code cannot be externalized to mutable organs

**Class:** Gate weakening via externalization of admission-defining code.

**Instance:** The deterministic gate (meristem/gates/deterministic.py, 245
LOC) was proposed for externalization to body/organs/deterministic-gate/ to
relieve core pressure (2996/3000). Refused: the gate that checks mutations
cannot itself be mutable by mutations. An organ in body/ is on the mutation
surface; a mutation could weaken the organ's checks, and the modified organ
would run against the candidate tree (which includes the modified organ),
passing its own weakening. No deterministic check would catch it because the
deterministic check IS the weakened organ.

**Structural reason:** meristem/gates/__init__.py states 'Everything under
this package is admission-defining.' Moving admission-defining code to
body/ removes it from the immune tier and places it on the mutation surface.
The vault-reference invariant would also need widening (the organ must
reference VAULT but is not in meristem/gates/), organ_manifests() would
become circular (the organ validating its own manifest), and engine.py's
dependency on kernel_loc()/KERNEL_LOC_CAP at context-build time cannot be
satisfied by an organ call.

**Resolution:** Core pressure must be relieved by other means — externalizing
non-immune kernel code (loop.py is the largest file), pruning stale code, or
an argued cap case. No subset of the deterministic gate can be safely
externalized: every check it performs is security-critical, and a thin
re-verification shim in the kernel would need to contain all checks, saving
zero lines.

## G-006 — Repeated rejection

Task: EXTERNALIZE: Move the closure gate from meristem/gates/closure.py (181 LOC) into body/organs/closure-gate/, reducing core pressure by 181 lines and closure pressure by the gate's closure footprint. Th
Rejected 3 times.
Previous attempts at this exact task were rejected:
- cycle 394: review:deepseek: The wrapper's `_from_json` silently defaults missing `fits` to `True`, missing `tokens` to `0`, missing `undeclared` to `[]`, and missing `files` to `[]`. Any incomplete or malformed organ response therefore becomes a passing closure; the old in-process code computed these values directly and had no such fail-open fallback.
- cycle 394: review:deepseek: The kernel now trusts organ JSON output without validating that all mandatory fields are present, that returned paths are within the inspected tree, that `fits` is consistent with `tokens` and the budget, or that undeclared dependency edges are reported. An omission in the organ output becomes an omission in the review closure, directly violating the conservative over-inclusion requirement.
- cycle 394: review:deepseek: The closure calculator is moved into a mutable organ and invoked via a fresh subprocess from `REPO`. A working-tree mutation to `body/orga
