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

## P-024 — An approved candidate discarded by an unrelated failure

- **Count:** 1 (cycle 120, silently)
- **Class:** A verdict is conditioned on something that did not produce it. The
  gates approved a change; the substrate then declined to promote it because
  the *process* that emitted it exited non-zero for an unrelated reason. The
  next cycle branched from the unchanged HEAD and overwrote it. Nothing failed
  loudly; the work simply stopped existing.
- **Instance:** The heartbeat promoted only when `returncode == 0 and
  argv[0] == "cycle"`. Cycle 120 -- the compression that would have made the
  first real externalization possible -- passed both reviewers unanimously,
  and its beat returned non-zero because of an earlier fault in the same beat.
  It was never promoted. Cycle 121 then branched from the same HEAD, added a
  `report` command to the old loop.py, and was promoted over it. Core pressure
  went up (0.88 -> 0.92) during the campaign whose entire purpose was to bring
  it down.
- **Structural fix:** promote whenever a candidate REF exists, not when the
  beat's exit code was clean. A candidate is the gates' verdict; it is not the
  exit status of the process that happened to produce it.
  `substrate/supervisor.py`.
- **Recovery attempted and refused, on evidence:** merging the orphaned branch
  back was textually clean and semantically broken -- 13 test failures, because
  both branches had evolved the same file independently. Reverted. Git merges
  text; it does not merge meaning. The work is better redone from the current
  HEAD than reconstructed from a fork that has drifted.
- **The rule:** never gate an outcome on a signal from outside the thing being
  judged. And when two lines of work touch one file, the second one landing
  first does not make the first one wrong -- it makes it lost, which is worse,
  because nothing reports it.

## P-023 — The prompt accumulated what the constitution had already excluded

- **Count:** 2 faults (cycles 107, 108), plus every cycle paying for it silently
- **Class:** A budget exclusion honoured in one place and ignored in another.
  P-022 removed `tests/` from the CLOSURE budget; the same file was still sent
  in full inside every MUTATION prompt. Fixing a rule where it is checked, but
  not where it is spent, leaves the cost exactly where it was.
- **Instance:** `tests/test_kernel.py` had reached 56KB -- larger than any
  kernel file and the single biggest item in the engine's context. Input
  reached 55,961 tokens and the engine returned nothing twice: the budget was
  gone before it arrived at the code it was asked to change. Alongside it,
  `control/agenda.md` had grown to 28KB holding forty tasks averaging ~500
  characters, nearly all of them already finished -- sent in full on every
  cycle.
- **Structural fix:** tests are included in the mutation context only when the
  task names one, so a task that must edit tests still sees them; the agenda
  was cut to its five genuinely open items with retired text moved to
  `state/agenda-archive.md`. Context for an ordinary task fell 25%, and the
  agenda from 28,310 to 3,080 characters.
- **Whose failure:** mine. I added tasks to the agenda for dozens of cycles and
  never removed a finished one -- accumulation instead of metabolism, in the
  one file charged by the token on every mutation. The constitution's own
  clause was sitting there unapplied.
- **The rule:** an exclusion must hold everywhere the excluded thing costs
  something, not only where it is measured. And any file that rides in every
  prompt has a size budget whether or not anyone wrote one down.

## P-022 — A budget spent on what the constitution excludes from it

- **Count:** 1 (three consecutive cycles blocked, 97-99)
- **Class:** An implementation that measures more than its own rule says to
  measure. The check is real, the threshold is real, and the refusals it emits
  are honest -- about the wrong quantity. It looks exactly like a system
  correctly reporting that it has run out of room.
- **Instance:** v3.1 1.3 excludes `tests/` from the budget (transplanted from
  ouroboros: tests are how we check the kernel, not the kernel itself, and
  they carry their own separate cap). The closure calculator counted them
  anyway. `tests/test_kernel.py` had grown to 1,280 lines -- 49,668 tokens on
  its own against a 50,000 budget -- so ANY change that also touched a test
  was refused, with a message about closure size that pointed nowhere near the
  cause. Three externalization cycles died on it in a row.
- **What made it look like something else:** the refusals arrived exactly when
  core pressure was high and an externalization campaign was running, so the
  obvious reading was "the system has grown too big to change itself" -- a
  dramatic conclusion the evidence seemed to support. Measuring one file at a
  time is what dissolved it: `breaker.py` alone was 34,308 of 50,000, with
  room to spare. The wall was in the ruler.
- **Structural fix:** the closure calculator skips `tests/` paths, matching
  the constitution it implements. Closure for the blocked change fell from
  49,668 to 34,428. Two tests pin the rule: touching a test must not grow the
  closure, and a test-only change must fit. `meristem/gates/closure.py`.
- **The rule:** when a gate refuses, check that it is measuring the thing its
  own charter names. A correct threshold over a wrong quantity produces
  truthful sentences about a fiction.

## P-021 — An urgency mechanism that starves the work it demands

- **Count:** 1 (twelve consecutive wasted beats)
- **Class:** A trigger pre-empts ordinary work in order to *plan* relief,
  while the relief can only arrive through the ordinary work it pre-empts.
  The more urgent the condition, the less progress toward resolving it -- a
  livelock assembled from two individually reasonable rules.
- **Instance:** Core pressure at 0.87 made every heartbeat beat choose
  `reflect --pressure` over running the agenda. Reflection only PROPOSES; the
  externalization that would lower pressure sits in the agenda as ordinary
  cycles. Twelve beats produced proposals and zero relief, and pressure never
  moved off 0.87.
