# Meristem

> **Everything grows from what remains small.**

A minimal self-growing intelligence kernel: it continuously expands its
capabilities while keeping the machinery of its own growth small enough to
review completely.

A meristem is the tissue that produces an entire tree while itself staying a
few millimetres across. That is the architecture, not a metaphor.

```
core size     ────────────────────────────▶  constant
capability    ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱▶  unbounded
```

## The thesis

The safe rate of recursive self-improvement is bounded by **reviewability**.
A gate that cannot see the whole of what it judges is not a gate — and a
system that expands faster than its immune system can comprehend has already
lost the immune system; it just does not know yet.

So the ceiling is not "how big may the system get". It is:

> **Any single mutation's review closure must fit in one review context.**
> If it does not fit, the organ splits first.

The tree may reach hundreds of thousands of lines. Changing one organ still
means reviewing kernel + that contract + that organ + its probes — and nothing
else. *Global small core, locally complete review closures.*

## Layout

| Path | Tier | Evolvable? |
|---|---|---|
| `root/` | root of trust | **never** — panic, succession, generation registry |
| `substrate/` | soil | only by succession, never in place |
| `meristem/` | core (≤3000 lines) | yes, through the gates |
| `meristem/gates/` | immune | yes, but **may improve, may not weaken** |
| `control/` | constitution & steering | constitution/purpose human; agenda seed-owned |
| `body/` | organs | **ships empty** — grows on demand |
| `state/` | audit memory | three JSONL records are append-only, forever |
| *(outside the repo)* | eval vault | rubrics, held-outs, fixtures — invisible to the engine |

## Two loops

- **Loop A — optimisation.** *Where am I doing badly?*
  failure → failure class → probe → sentinel → mutation → gates → promotion
- **Loop B — growth.** *What can I not yet do, and what is worth becoming
  able to do?*
  gap → probe first → grow organ → gates → register → use → measure → keep,
  evolve, or prune

Six operators, all first class: `mutate`, `grow`, `prune`, `externalize`,
`internalize`, `reproduce`.

## Quick start

```bash
python bootstrap.py                       # create the eval vault OUTSIDE the repo
export MERISTEM_VAULT=/path/to/vault      # make it permanent
export DEEPSEEK_API_KEY=...               # any OpenAI-compatible endpoint

python -m unittest discover -s tests      # 25 kernel self-tests
python -m meristem.loop selftest          # immune self-test: do the gates fire?
python -m meristem.loop status            # cycles, LOC, spend, next task
python -m meristem.loop cycle             # run one evolution cycle
python substrate/supervisor.py promote    # canary boot, then promote
python root/panic.py engage               # full stop
```

Tasks come from `control/agenda.md`. In P0 the human is the first reflect.

## Design decisions worth knowing

**No coding-agent CLI.** A coding agent exists because its target is too big
for one context, so it explores — re-sending accumulated context each turn.
The seed *fits*. Paying to discover "where is the relevant code" when the
answer is "all of it, here" costs roughly 6–10× the tokens before model-tier
price differences even apply. Tier A is one structured call returning complete
replacement files; Tier B (a minimal read/write/run loop) and Tier C (a
commodity CLI) exist as an escalation ladder. **Escalation is data**: a task
that needs B or C means the task was underspecified or the kernel is getting
hard to modify.

**Promotion authority lives in the substrate, not the kernel.** The kernel
proposes a candidate; the substrate runs its own protected-path check with
plain git and no kernel code, boots a canary, and only then fast-forwards.
The code under review does not decide what gets in.

**Rubrics are physically invisible.** Not "the prompt is told not to look" —
the vault is outside the repository and outside the worktree, only
`meristem/gates/` may reference it, and a deterministic invariant enforces
that every cycle.

**Human gates are prosthetics.** Each carries demotion criteria and steps
down on evidence: prior approval → post-hoc audit → notification only.
Tightening is automatic and needs no evidence; loosening always needs both
evidence and a human. The ratchet turns freely only toward safety.

**The soil is not a parent; it is physics.** The seed grows wherever it
chooses. It cannot repeal gravity.

## Status: P0

Not yet an RSI system — the minimal base from which recursive improvement can
safely begin. Birth is defined by seven runtime criteria, not by "the code is
written": 20 consecutive cycles, 3 accepted self-modifications of the kernel,
every gate tripped at least once by a fixture, an intentional boot failure
auto-refused, the engine measurably unable to read rubrics, six-question
journal entries by construction, and one trivial organ taken through the full
germline lifecycle. See `control/strategy.md`.

Known gaps are tracked honestly in `state/gaps.md` rather than claimed as
solved.

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
