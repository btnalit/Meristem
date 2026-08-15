# Review checklist

Adapted from Ouroboros `docs/CHECKLISTS.md` (MIT). Reviewers score a
candidate diff against every section. **Section 1 is terminal**: a finding
there rejects the change regardless of quorum.

## 1. Gate weakening (terminal)

- [ ] Does it remove, narrow, or disable any check?
- [ ] Does it loosen a threshold, cap, quorum, or budget?
- [ ] Does it shrink what reviewers or the closure calculator see?
- [ ] Does it grant the mutation engine access it did not have —
      especially any path toward the eval vault?
- [ ] Does it make a failure mode harder to detect or attribute?
- [ ] Does it touch `root/` or `substrate/`?
- [ ] Does it relax organ-manifest validation or narrow the closure union?

A refactor that preserves strength is not a weakening. Uncertainty resolves
toward reject.

## 2. Constitutional compliance

- [ ] Kernel line cap respected; capability that belongs in an organ is not
      being smuggled into the core.
- [ ] Closure computed, not asserted; declared dependencies match reality.
- [ ] Append-only records untouched (`journal`, `decisions`, `scoreboard`).
- [ ] `change is not delete`: if new wording were removed, would the original
      principle still be recognisable?
- [ ] Rationale recorded, and at the level of the failure class — not the
      instance.

## 3. Meta-over-patch depth

- [ ] Is this a patch to one symptom, or does it make a class impossible?
- [ ] The class test: had this existed six months ago, could the same failure
      have arrived through a different surface?
- [ ] Is it the *smallest* structural change that closes the class? A
      subtraction is usually better than a new gate.

## 4. Growth hygiene (organ changes)

- [ ] Manifest complete and honest; entrypoint real; probes present.
- [ ] Did a measuring stick exist before the capability did?
- [ ] Utility measure defined, and a retirement path stated.
- [ ] If `internalize`: is it budget-neutral — did equal complexity leave?

## 5. Correctness and blast radius

- [ ] Would it run? Imports resolve, paths exist, no obvious type errors.
- [ ] What breaks if this is wrong, and is that recoverable by revert?
- [ ] Any secret, credential, or private path in the diff?
- [ ] Any new external dependency, executable, or privileged tool?
