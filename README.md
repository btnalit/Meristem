# Meristem

> **Everything grows from what remains small.**

A meristem is the tissue that produces an entire tree while itself staying a
few millimetres across. That is the architecture, not a metaphor.

## Status — read this first

**v3.1 is retired.** It ran 400 cycles, promoted 37 changes, and produced
**zero** measured improvements. Its kernel, organs, gates and tests were
removed on 2026-08-23; the full history is preserved in this repository and in
a verified `git bundle` archive off-repo. What the run actually demonstrated is
written up in the spec: the scale was binary, so `improved` was **structurally
impossible to produce**, and the system reported progress anyway.

**v5 is specified, and P0-a is partly built.** The authority is:

- **[`docs/MERISTEM-V5-SPEC.md`](docs/MERISTEM-V5-SPEC.md)** — `v5.10-frozen`.
  Per-module responsibilities, invariants, data schemas, function signatures,
  and acceptance criteria. Written to be built from.
- [`docs/MERISTEM-CHECKLIST.md`](docs/MERISTEM-CHECKLIST.md) — task log.

| P0-a component | State |
|---|---|
| seed spine (`meristem/`, 6 modules) | implemented |
| `substrate/probe_runner.py`, `fitness.py` | implemented |
| `substrate/pipeline.py`, `soil_state.py` | implemented |
| `manual-cycle` / `ignition-status` | implemented |
| ignition organ + internal probe | implemented |
| `seed/agenda.md` + `soil/p0a-task-h1.json` | implemented (current task declaration; prior `soil/p0a-task.json` remains historical) |
| v3.1 entry points (`heartbeat`/`run`/`promote`/…) | **deleted** — `supervisor.py` went 1588 → 331 lines; `run_meristem.sh` removed. Gating them behind a flag was the wrong fix: a locked second entry point is still a second entry point. P0-c redesigns keeper/breaker for v5 rather than thawing v3.1's |
| panic latch (`root/panic.py`) | **now read by v5** — `manual-cycle` refuses while it is engaged; `ignition-status` stays readable (a stopped system is exactly when you need the criterion) |
| **anchor probe (5 hidden cases)** | **not written — human-authored, by design** |
| `budget.py`, `model_gateway_server.py`, `model_gateway_client.py`, `autonomous_panel` | implemented |
| `soil/report_renderer.py` | not implemented |
| **organ filesystem/network isolation (§15.6 C6, P0-a tier)** | **implemented and verified on the server** — `worker` UID, no supplementary groups, private netns, isolated organ copy; `SECURITY_ASSURANCE=FULL`. Falls back to a loudly-reported `best_effort` where the host cannot enforce it |
| calibration control group (§12.0.1) | **run on the server: 40.0 → 60.0, `improved`** — the apparatus measures |
| **full P0-a loop (bounded, H1)** | **P0-a acceptance reached in one Agnes autonomous cycle; H1 owner decision pending** |

**Current runtime snapshot (2026-08-25):** task `948d2c0b3aec5b81` completed one bounded Agnes autonomous P0-a cycle at soil_cycle 50; primary `40→60`, anchor `20→20`, `accepted_fitness=1`, `promotion_committed=1`, and `ignition events=1`. H1 remains frozen pending final owner decision. Runtime projections and manifest are deployment state and are intentionally ignored by Git.

## The thesis

The safe rate of recursive self-improvement is bounded by **reviewability**. A
gate that cannot see the whole of what it judges is not a gate.

v3.1 enforced this with a hard ceiling of 3000 kernel lines. That ceiling
became a monotonic counter with no rolling window, and eventually a deadlock
the system could not repair from inside. v5 replaces it with **three
comprehension budgets** — mutation closure, prompt surface, contract surface —
each with a data structure and an assertion rather than a number in a doc.

The v5 first principle is narrower and testable:

> **Soil is the set of things the seed may not author, because once it can
> author them it can fake its own progress.**

Every module assignment in the spec is derived by asking one question of it:
*if the seed could change this, could it thereby make an unimproved change look
improved?* If yes, it is soil.

## Layout

| Path | Tier | Status |
|---|---|---|
| `root/` | root of trust — panic latch, succession | **kept from v3.1, unchanged** |
| `substrate/` | soil — supervisor, and five modules v5 adds | `supervisor.py` kept; the rest **not written** |
| `soil/` | soil-private — model policy, panel policy, report facts | `model-policy.toml` only |
| `seed/` | seed-visible — constitution, agenda, narrative, feedback | `model-interface.json` only |
| `meristem/` | the seed itself | **not written** |
| `body/organs/` | organs | **not written** |
| `state/` | soil ledgers, `state/soil-*.jsonl` | runtime; created by soil, not tracked |
| *(outside the repo)* | eval vault | rubrics and held-outs, invisible to the engine |

## Design decisions worth knowing

**Promotion authority lives in the substrate, not the kernel.** The seed
proposes a candidate; the soil measures it, judges it, and only then
fast-forwards. The code under review does not decide what gets in. In v5 the
seed also no longer writes any ledger, renders any score, or runs any probe.

**Rubrics are physically invisible.** Not "the prompt is told not to look" —
the vault lives outside the repository and outside the worktree, and only soil
code may resolve its path.

**The seed may narrate direction; it may not narrate results.** v3.1's
`proved_better_by` claimed a proven improvement 166 times and it never once
happened. The file was append-only and the data was never tampered with — the
lie was in the rendering step. Tamper-proofing does not catch that. So in v5
the report is a soil-rendered derivative of soil-owned facts.

**The soil is not a parent; it is physics.** The seed grows wherever it
chooses. It cannot repeal gravity.

## Lineage

Meristem is not a fork. Four projects were read as quarries, not parts:

- **[Ouroboros](https://github.com/razzant/ouroboros)** (MIT) — the running
  precedent for reviewed core evolution. Constitutional text, the review
  checklist, quorum discipline, deterministic-before-expensive ordering, and
  the panic invariant are adapted from its `BIBLE.md` with thanks.
- **[PenguinHarness](https://github.com/Prism-Shadow/penguin-harness)**
  (Apache-2.0) — probe methodology: discriminating calibration, statement /
  rubric isolation, freeze rules.
- **[pi](https://github.com/badlogic/pi-mono)** (MIT) — reference for how thin
  an LLM interface and a tool loop can be.
- **[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)**
  (MIT) — contracts-as-seams, the shape the body grows into.

## License

MIT — see [LICENSE](LICENSE).
