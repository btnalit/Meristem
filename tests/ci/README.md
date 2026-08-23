# tests/ci -- SA/CA consistency assertions (§17.8)

Machine form of docs/MERISTEM-V5-SPEC.md §17.8 ("一致性断言集"). Two files:

- `test_spec_assertions.py` -- **SA-1..SA-5**. Authority source is the spec
  text itself. Run whenever `docs/` changes.
- `test_code_assertions.py` -- **CA-1..CA-12**. Authority source is the P0-a
  repository (real files, real ledger). Run on every push.

`soil/authority-matrix.json` (checked by SA-2/SA-3) is the machine-readable
transcription of §16's table. **Per §17.8.1 it is a transcription, not yet
authoritative** -- the §16 Markdown table in the spec remains the sole
authority at this stage; SA-2 is what keeps the two from drifting apart.

Stdlib only. No third-party dependencies.

## How to run

```bash
# preferred
python -m pytest tests/ci -v -s

# pytest not available -- IMPORTANT: you must pass -t tests, otherwise the
# test modules' relative imports (`from . import ...`) fail with
# "attempted relative import with no known parent package". This is because
# tests/ci/__init__.py exists but tests/__init__.py deliberately does not
# (tests/ is owned by other agents' work in this repo) -- unittest's
# discovery needs an explicit top-level-dir to treat "ci" as the importable
# package root.
python -m unittest discover -s tests/ci -t tests -v
```

Do not run `python -m unittest discover` (bare, from repo root) or
`python -m unittest tests.ci.test_spec_assertions` -- both fail on this
machine because a *different*, unrelated package literally named `tests`
already exists in this Python installation's site-packages
(`.../site-packages/tests`), which shadows the local `tests/` directory for
any *absolute* `import tests...`. The two invocations above sidestep this
entirely (pytest by its own rootdir-insertion; unittest by pointing
`-t` at `tests/` explicitly), and neither depends on `tests/` having an
`__init__.py`.

## What each assertion checks, and its current status on a fresh P0-a checkout

### SA-x (spec-side)

