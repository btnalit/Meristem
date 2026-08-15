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

## P-001 — Self-declared inputs to a check that guards against self-declaration

- **Count:** 1 (found in design review, before first code)
- **Class:** A gate computes something "independently", but every input it
  computes from is supplied by the thing being gated.
- **Instance:** The review-closure calculator refused self-declared closures,
  but computed the closure from the dependency manifest — which the mutation
  also writes. Guarding the first order left the second wide open.
- **Structural fix:** Closure is the union of three sources — declared, static
  (AST), and observed (registry edges) — and any edge present in the observed
  or static set but absent from the manifest is a contract violation, not a
  warning. `meristem/gates/closure.py`.
- **Residual:** Observation is instrumented only at the registry and by static
  scan. Syscall-level is P2+. Recorded in `gaps.md` rather than claimed.

## P-002 — Silent path-shape assumptions in a safety scanner

- **Count:** 1 (found by `tests/test_kernel.py` on first run)
- **Class:** A security check crashes — rather than fails closed — when given
  input outside its assumed shape. A crash in a scanner reads as "no findings"
  to anything that catches it.
- **Instance:** `scan_secrets` called `Path.relative_to(REPO)` on a temp file
  outside the repo and raised `ValueError`.
- **Structural fix:** The scanner never derives control flow from path shape;
  labels degrade to the absolute path. `meristem/gates/deterministic.py`.

## P-003 — Reading success from a command that reports failure on stdout

- **Count:** 1 (found while testing promotion)
- **Class:** Trusting a subprocess's stdout without checking how it signals
  absence. `git rev-parse <missing-ref>` echoes the ref name and exits
  non-zero; reading stdout alone turns "nothing there" into "something there".
- **Instance:** `promote()` believed a candidate existed when none did.
- **Structural fix:** One `resolve()` helper using `--verify --quiet` is the
  only way the substrate resolves a ref. `substrate/supervisor.py`.
