# Capability gaps

Loop B's input. `patterns.md` records what goes **wrong**; this records what
Meristem **cannot yet do**. A gap becomes an organ only after a probe for it
exists — the measuring stick precedes the capability.

Honest day-0 note: every entry below was written by the human. The first real
test of Loop B is whether Meristem starts adding entries here that nobody
pointed out — that ratio is the self-detection metric.

---

## G-001 — Cannot run unattended

- **Missing:** task self-supply (reflect), non-blocking human gate (mailbox),
  memory consolidation, a report a human can read in the morning.
- **Deliberately not pre-decomposed.** P1 hands over the *gap*, not a parts
  list. The five-organ decomposition in the design notes is an existence
  proof, not a roadmap — if Meristem proposes a different decomposition, that
  is the experiment working.
- **Blocked on:** P0 birth criteria.

## G-002 — Observation is shallower than the closure invariant implies

- **Missing:** syscall-level dependency observation. Today: registry call
  edges + AST static scan only.
- **Why it matters:** "Every dependency must be explainable" is only as strong
  as what we instrument. The invariant is stated with its scope, not beyond it.
- **Blocked on:** P2 hardening.

## G-003 — The vault's seed content is generated from inside the repository

- **Missing:** an out-of-repo authoring path for anchors and fixtures.
- **Why it matters:** `bootstrap.py` contains the demo rubric, so a determined
  mutator could read it. Real anchors must be hand-written into the vault and
  never committed; moving the generator out of the repo closes the hole.
- **Blocked on:** P1.

## G-004 — Soil isolation is weaker on Windows than the design assumes

- **Missing:** OS-level ownership separation for the panic latch, and cgroup
  resource limits.
- **Why it matters:** on POSIX the latch is operator-owned and the seed cannot
  write it. On Windows that is convention, not enforcement.
- **Blocked on:** a Linux target, or a Windows equivalent (ACLs + Job Objects).

## G-005 — No external anchor of substance

- **Missing:** a locally reproducible external benchmark subset.
- **Why it matters:** self-authored probes prevent regression; only external
  anchors prevent self-deception. Until P3 the divergence alarm is the main
  defence, and it is thin with one anchor.
- **Blocked on:** P3.