| ID | Checks | Status here | Why |
|---|---|---|---|
| SA-1 | No `journal` residue in the spec except historical/self-referential mentions | **PASS** | All 7 current hits are whitelisted (v3.1 references, the §13.4 v3-archive inventory line, a narrative aside, the §17.7 scan-list's own declaration, and one §18 change-log row) |
| SA-2 | §16 Markdown table == `soil/authority-matrix.json`, cell-for-cell (after stripping `**`/`` ` `` markup) | **PASS** | 18 rows x 7 columns, verified equal |
| SA-3 | Every one of the 18 rows has all 7 permission cells filled (`—` and qualified values like `摘要` count as filled; only truly blank cells fail) | **PASS** | Checked against both the Markdown table and the JSON |
| SA-4 | Every name in §17.7's scan word-lists has its occurrences "registered" (see **Known limitation** below) | **FAIL (expected, see below)** | |
| SA-5 | §1.2's `is_ignition_event` code block matches its implementation in `substrate/` byte-for-byte | **FAIL by design** | `substrate/pipeline.py` (the predicate's sole implementation point per §1.2/§12.0.2) does not exist yet -- wave 2 work. This assertion is deliberately written to FAIL loudly, not skip, so it cannot be missed once wave 2 lands the file. |

**SA-4 known limitation (reported, not silently resolved):** §17.8.2 describes
SA-4 only as "each managed name's occurrences are registered" -- it does not
give a machine-checkable predicate the way SA-1..3/5 do. This implementation
takes the closest concrete spec text, §17.7's shell-loop predecessor, and
mechanizes it as written: list ① (current names) must be used at least once
outside their own scan-list declaration; list ② (retired names) must have
**zero** hits outside (a) the scan-list's own declaration, (b) the §18
change-log section, and (c) a contiguous blockquote paragraph carrying an
explicit "本轮勘误" annotation -- the three exemptions §17.7 literally states
("除 §18 勘误行与显式的「本轮勘误」注记外，命中数必须为 0"; (c) is grouped by
paragraph, not single line, because the annotation typically opens a
multi-line remark and the retired term it explains often lands on a later
line of that same remark).

Run today, this finds **5 of the 11** retired words with hits outside those
three exemptions: `journal`, `control/`, `probe.json`, `panel=None`,
`is_ignition_event(ev, task)`. Reading those hits (the test prints every
offending line number), **all of them are legitimate historical/explanatory
prose** -- describing what v3.1 used to do, or what an earlier *draft*
("初稿") of this spec got wrong before a fix ("是笔误" -- semantically
identical to "本轮勘误" but not the literal string §17.7 names as an
exemption). SA-1 solves exactly this problem for `journal` specifically, via
a hand-verified per-line whitelist; §17.7 gives no equivalent per-word
whitelist for the other retired words, and no exemption for "初稿"/"是笔误"
narration. Inventing one unilaterally here would be deciding a spec ambiguity
rather than reporting it. **This is flagged in the dispatch report as a
discovered tension for a human to resolve** (either broaden §17.7's
exemption wording, or accept SA-4 as advisory/informational for the
non-`journal` retired words). The assertion is left failing rather than
silently patched green.

### CA-x (code-side)

| ID | Checks | Status here | Why |
|---|---|---|---|
| CA-1 | Matrix rows with a concrete `path`: `seed_write=✗` -> path not in `SEED_WRITABLE`; `seed_write=✓` -> path in it | **PASS** | `meristem/engine.py` exists (wave-2-adjacent work landed during this session); 7 of 18 matrix rows have a resolvable single path and were checked, the other 11 are conceptual/abstract and skipped per-row (not guessed at) |
| CA-2 | Matrix rows with `soil_write=✓`: on-disk owner is `soil`, no group/other write bits | **SKIPPED (no UID separation)**, `SECURITY_ASSURANCE=BEST_EFFORT` | Windows has no POSIX UID separation (`os.name != "posix"`), **and** `substrate/probe_runner.py`'s worker-isolation model means there's no soil/worker UID distinction to check even in principle yet. Both reasons printed. |
| CA-3 | `meristem/`, `body/` contain no `vault/`/`soil-ledger`/`scoreboard` path-constants | **PASS** | Both directories now exist and were scanned (7 `.py` files); zero matches |
| CA-4 / I9 | `substrate/` does not import `meristem` | **PASS** | 3 `.py` files scanned, zero matches |
| CA-5 | `seed/probe-proposals/*.json` has no `entrypoint` field (recursive) | **PASS** | 1 proposal file scanned |
| CA-6a | Real ledger's `kind` values ⊆ spec-declared set | **SKIPPED (no ledger yet)** | `state/soil-ledger.jsonl` does not exist |
| CA-6b | `tests/fixtures/soil-ledger-all-kinds.jsonl` covers exactly the spec-declared kind set | **PASS** | Fixture covers all 7 declared kinds, invents none |
| CA-7 | No `accepted_fitness` with `calibration:true`; every fitness-class event has the 6 mandatory §8.2 fields | **SKIPPED (no ledger yet)** | |
| CA-8 | `state/` files match `^state/soil-[a-z0-9-]+\.jsonl$`; no symlinks; no hardlinks resolving into `seed/`/`soil/` | **SKIPPED** (`state/` directory does not exist yet) | |
| CA-9 | `probe_manifest_sha` on a Measurement == that probe's frozen registration | **SKIPPED (no ledger yet)** | Also: the frozen probe registry is soil-private with no defined on-disk path in this wave, so even with a ledger there is nothing yet to compare against |
| CA-10 | One promotion's event chain is field-correspondent, not just count-equal | **SKIPPED (no ledger yet)** | |
| CA-11 | manual-accept and panel-accept produce identical event sequences except `verdict.authority` | **SKIPPED (no ledger yet)** | *Not explicitly named in the dispatch's "needs real ledger" list (CA-6a/7/9/10/12) -- treated identically here since it has the same dependency (and the ledger schema has no `verdict`/`authority` field at all yet). Flagged as an assumption in the dispatch report.* |
| CA-12 | Projections' `source_ledger_tail_hash` == current ledger tail hash | **SKIPPED (no ledger yet)** | Also missing: `soil/report-facts.json`, `seed/feedback.json` |

**The 7 real ledger/fixture/UID-dependent SKIPs above are the honest result on
a fresh P0-a checkout, not a placeholder to be filled in by faking data.**
Per §17.8.3 ("跳过必须可见；静默跳过等于没有"), every SKIP prints its reason and
is a real `unittest`/`pytest` SKIP (visible in the run summary), never a
silently-true assertion.

## `SECURITY_ASSURANCE`

Computed by `determine_security_assurance()` in `test_code_assertions.py`
(importable via `from tests.ci.test_code_assertions import
determine_security_assurance` once `tests/__init__.py` exists elsewhere in
the repo, or via a relative import from within `tests/ci`). It is:

- **printed** to stdout every time CA-2 runs (`SECURITY_ASSURANCE=<value>
  (<reason>)`), and
- **returned** as `(value, reason)` for any external caller.

`BEST_EFFORT` today, because this is Windows (no POSIX UID) *and*
`substrate/probe_runner.py`'s execution isolation has no soil/worker UID
model to check even on POSIX yet. It becomes `FULL` only once CA-2 has
**actually run for real and passed** (§17.8.3: "CI 全绿 ≠ 隔离已被证明" -- a
skip anywhere in CA-2 must never be read as isolation having been verified).

Per §17.8.3, this value is supposed to also be written into `state/soil-ledger.jsonl`
and `soil/report-facts.json` so it survives into the rendered report -- **that
wiring is out of scope for this wave** (neither file/writer exists yet); it is
flagged as a known gap for whoever lands the ledger-writing code.

## `tests/fixtures/soil-ledger-all-kinds.jsonl`

Feeds **only** CA-6b. Never read by CA-6a/7/9/10/12 (those use the real
`state/soil-ledger.jsonl`), and must never be copied into `state/` --
CA-8's strict `^state/soil-[a-z0-9-]+\.jsonl$` filename regex would reject it
there anyway, since its name doesn't match that pattern. It is intentionally
seed-writable (`tests/` is in `SEED_WRITABLE`, §10.1) -- per §17.8.2's own
note, tampering with it only makes the seed's own CI run red, it feeds no
authority-bearing判据.

The kind set it covers (`observed_fitness`, `promotion_intent`,
`accepted_fitness`, `promotion_committed`, `promotion_outcome`, `cycle`,
`prompt_over_budget`) is sourced from explicit `"kind":"..."` literals in the
spec (§8.2's event envelope, §10.2's `pipeline.py` pseudocode, §10.1's
`kind:"cycle"`/`kind:"prompt_over_budget"` mentions) -- see the citation
comment above `SPEC_DECLARED_LEDGER_KINDS` in `tests/ci/__init__.py`. It
deliberately excludes `"kind":"probe"` (that's the *scoreboard*'s kind, §8.3,
a different file/schema than the ledger CA-6 is scoped to) and
`"kind":"fitness"` (an explicitly retired/never-valid value, §8.2 erratum).

## Known limitations / best-effort caveats

- **CA-2** cannot verify UID isolation on non-POSIX platforms or before
  `substrate/probe_runner.py` exists; see `SECURITY_ASSURANCE` above.
- **CA-8**'s hardlink check (`path_has_symlink_or_bad_hardlink` in
  `tests/ci/__init__.py`) is best-effort: it detects `st_nlink > 1` and checks
  whether the resolved path falls under a protected directory, but cannot
  fully verify hardlink *identity* against every file in `seed/`/`soil/`
  without walking and inode-matching the whole tree. Documented, not silently
  assumed complete.
- **CA-1** only checks the 7 (of 18) matrix rows that carry a single,
  concrete, spec-derived `path` in `soil/authority-matrix.json`. The other 11
  rows are conceptual (e.g. "active probe cases", "TaskDecision") and have no
  one-to-one filesystem path to check against `SEED_WRITABLE` -- skipped
  per-row rather than guessed.
- **SA-5** is deliberately a hard FAIL, not a SKIP, while
  `substrate/pipeline.py` is absent -- per the wave-1 dispatch instruction,
  this needs to be impossible to miss once wave 2 lands the implementation.
