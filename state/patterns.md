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
