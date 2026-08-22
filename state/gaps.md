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

- RESOLVED G-003: Vault seed content generation moved out of the repository.
  bootstrap.py no longer contains demo probe rubrics, statements, or any
  probe content. It creates the vault directory structure and an empty
  manifest only. The human writes anchor probes directly into the vault.
  This closes the class structurally: there is no rubric content in the
  repository for the mutation engine to read, so physical invisibility
  holds by construction rather than by convention.

## G-001 — Mailbox acknowledgment protocol

Addressed by `body/organs/mailbox-ack/` (candidate). Provides structured
tracking of mailbox entries with timestamp, status
(pending/acknowledged/expired), and TTL-based expiry. Organ at candidate
stage; probes to be staged through `state/probe-proposals/` and promoted
to the vault by the gates before calibrate. No rubric logic in the
mutation surface — the previous rejection at cycle 175 taught that
placing check.py inside the repo lets the engine read scoring logic and
craft organ behavior to pass, weakening the internal probe as a genuine
gate.

## G-007 — The pattern register has never been written by the loop

Observed facts, recorded as evidence:

- `state/patterns.md` holds 23 failure classes (P-001..P-024, P-015 dangling).
  `git log -- state/patterns.md` shows **zero commits authored by a cycle**;
  every entry was written by the human operator. By contrast the loop HAS
  appended to `state/gaps.md` in 4 cycles, so the appends mechanism and the
  permission both work — `state/patterns.md` is not on a guarded path and a
  proposal naming it routes to the agenda normally.
- Cycles 172-176 worked one task, G-001. Attempt 2 (cycle 173) was refused
  `deterministic: kernel is 3150 lines, over the 3000 cap`. Attempt 5 (cycle
  176) was approved 2/2 and promoted, and its diff put 303 lines under
  `body/organs/mailbox-ack/` with zero lines added to `meristem/`.
- Cycles 177-178 then began G-002, a different task. Attempt 2 (cycle 178)
  was refused `deterministic: kernel is 3098 lines, over the 3000 cap`.
- `failure_history()` selects journal rows whose `why` equals the current
  task, so rejections from G-001 are not visible while working G-002. It also
  records only rejections; the successful fifth attempt left no trace in any
  feedback channel.
- Constitution Principle 2 assigns the detection of failure CLASSES, rather
  than instances, to the loop itself.

## G-008 — The aggregator's own runs leave no trace in the journal

Observed facts about `body/organs/failure-aggregator/`, built by cycle 197
and invoked from `_try_aggregate_failures()` every cycle:

- The organ works. On 2026-08-18 it appended 18 entries (FA-001..FA-018,
  209 insertions, 0 deletions) to `state/patterns.md`, aggregating repeated
  rejections per task across cycles 74-83 and later, each with a failure
  class, a count, and representative reviewer reasons.
- Its call site records nothing. `_try_aggregate_failures()` prints organ
  failures to stderr and returns; a non-zero exit, a timeout, an exception,
  or a malformed reply all leave the journal unchanged. A successful run
  leaves the journal unchanged too. There is no `kind` for this organ.
- Consequence, observed: between the organ's promotion and its first
  written entries, `{"ok": true, "signals": [], "patterns_written": []}`
  was its actual return value when run by hand, and nothing in the journal
  distinguished that state from "the organ never ran".
- The one visible signal is a printed line reading
  `pattern: unclassified on '<task>' (2 rejections)` — the classifier
  reached a verdict of `unclassified` for that group.

## G-009 — A working organ checker exists and nothing calls it

- `body/organs/selfcheck-runner/` was promoted by cycle 210 (commit 1fc2e4c5).
  `grep -rn "selfcheck-runner\|selfcheck_runner" meristem/ substrate/`
  returns nothing: no call site exists in the kernel or the substrate.