- **Two compounding defects in the same log.** Reflect writes no "cycle"
  record, so `next_cycle()` returned the same number every time and the
  per-cycle call cap counted 51 calls against a limit of 12 -- so every one of
  those reflections also faulted. And nine proposals had accumulated with no
  path from `proposals.md` into the agenda: the seed had been thinking, and
  nothing was listening.
- **Structural fix:** pressure pre-empts ONCE per heartbeat run, then stands
  aside so the agenda can execute; it re-issues only when the agenda is empty
  and pressure persists, since that means the previous mandate produced
  nothing actionable. Reflection journals its own cycle record, making it a
  proper accounting unit. `substrate/supervisor.py`, `meristem/loop.py`.
- **The shape worth remembering:** when a mechanism reacts to a condition by
  *scheduling* rather than *acting*, ask what executes the schedule -- and
  whether the reaction has starved it.

## P-020 — The soil called a seed interface that did not exist yet

- **Count:** 1 (heartbeat beat 10, the pressure trigger's first firing)
- **Class:** P-018 across the soil/seed boundary. I wrote the substrate to
  invoke `reflect --pressure` and left the flag itself as an agenda task for
  the seed. The trigger fired correctly, read the pressure correctly, chose
  correctly -- and died on argparse. A capability that calls an interface it
  does not own must ship with that interface, exactly as a fence must ship
  with what it fences (P-019).
- **Instance:** `core pressure 0.85 >= 0.85 -- reflecting under pressure
  mandate` followed immediately by `unrecognized arguments: --pressure`. The
  whole mechanism worked apart from the one line that connects it, and the
  beat was spent for nothing.
- **Why the gates could not catch it:** the soil is outside the mutation
  surface, so no gate reviews it, and the kernel's tests do not know what the
  substrate calls. The seam between the two planes has no automatic reader --
  which is the price of the separation that makes promotion authority safe.
- **Structural fix:** the flag, the mandate text and the digest branch landed
  in one change, plus a test asserting `run_reflect` accepts `pressure` and the
  CLI parses it. That test is the only automatic reader that seam has.
- **Third instance of one lesson:** P-018 (organ modules), P-019 (fence behind
  capability), now P-020 (soil ahead of seed). Splitting work to fit an output
  budget keeps producing seams, and seams keep being what nothing is looking
  at. The rule has earned its generality: **whatever calls an interface ships
  with it, and the seam gets a test in the same change.**

## P-019 — A capability shipped ahead of its own fence

- **Count:** 1 (the first real reflect, cycle 77)
- **Class:** A new pathway for model output to reach the work queue is enabled
  before the check that constrains it. The window is small and the failure is
  quiet: nothing crashes, nothing is refused, and the unconstrained output
  simply lands where it should not have.
- **Instance:** The reflect step and its protected-path guard were written as
  two separate agenda tasks, in that order. Reflect landed first and ran, and
  its very first output included a proposal to change `substrate/supervisor.py`
  -- a *correct* observation (it had found the open item recorded in P-010,
  unaided) that went straight into the work queue, which is the one place a
  proposal about the soil must never reach.
- **Whose fault:** the sequencing was mine. The seed did exactly what it was
  asked. Ordering a fence behind the thing it fences is a scheduling error, not
  a model error.
- **Structural fix:** `route_proposal` sends any proposal naming `root/`,
  `substrate/`, `meristem/gates/`, the constitution or the checklists to the
  mailbox for human review instead of the queue, with tests on both branches.
  The seed may notice something about the soil and say so -- saying so must
  not be a route to acting on it. `meristem/loop.py`.
- **The rule:** a fence ships with the thing it fences, in the same change,
  never in the next one. Splitting work into small tasks (P-014, P-017) makes
  this easy to get wrong, because the fence looks like a separable increment.
  It is not: it is part of the capability's definition.

## P-018 — Splitting a task without verifying the seams

- **Count:** 1 (memory-graph organ, cycles 38-51)
- **Class:** A unit of work divided to fit a mechanism's limit, with nothing
  checking that the pieces still fit *each other*. Each part passes its own
  gates honestly; the assembly is broken, and nothing looks wrong until
  something tries to run it.
- **Instance:** `main.py` calls `edges.derive_edges(nodes)`; `edges.py`
  defines `derive(nodes)`. Both were written by the seed, each approved 2/2,
  each correct in isolation. The mismatch came from the *instructions* -- I
  named the function differently in the two tasks that wrote them. The organ
  reported `ok: false` the first time it was invoked.
- **Why the gates could not catch it:** the closure calculator reasons about
  dependencies between organs and files, not about whether one module's call
  matches another's definition. The reviewers saw one file at a time and had
  no reason to doubt a plausible function name. This is a real limit of
  per-change review: correctness of the seam is a property of the assembly,
  and no reviewer of a part is looking at it.
- **Structural fix:** the organ gets a `selfcheck` op that imports its own
  modules and exercises each entry point with tiny fixtures, so a part-to-part
  mismatch is caught by the organ itself rather than by whoever calls it. That
  is the organ-scale analogue of the golden fixtures: something whose only job
  is to prove the pieces still connect.
- **The lesson about splitting:** P-014 and P-017 both push toward smaller
  tasks, and this is the cost of that push. Dividing work to fit an output
  budget multiplies seams, and seams are exactly what per-piece review cannot
  see. Every split should name the interface, not just the behaviour -- and
  the assembly needs a check of its own.

## P-017 — Truncation detected only in one direction, and an error that ate its own evidence

- **Count:** 4 identical faults on one task (live cycles 40-42, 46-47)
- **Class:** A validity check that covers one failure direction and silently
  admits the opposite one, compounded by an error message that names the
  symptom while discarding the artefact that would explain it. Either alone is
  a bug; together they guarantee the fault repeats undiagnosed.
- **Instance, both halves.**
  1. `llm.complete` treated a reply as good if its content was non-empty. A
     reply that ran INTO `max_tokens` is non-empty -- so a truncated payload
     passed as healthy, was billed `ok=True`, and failed thousands of lines
     later as "unparseable JSON" with no connection to its real cause.
     Measured: cycle 47 returned exactly `out=32000` against a cap of 32,000,
     and the text stopped mid-way through a Python string literal.
  2. The parse error said only "engine returned no parseable JSON object". The
     reply was thrown away. Four occurrences produced four identical messages
     and zero information; the diagnosis only became possible once the message
     carried the length and the first and last 300 characters -- at which point
     the truncation was obvious in one reading.
- **Structural fix.** `finish_reason == "length"` is the authoritative
  truncation signal, with completion-tokens-at-cap as corroboration; a
  truncated reply now fails at the call, named for what it is, and is billed
  `ok=False`. The parse error carries bounded evidence -- enough to tell
  truncation from prose from a wrong shape, capped so a runaway reply cannot
  flood the journal. `meristem/llm.py`, `meristem/engine.py`.
- **Relation to P-014, which this partly corrects.** P-014 concluded no call
  had ever been truncated -- true of the data available then, and false now:
  the check that would have detected truncation did not exist, so the evidence
  could not have appeared. Both failure modes are real: an over-large task can
  make the model decline (P-014) *or* run past the cap (P-017), and they look
  nothing alike from the outside.
- **The rule:** an error must preserve what is needed to diagnose it. A message
  that reports only its own name converts a recurring fault into a recurring
  mystery.

## P-016 — Two opposite failures wearing the same label

- **Count:** 1 (live cycles 40-42, the circuit breaker's first real firing)
- **Class:** Two conditions that demand opposite responses are recorded under
  one label, so any policy keyed on that label is wrong for one of them. The
  record is not false -- it is merely undifferentiated, which is enough.
- **Instance:** A cycle that faults and a cycle the gates refuse both land as
  `outcome: "rejected"`. The breaker counted them together and parked
  `memory-graph/edges.py` after three unparseable engine replies -- three
  cycles in which **no gate ever formed an opinion**. A rejection means the
  change was seen and refused, and repeating it will fail again. A fault means
  the proposal never reached judgement, and retrying is exactly right.
- **Structural fix:** `rejections_for` counts only cycles with no
  corresponding fault record, `faults_for` counts the rest, and `should_park`
  carries two thresholds -- tight for judged rejections, which repeat
  deterministically; looser for faults, which are often transient. Not
  unlimited, though: a task the mechanism can never express is also worth
  setting aside. `meristem/breaker.py`.
- **Worth noting about the breaker itself:** it worked. Eleven cycles once went
  to one impossible task; this time three went to a task and the loop moved on
  by itself. The defect was in *what it counted*, not in whether it should
  count -- and the seed built it, so the correction is a repair of its own
  work, from evidence its own journal supplied.

## P-014 — An empty proposal, and a misdiagnosis of it

- **Count:** 2 (live cycles 30-31, same task)
- **Class:** The model returns a well-formed, schema-valid, EMPTY payload.
  Sibling of P-004: there a tight budget produced empty *content*, here an
  intact structure with nothing in it -- worse, because nothing looks wrong.

**Correction, recorded rather than quietly edited.** This entry originally
blamed output-budget exhaustion and quoted `in=36,339 out=2,413,
reasoning=1,598`. Those numbers belong to **cycle 29, which succeeded**. They
were read off the last usage row in the journal and attributed to the failure
that followed it. The measured facts are:

- `max_tokens` for mutate is 32,000; the largest output ever produced is
  21,496. **No call has ever been truncated** -- zero calls came within 10k of
  the cap.
- Cycles 30 and 31 have **no usage rows at all** (see P-015): the failing
  calls were never billed, which is why their real cost is unknown and why the
  wrong numbers were within reach.

So the mechanism is not truncation. The model, handed a task requiring a
400-line file rewritten in full, produced a short reasoning trace and then
**declined** -- returning `{"files": {}, "appends": {}}` rather than a partial
or truncated answer. An over-large task does not fail loudly here; it fails
politely.

- **Structural fix (unchanged, and still correct):** the task was split into
  three, each touching one file of modest size, and every split part passed on
  the first attempt. The error now reports in/out/reasoning counts so the next
  instance is diagnosed from data rather than from the nearest plausible story.
- **The rule this yields, restated honestly:** *task granularity must match
  what the model will actually attempt*, which is a smaller bound than what it
  could physically emit. Retrying an over-large task does not converge; it must
  be divided.
- **The lesson about the lesson:** a Pattern Register entry built on a misread
  is worse than no entry -- it teaches a wrong cause with the authority of a
  record. The constitution's rule against proxies applies to our own logs:
  check the authoritative row, not the nearest one.

## P-013 — A check whose reference point has already moved

- **Count:** 1 (live cycles 18–20, the same task rejected three times)
- **Class:** A "before and after" comparison where the caller supplies a
  *before* that the change has already overwritten. The check runs, reports
  no difference, and its silence is recorded as a pass. Kin to P-009 — a gate
  reading the wrong subject — but subtler, because here the subject is right
  and only the baseline is wrong.
- **Instance:** `memory_integrity` read the previous file with
  `git show HEAD:<path>`. The loop commits the mutation into the worktree
  *before* the gates run, so in that tree HEAD **is** the mutation. Before and
  after were the same bytes, no entry ever looked lost, and the structural fix
  written for P-008 was inert from the moment it shipped.
- **Structural fix:** the reference point is now an explicit `base` argument
  the caller must name — `HEAD~1` for a committed candidate, `HEAD` for an
  uncommitted working tree. A test builds a real two-commit repository and
  asserts that reading HEAD sees nothing while reading HEAD~1 sees the
  erasure, so the distinction is pinned rather than remembered.
  `meristem/gates/deterministic.py`, `meristem/loop.py`.
- **The class test:** a fix that closes only this instance would be "use
  HEAD~1 here". The class is *implicit baselines*, so the rule is that any
  before/after check takes its baseline as an argument and never assumes one.
- **What caught it, again:** the review panel — and this time the panel's
  heterogeneity is doing measurable work. `review:sensenova` rejected all
  three attempts; `review:deepseek` approved the first two. A single-reviewer
  gate running deepseek would have admitted the erasure of five capability
  gaps twice. That is the strongest evidence yet for failure independence
  being the property worth optimising, and it is now two independent
  observations rather than one.

## P-012 — A fallback that raises the exception it exists to absorb

- **Count:** 1 (live cycle 6)
- **Class:** Error handling whose recovery path can fail the same way as the
  path it recovers from. The failure then arrives *outside* every handler
  written for it, so it surfaces as a raw traceback and the durable record
  keeps a rejection with no reason attached.
- **Instance:** The engine's reply had a JSON object followed by commentary —
  routine for reasoning models. `_parse` caught `JSONDecodeError`, fell back
  to a greedy `{.*}` regex, and called `json.loads` on the result *without a
  guard*; "Extra data" propagated out of the fallback. Compounding it,
  `main()` caught only `MeristemError`, so an ordinary parse failure was not
  treated as a cycle outcome at all.
- **Structural fix:** parse with `json.JSONDecoder().raw_decode`, which reads
  one value and reports where it stopped — trailing prose becomes a non-event
  rather than an error to recover from. And `main()` treats *any* exception as
  a cycle outcome, journalling a `fault` record: an unexplained rejection is
  the one outcome the loop must never produce. `meristem/engine.py`,
  `meristem/loop.py`.

## P-011 — Completion recorded as an edit instead of a record

- **Count:** 1 (surfaced twice during live verification, as a blocked `git pull`)
- **Class:** State that belongs in a record is written as a mutation of tracked
  source, outside any transaction and never committed. The tree is dirty
  forever after, and ordinary operations start failing for reasons unrelated
  to what they are doing.
- **Instance:** The loop marked finished agenda items by rewriting
  `control/agenda.md` in the main checkout. Nothing owned that change, so it
  accumulated and twice blocked `git pull --rebase` mid-verification.
- **Structural fix:** the journal already knows which tasks produced a
  candidate, so completion is read from it. `agenda.md` returns to pure
  human-authored source the loop only reads — and a rejected task correctly
  stays open for retry. `meristem/loop.py`.

## P-009 — A gate that inspects the wrong tree

**The most serious class found so far, and the one this whole project exists
to prevent: a gate that looks exactly like a working gate while enforcing
nothing.**

- **Count:** 1 (found in live cycle 5, when a mutation slipped a vault
  reference past a check written specifically to catch vault references)
- **Class:** A check reads a module-level path constant instead of the subject
  it was handed. Every content-based assertion then describes the *current
  checkout* rather than the *candidate*, so it passes unconditionally. It
  cannot fail, it produces no error, and its passing verdict is recorded as
  evidence of safety.
- **Instance:** `deterministic.run(changed)` resolved every file through the
  `REPO` constant. The mutation was written into a worktree. So kernel LOC,
  the vault-reference invariant, closure size, memory integrity, organ
  manifests, and the secret scan all examined unmodified code. Only the
  path-string checks (protected prefixes) ever really ran. The gate had been
  inert for candidate content since the first cycle.
- **What saved it:** the expensive gate. The reviewers read the actual `git
  diff`, so cycle 4's erasure was still caught. Defence in depth worked
  exactly as intended -- which is also why the failure stayed invisible for
  four cycles. **A redundant check earning its keep looks identical to a
  redundant check doing nothing.**
- **Structural fix:** every gate function takes the tree it inspects as an
  argument, with the constant only as a default; the loop passes the candidate
  worktree. The signature now makes the correct call the natural one and the
  wrong call impossible to write by omission.
  `meristem/gates/deterministic.py`, `meristem/gates/closure.py`,
  `meristem/loop.py`.
- **The class test:** had this fix existed from the start, could the same
  failure have arrived through another surface? Yes -- any future check that
  closes over a global. Hence the rule stated as a rule, at the top of the
  module: a gate never reads a path constant when it was handed a subject.

## P-010 — "Alive" defined more narrowly than "correct"

- **Count:** 1 (same cycle, downstream of P-009)
- **Class:** A promotion gate certifies a weaker property than the one the
  project actually requires, so a candidate is admitted and marked *last-good*
  while failing checks the repository already contains.
- **Instance:** The canary ran only the immune self-test. Cycle 5's candidate
  passed the fixtures, was promoted, became last-good — and broke a kernel
  invariant its own test suite asserts. Worse, last-good then pointed at the
  broken commit, so the rollback target was itself unsound.
- **Structural fix:** the canary runs the fixtures **and** the kernel test
  suite; a candidate must satisfy both before it can become main.
  `substrate/supervisor.py`.
- **Open, recorded not solved:** last-good is still set at promotion, before
  the change has proven itself in service. A commit that dies later still
  leaves last-good pointing at it. Tracked in `gaps.md`.

## P-008 — Whole-file replacement erases what it was asked to extend

- **Count:** 1 (caught by the review gate in real cycle 4 — the first time the
  immune system stopped a live mutation)
- **Class:** The mutation mechanism's own shape makes a specific destruction
  the *natural* failure mode. Tier A returns complete replacement files, so
  "add an entry to this register" degrades gracefully into "replace this
  register with one entry" — and the result looks entirely well-formed.
- **Instance:** Asked to record a new failure class, the engine rewrote
  `state/patterns.md` from seven entries to one, deleting P-002…P-007 and
  renumbering its own entry as P-001. Both reviewers, from different lineages,
  independently flagged `weakens_gate=True` and cited Principle 6 and the
  register's role in Principle 2.
- **Why a prompt rule is not the fix:** "discipline is not a fix." The engine
  was not disobeying an instruction; it was doing exactly what whole-file
  replacement does. The class survives any wording that asks it to be careful.
- **Structural fix:** a deterministic check — append-only registers under
  `state/` may gain entries and may have entry bodies edited, but every
  existing `## X-NNN` heading must still be present. Cheap, total, and it fires
  before the expensive reviewer is ever paid. Plus a golden fixture, so the
  check that guards the register is itself guarded.
  `meristem/gates/deterministic.py`, `meristem/loop.py`.
- **What this instance is evidence *for*:** two heterogeneous reviewers
  reaching the same correct verdict independently is the first real datum in
  favour of the failure-independence hypothesis (§ models.toml). One data
  point, recorded as such.

## P-007 — An environment difference that reads as a capability regression

- **Count:** 1 (found on first deployment to Linux)
- **Class:** A measurement harness fails for an environmental reason and
  reports the failure **in the units of the thing being measured**. A probe
  that cannot start scores 0, which is indistinguishable from a probe that ran
  and found the system broken. This is the most dangerous shape a bug can take
  here: it corrupts the scoreboard, and the scoreboard is what every accept /
  reject decision rests on.
- **Instance:** The probe runner spawned rubrics with the bare name `python`.
  Debian ships only `python3`, so every rubric would have scored 0 on the
  deployment host while scoring 100 on the development host.
- **Structural fix:** rubrics are spawned with `sys.executable` — the very
  interpreter already running the kernel — so the harness cannot disagree with
  itself about which Python exists. `meristem/gates/probes.py`.
- **Generalisation:** never name an external tool the environment might not
  have when a self-reference is available. Where a probe genuinely cannot run,
  it must report *inability*, not a score.

## P-006 — A gate denominated in a unit the world does not charge in

- **Count:** 1 (found while routing a quota-limited endpoint)
- **Class:** A deterministic gate measures the wrong quantity, so it is
  structurally incapable of firing. A check that cannot fail is decoration
  that reads like protection.
- **Instance:** The budget gate was denominated in USD. The endpoint bills in
  **calls per 5 hours**, not tokens, so every priced field was 0 and the cap
  could never trip no matter how many requests the loop made.
- **Structural fix:** The ledger now enforces call caps alongside USD caps,
  and a test asserts the call cap is active. `meristem/ledger.py`.
- **Generalisation to watch for:** any future gate should be asked "in what
  unit does reality actually push back?" before it is written.

## P-005 — Measuring diversity by address instead of by ancestry

- **Count:** 1 (found when a single gateway fronted three model families)
- **Class:** A property that must hold of the *thing* is tested on a *proxy
  for the thing* — and the proxy silently stops tracking it.
- **Instance:** Reviewer independence was tested by counting distinct
  `base_url`s. One gateway fronting DeepSeek, Zhipu, and SenseNova would have
  read as "one provider, not diverse", while three siblings behind three
  URLs would have read as diverse. Both readings are wrong: what makes
  reviewer failures independent is **training lineage**, not network address.
- **Structural fix:** every reviewer slot declares an explicit `lineage`;
  the test asserts on lineage, and a second test asserts the mutator's own
  lineage is absent from the review panel — the author's family must not sit
  on the panel judging it. `control/models.toml`, `tests/test_kernel.py`.

## P-004 — Truncation masquerading as a successful empty answer

- **Count:** 1 (found on first real call against reasoning models)
- **Class:** A transport-level success (HTTP 200) carrying a semantic
  failure. The caller reads the happy path and propagates emptiness downstream
  where it surfaces as something confusing and far away.
- **Instance:** Every model at this endpoint is a reasoning model, and the
  thinking trace is billed against the same `max_tokens` as the answer. With a
  tight cap the trace consumed the entire budget and the response arrived
  `200 OK` with `content: ""`. Read naively, "the model was truncated" became
  "the model had nothing to say".
- **Structural fix:** `llm.complete` treats empty content as a failed call and
  retries, with an error naming the real cause. Reasoning tokens are recorded
  separately so the ledger shows where the budget went. `meristem/llm.py`.
- **Same class, second surface:** 429 at this endpoint means the request rate
  *ramped* too fast, not that quota ran out — an error whose text says
  "too many" but whose meaning is "too suddenly". Fixed structurally by pacing
  every call and backing off 15/30/60s rather than 1/2/4s.

## P-003 — Reading success from a command that reports failure on stdout

- **Count:** 1 (found while testing promotion)
- **Class:** Trusting a subprocess's stdout without checking how it signals
  absence. `git rev-parse <missing-ref>` echoes the ref name and exits
  non-zero; reading stdout alone turns "nothing there" into "something there".
- **Instance:** `promote()` believed a candidate existed when none did.
- **Structural fix:** One `resolve()` helper using `--verify --quiet` is the
  only way the substrate resolves a ref. `substrate/supervisor.py`.

## FA-001 — Repeated rejection: gate-weakening
- **Task:** Add ONE function to body/organs/memory-graph/main.py named pipeline_check(workdi
- **Failure class:** gate-weakening
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 74: review:deepseek: The selfcheck operation no longer returns `ok: false` when the pipeline detects 'no edges from >10 nodes' or 'all pattern nodes undated'. Previously it returned a hard failure (`ok: false` with a `fai
  - cycle 74: review:deepseek: The `_current_cycle` function lost the broad `OSError`/`FileNotFoundError` catch-all, replacing it with only an `exists()` check. If the journal file exists but is unreadable (e.g. permission error), 
  - cycle 75: review:deepseek: Selfcheck no longer fails when pipeline problems exist (no edges with >10 nodes, all pattern nodes undated); it returns ok with problems listed, weakening the gate that previously rejected such states
  - cycle 75: review:deepseek: Error handling changed: exception details are removed from failure messages, making failures harder to diagnose and attribute.
  - cycle 76: review:deepseek: Removes fields from the ABI responses of build, query, stale, and explain ops, breaking the existing structured return contract.
  - cycle 76: review:deepseek: Rewrites the selfcheck op to test modules with hardcoded trivial data instead of the real workdir, weakening its ability to detect actual pipeline failures.
- **Detected at cycle:** 202


## FA-002 — Repeated rejection: gate-weakening
- **Task:** Add a `report` command to meristem/loop.py that writes REPORT.md at the reposito
- **Failure class:** gate-weakening
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 82: deterministic: review closure is ~50148 tokens, over the 50000 budget -- split the organ before growing it
  - cycle 83: review:deepseek: Removes gitignore entries for meristem-vault/, eval-vault/, meristem-wt-*/, and meristem-canary/, which were defence-in-depth gates preventing accidental commit of sensitive or transient artifacts.
  - cycle 83: review:deepseek: The constitution states 'The eval vault must NEVER be committed'; the previous gitignore helped enforce that. Removing it weakens the gate and makes accidental exposure more likely.
  - cycle 84: review:deepseek: .gitignore change removes entries that protected eval vault, transient worktrees, and environment files from being committed, weakening defense-in-depth against accidental exposure of sensitive data a
  - cycle 84: review:deepseek: Removing the .gitignore entry for `meristem-vault/` and `eval-vault/` directly contradicts the constitution's requirement that the vault must never be committed.
- **Detected at cycle:** 202


## FA-003 — Repeated rejection: probe-regression
- **Task:** Add a `utility` command to meristem/loop.py that reads state/journal.jsonl and p
- **Failure class:** probe-regression
- **Count:** 2 rejection(s)
- **Representative reasons:**
  - cycle 88: probes: regression on frozen probe 'probe-kernel-selftest': 100.00 -> 0.00
- **Detected at cycle:** 202


## FA-004 — Repeated rejection: unclassified
- **Task:** Add a golden fixture to meristem/loop.py golden_fixtures() covering a failure cl
- **Failure class:** unclassified
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 4: review:deepseek: The diff replaces the entire contents of state/patterns.md, deleting all existing pattern entries (P-002 through P-007 and the original P-001) and renumbering/repurposing P-001. This violates Principl
  - cycle 4: review:deepseek: The deletion of pattern history reduces the effectiveness of the meta-over-patch principle (Principle 2), which relies on patterns.md as the memory of failure classes. Without these entries, the syste
- **Detected at cycle:** 202


## FA-005 — Repeated rejection: gate-weakening
- **Task:** Add an op "explain" to body/organs/memory-graph/main.py taking args {"id": "..."
- **Failure class:** gate-weakening
- **Count:** 2 rejection(s)
- **Representative reasons:**
  - cycle 57: review:sensenova: op_query and op_explain convert a hard failure into a success response: when a node is not found, the old code returned {"ok": false, "result": {"error": "..."}} but the new code returns {"found": fal
  - cycle 57: review:sensenova: op_selfcheck is substantially weakened: the old version created a controlled temporary workdir with proper state files and tested the full extract→edges→decay pipeline end-to-end with type assertions.
  - cycle 60: review:deepseek: Error response format changed from {"ok": false, "result": {"error": "..."}} to {"ok": false, "error": "..."}, breaking the documented ABI and making error attribution harder for callers that expect t
  - cycle 60: review:deepseek: Query response no longer includes the full "activations" dict; only the activation of the queried node is returned. This removes information that may be relied upon by the immune system probes, potent
- **Detected at cycle:** 202


## FA-006 — Repeated rejection: gate-weakening
- **Task:** Append one new capability gap to state/gaps.md that you observed while running, 
- **Failure class:** gate-weakening
- **Count:** 10 rejection(s)
- **Representative reasons:**
  - cycle 25: deterministic: state/gaps.md drops append-only entries ['G-001', 'G-002', 'G-003', 'G-004', 'G-005'] -- registers may gain entries, never lose them
  - cycle 26: deterministic: state/gaps.md drops append-only entries ['G-001', 'G-002', 'G-003', 'G-004', 'G-005'] -- registers may gain entries, never lose them
  - cycle 27: review:sensenova: The task explicitly requires 'Append only: every existing G-NNN heading must survive.' The diff replaces all five existing G-001 through G-005 entries with entirely different content rather than appen
  - cycle 27: review:sensenova: Violates Principle 6 (change is not delete): removing the new wording does not leave the original principle recognizable — the old entries are gone, not supplemented. The old G-001 ('Cannot run unatte
- **Detected at cycle:** 202


## FA-007 — Repeated rejection: gate-weakening
- **Task:** Collect organ utility. meristem/germline.py already journals an "organ_call" rec
- **Failure class:** gate-weakening
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 106: review:deepseek: The diff makes only two cosmetic changes: a docstring line wrap and added whitespace in the Pressures digest string.
  - cycle 106: review:deepseek: No check, assertion, threshold, cap, quorum, visibility, or invariant is removed, narrowed, or relaxed.
- **Detected at cycle:** 202


## FA-008 — Repeated rejection: gate-weakening
- **Task:** EXTERNALIZE: Move generate_report() formatting logic from meristem/loop.py into 
- **Failure class:** gate-weakening
- **Count:** 6 rejection(s)
- **Representative reasons:**
  - cycle 146: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
  - cycle 156: review:deepseek: Bypasses germline.invoke by using subprocess.run directly, which weakens the gate that controls organ execution (lifecycle validation, resource limits, probes).
  - cycle 156: review:sensenova: Direct subprocess.run invocation bypasses germline.invoke entirely, circumventing the lifecycle gate that requires organs to be 'active' before invocation. The mutation explicitly acknowledges this ('
  - cycle 157: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
- **Detected at cycle:** 202


## FA-009 — Repeated rejection: probe-regression
- **Task:** EXTERNALIZE: Move generate_report() from meristem/loop.py into a new organ body/
- **Failure class:** probe-regression
- **Count:** 2 rejection(s)
- **Representative reasons:**
  - cycle 133: deterministic: organ 'reporter': an organ may not reach register/active with no probes
  - cycle 134: deterministic: organ 'reporter': an organ may not reach register/active with no probes
- **Detected at cycle:** 202


## FA-010 — Repeated rejection: unclassified
- **Task:** EXTERNALIZE: Move journal aggregation and task lifecycle (take_task, done_tasks,
- **Failure class:** unclassified
- **Count:** 2 rejection(s)
- **Representative reasons:**
  - cycle 165: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
  - cycle 167: review:deepseek: reviewer unavailable: review:deepseek failed after 4 attempts: HTTP Error 429: Too Many Requests
- **Detected at cycle:** 202


## FA-011 — Repeated rejection: gate-weakening
- **Task:** Externalize the circuit breaker for repeated review rejections (G-006) into a ne
- **Failure class:** gate-weakening
- **Count:** 4 rejection(s)
- **Representative reasons:**
  - cycle 190: review:deepseek: Organ's _read_jsonl returns [] when the journal path is missing, so should_park silently becomes False (retry) instead of failing loud; a missing/corrupt journal now disables the circuit breaker witho
  - cycle 190: review:deepseek: The shim passes str(JOURNAL) while running the organ with cwd=str(_ORGAN_DIR); if JOURNAL is relative, the organ reads a non-existent path and the breaker never trips.
  - cycle 191: engine proposed no files and no appends -- output budget exhausted (in=42147, out=42825, of which reasoning=25305); the task likely requires rewriting more than one large file at once
  - cycle 192: probes: regression on frozen probe 'probe-kernel-selftest': 100.00 -> 40.00
- **Detected at cycle:** 202


## FA-012 — Repeated rejection: gate-weakening
- **Task:** GROWTH (G-001): Implement a mailbox acknowledgment protocol with timestamp, stat
- **Failure class:** gate-weakening
- **Count:** 4 rejection(s)
- **Representative reasons:**
  - cycle 173: deterministic: kernel is 3150 lines, over the 3000 cap
  - cycle 174: mutate:glm failed after 4 attempts: The read operation timed out
  - cycle 175: review:deepseek: Adds a probe rubric (state/probe-proposals/probe-mailbox-ack-basic/rubric/check.py) inside the repository/worktree, violating Principle 4's physical-invisibility invariant for rubrics.
  - cycle 175: review:deepseek: The mutation engine can now read the exact scoring logic and craft organ behavior to pass the probe, weakening the internal probe as a genuine gate.
- **Detected at cycle:** 202


## FA-013 — Repeated rejection: unclassified
- **Task:** GROWTH (G-002): Add syscall-level dependency observation to strengthen the closu
- **Failure class:** unclassified
- **Count:** 5 rejection(s)
- **Representative reasons:**
  - cycle 179: deterministic: kernel is 3013 lines, over the 3000 cap
  - cycle 180: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
  - cycle 181: deterministic: kernel is 3030 lines, over the 3000 cap
- **Detected at cycle:** 202


## FA-014 — Repeated rejection: unclassified
- **Task:** GROWTH (self-detection): Add a reflect sub-mode that scans the kernel for unexer
- **Failure class:** unclassified
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 183: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
  - cycle 184: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
  - cycle 185: deterministic: kernel is 3071 lines, over the 3000 cap
- **Detected at cycle:** 202


## FA-015 — Repeated rejection: gate-weakening
- **Task:** Make meristem/breaker.py delegate counting to the journal-query organ when that 
- **Failure class:** gate-weakening
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 112: review:deepseek: When the journal-query organ is active, breaker.rejections_for/faults_for use unchecked organ output instead of the old deterministic journal counts; an organ that returns lower counts (or an empty re
  - cycle 112: review:deepseek: Malformed organ responses are silently accepted as zeros via result.get(..., 0); a missing key returns (0,0) instead of raising and falling back, converting a hard failure into silent default-continue
  - cycle 113: review:deepseek: The breaker's retry budget now trusts counts returned by the journal-query organ whenever it is active, and the only validation is key-presence; a stale, undercounting, or malformed organ result (e.g.
  - cycle 113: review:deepseek: The organ's count semantics (including the P-016 fault-exclusion rule) are not part of this review closure; the diff asserts equivalence with the inline logic without demonstrating it, so reviewers ca
  - cycle 114: review:deepseek: The breaker's parking gate no longer derives rejection/fault counts directly from the journal whenever the journal-query organ is active; it trusts organ-supplied cycles and faulted_cycles. An active 
  - cycle 114: review:deepseek: The organ result is only type-checked at the outer list level, not validated for task ownership, row shape, or consistency with the journal. Malformed list contents raise outside the try/except, so th
- **Detected at cycle:** 202


## FA-016 — Repeated rejection: closure-budget
- **Task:** Make meristem/breaker.py delegate to the journal-query organ when it is active, 
- **Failure class:** closure-budget
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 97: deterministic: review closure is ~51180 tokens, over the 50000 budget -- split the organ before growing it
  - cycle 98: deterministic: review closure is ~50916 tokens, over the 50000 budget -- split the organ before growing it
  - cycle 99: deterministic: review closure is ~51360 tokens, over the 50000 budget -- split the organ before growing it
- **Detected at cycle:** 202


## FA-017 — Repeated rejection: gate-weakening
- **Task:** Make the dating in body/organs/memory-graph/extract.py actually take effect: eve
- **Failure class:** gate-weakening
- **Count:** 3 rejection(s)
- **Representative reasons:**
  - cycle 63: review:deepseek: The new _parse_register function no longer checks that the node id starts with the expected prefix (P, G, B) for the respective register file. The old code enforced that only entries with the correct 
  - cycle 63: review:deepseek: Even though the primary goal is to fix dating, the removal of the prefix check is an unintended relaxation that could allow erroneous or misattributed entries into the graph.
  - cycle 64: review:sensenova: Organ nodes lose the 'probes' field entirely — information that downstream consumers (decay, edge rules, or review gates checking probe coverage) may depend on is silently removed. This narrows what t
  - cycle 64: review:sensenova: Cycle node titles are truncated to 80 characters (`str(row.get('why', ''))[:80]`). Edge rules that match pattern/gap ids in cycle titles could miss matches in the truncated portion, weakening the memo
  - cycle 65: review:deepseek: Removal of the 'probes' field from organ nodes reduces the information available to the memory graph, weakening the ability to track probe relationships and potentially undermining immune system monit
  - cycle 65: review:deepseek: Truncation of cycle node title to 80 characters may lose information, though this is a minor concern.
- **Detected at cycle:** 202


## FA-018 — Repeated rejection: unclassified
- **Task:** Strengthen the memory-graph selfcheck so it would have caught this. In addition 
- **Failure class:** unclassified
- **Count:** 5 rejection(s)
- **Representative reasons:**
  - cycle 69: review:deepseek: Changed error response structure from nested {'ok': false, 'result': {'error': ...}} to flat {'ok': false, 'error': ...}, breaking any consumer that expects the old format and making errors harder to 
  - cycle 69: review:deepseek: Removed 'current_cycle' from op_query and op_stale results, reducing information available to callers.
  - cycle 70: review:sensenova: op_query removes the node-existence validation: the old code returned {"ok": false, ...} when the requested node_id was not found; the new code silently returns {"ok": true, "result": {"node": null, .
  - cycle 70: review:sensenova: op_explain removes the same node-existence validation: the old code returned {"ok": false, ...} for a missing node; the new code proceeds with node=None, returning zeroed-out fields (activation 0.0, l
- **Detected at cycle:** 202


## FA-019 — Repeated rejection: gate-weakening
- **Task:** Externalize the failure-history aggregation and pattern-class detection logic fr
- **Failure class:** gate-weakening
- **Count:** 2 rejection(s)
- **Representative reasons:**
  - cycle 202: mutate:glm failed after 4 attempts: HTTP Error 429: Too Many Requests
  - cycle 203: review:deepseek: The new classifier narrows what it inspects: it no longer scans per-reviewer `rejected_by[*].reasons`, and it drops the old broad keywords (weaken/gate/invariant/secret/protected/root/substrate/closur
  - cycle 203: review:deepseek: The organ's selfcheck no longer exercises classification at all; it just runs aggregate on /dev/null. A completely broken `_classify` would pass the organ's own health check, removing a prior validati
- **Detected at cycle:** 203


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
