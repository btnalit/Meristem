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

## G-006 — No circuit breaker for repeated review rejections

**What is missing.** The loop retries a rejected task on the next cycle because
`done_tasks()` only marks a task complete when the outcome is `candidate`, and
`take_task()` returns the first open item. There is no aggregation of rejection
reasons across cycles, no retry counter, and no escalation path when the same
task fails review repeatedly for the same reason. The journal records each
rejection's reasons, but nothing reads those records back to detect "this task
has been rejected N times for the same failure class."

**Why it matters.** A task that the engine cannot complete through Tier A
whole-file replacement will burn model calls every cycle — up to the
`cycle_calls` cap of 40 — producing the same rejection each time. This wastes
quota, generates noise in the journal, and masks the real signal: the task is
underspecified for Tier A and needs Tier B, or the kernel has grown hard to
modify, or the task itself is malformed. Principle 2 says discipline is not a
fix: adding a retry limit to a prompt is training, not growth. The structural
fix is a circuit breaker that counts consecutive rejections per task, and when
a threshold is crossed, either escalates the tier, surfaces the pattern to
`state/patterns.md`, or marks the task as blocked for human attention.

**Blocked on.** A mechanism to aggregate journal entries by task text across
cycles (the journal stores `why` as the task string, so this is queryable but
nothing does it yet), and a policy for what happens when the threshold is
crossed — escalate tier, block, or notify. Both are kernel-level changes that
belong in `meristem/loop.py` and should be gated normally.

## G-SELFCHECK — Selfcheck enforcement and runner

The germline protocol now defines a `selfcheck` op that multi-part organs
must support (control/germline-protocol.md). Two things are not yet built:

1. **Validator enforcement.** `meristem/gates/germline_validate.py` does not
   yet check that a multi-part organ (more than one `.py` file) declares
   selfcheck support. This should be added when the first multi-part organ
   reaches calibrate, so the requirement is enforced at the gate rather than
   merely documented.

2. **Kernel runner.** There is no `meristem loop selfcheck` command that
   invokes the selfcheck op on every active multi-part organ. This should be
   added (or grown as an organ) when organs exist — adding it now would be
   premature core growth with nothing to run against.

Both are blocked on the first multi-part organ appearing in body/.