- Run by hand against the live organ set, it returns:

      {"ok": false,
       "results": [
         {"organ": "failure-aggregator", "ok": false,
          "error": "exit 1: Traceback (most recent call last):\n
            File \"body/organs/failure-aggregator/main.py\", line 34,
            in <module>\n from classify import classify, ...\n
            File \"body/organs/failure-aggregator/classify.py\", line 21\n
            dominant-class detection.\n SyntaxError: invalid syntax"},
         {"organ": "memory-graph", "ok": true, "results": [], "failures": []}
       ],
       "failures": ["failure-aggregator"]}

- `failure-aggregator` was rewritten by cycle 206 (promoted). Before that
  rewrite it appended 18 entries to `state/patterns.md`; since it, none.
- Cycle 209 attempted this same task with a kernel call site and was refused
  `deterministic: kernel is 3001 lines, over the 3000 cap`. Cycle 210 was
  promoted with zero kernel lines. Its own rationale records the reason:
  "The validator-enforcement half ... is deliberately deferred to avoid
  adding kernel lines while at the cap; it can be done in a separate
  mutation when core pressure drops."
- `_try_aggregate_failures()` in `meristem/loop.py` catches every exception
  from the aggregator organ, prints to stderr, and returns; no journal
  record is written for either a success or a failure.

## G-FEASIBILITY

**Gap:** No pre-proposal feasibility check exists. Tasks that would exceed
the kernel line cap or closure token budget are only caught after a model
call has been made, wasting cycles (FA-016 pattern: 3 rejections for
closure-budget at cycles 97-99). The existing `estimate_closure` in loop.py
is a rough inline function, not a proper capability with probes and lifecycle.

**Measuring stick:** The feasibility-check organ at body/organs/feasibility-check/
estimates core and closure pressure impact before a task enters the agenda.
A probe should feed known feasible and infeasible tasks and verify the
organ's verdict matches expectations.

**Closed by:** body/organs/feasibility-check/ (candidate stage).

## Feasibility-check: probe proposals staged, organ.json update pending

Two probe proposals staged under state/probe-proposals/:
- probe-feasibility-check-feasible: verifies the organ returns feasible=true
  for a clearly feasible task (small one-file edit)
- probe-feasibility-check-infeasible: verifies the organ returns feasible=false
  for a clearly infeasible task (rewrite entire kernel plus all organs)

To complete the promotion from candidate to calibrate:
1. Gates must promote the probe proposals to the vault.
2. organ.json must be updated to add probe ids to its probes list.
3. Lifecycle must be advanced from candidate to calibrate.

Step 2 requires seeing the current organ.json content, which is not
available in the mutation context because body/ is in EXCLUDED_DIRS.

## G-031 — Vault-only rubric authoring

Rubric scoring logic for immune-behavior probes must not reside in the
repository (P-030). The rubric-author organ writes rubrics directly into
the vault, closing the class structurally. Measuring stick:
probe-rubric-author-vault verifies a generated rubric is in the vault
and no check.py appears in body/.

## G-032 — Gitignored runtime records are counted in the review closure

Cycle 388 was refused by the deterministic gate:

    closure ~52704 > 50000 budget. Kernel+control ~39180 always counted.
    Droppable: state/patterns.md ~13512, state/decisions.jsonl ~12...

state/decisions.jsonl is not tracked by git. It sits in .gitignore beside
journal.jsonl and scoreboard.jsonl, under the note that runtime records are
per-deployment state rather than source. closure.compute walks the filesystem
rather than the index, so a mutation that touches one of these carries its
whole size into the review context.

Measured 2026-08-23, on main:

    kernel + control baseline   39180 tokens of a 50000 cap
    state/decisions.jsonl        1128
    state/scoreboard.jsonl       3108
    state/journal.jsonl         21060

The closure invariant itself is not in question: any single mutation's review
closure must fit in one review context, and that is what the cap enforces. The
observation is narrower -- that files which are deliberately not part of the
source tree are being charged against it.

What should change is not stated here, and one constraint on any answer is
worth knowing in advance: narrowing what the closure calculator counts is
literally "shrinks what reviewers or the closure calculator can see", which is
a terminal question in section 1 of the review checklist. Any proposal on this
ground meets the panel on exactly that question.
