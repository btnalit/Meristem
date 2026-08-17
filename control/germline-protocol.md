# Germline: the morphogenesis protocol

The seed is born knowing **how** to grow an organ. It is never told **which**
organs to grow. `body/` ships empty, and stays empty until real work demands
something.

Without this protocol the first organ would force the model to invent, on the
spot, a directory layout, a manifest shape, a calling convention, a
registration flow, and a retirement path. Six months later organ A speaks
stdin, B speaks HTTP, C speaks a file queue — and the tree did not grow, it
went feral. This protocol defines *what qualifies as a Meristem organ*: not a
growth script, a rule of cell division.

The protocol itself is contract tier and may evolve. Its **validator** is
immune tier (`meristem/gates/germline_validate.py`) — relaxing what counts as
a legitimate organ is a gate weakening.

## Layout

```
body/organs/<organ-id>/
├── organ.json          # the manifest (below)
├── <implementation>    # anything the entrypoint needs
└── README.md           # what this organ is for, in one paragraph
```

## Manifest — `organ.json`

Every field is required.

```json
{
  "id": "word-count",
  "version": "1",
  "capability": "One sentence: what this organ makes possible.",
  "entrypoint": ["python", "main.py"],
  "input_schema":  {"text": "string"},
  "output_schema": {"words": "integer"},
  "dependencies": [],
  "probes": ["probe-word-count-basic"],
  "metrics": ["usage", "success_rate"],
  "lifecycle": "candidate"
}
```

- `dependencies` — other organ ids, plus `effect:<name>` for effectful calls
  (`effect:run`, `effect:urlopen`, `effect:connect`). **Every dependency must
  be explainable**: an edge found statically or observed at the registry that
  is absent here is a contract violation, not a warning.
- `probes` — ids in the eval vault. An organ may not reach `register` or
  `active` with none: growth without a measuring stick is not growth.
- `metrics` — what its utility is judged on, so pruning has evidence.

## Lifecycle

Strictly sequential; skipping a stage is refused.

```
candidate → calibrate → register → active → deprecating → archive
```

- **candidate** — grown in a worktree, gates not yet passed.
- **calibrate** — its probes exist and are scored; discriminating power checked.
- **register** — admitted to the capability registry.
- **active** — callable by the kernel and by other organs.
- **deprecating** — utility fell; still callable, no new dependents.
- **archive** — removed from service, lineage retained. Pruning is metabolism,
  not deletion: the record stays.

## ABI

One call shape, forever until the contract tier changes it:

```
stdin  : one JSON object matching input_schema
stdout : one JSON object matching output_schema
exit 0 : success; anything else is a failure with stderr as the reason
```

The organ runs with its own directory as the working directory. Every
invocation through the registry is an observed dependency edge and is
journalled — that is what makes the observed half of the closure real rather
than assumed.

## Selfcheck op

Multi-part organs — those with more than one source file — must support a
`selfcheck` op. When invoked with `{"op": "selfcheck"}`, the organ:

1. imports each of its own modules,
2. exercises every entry point with tiny fixtures,
3. returns `{"ok": true, "results": [...]}` on success, or
   `{"ok": false, "results": [...], "failures": [...]}` on failure.

This is the organ-level equivalent of the kernel's `selftest` command: a way
to prove the parts still fit together after a change. Single-file organs are
exempt — their entry point IS the whole surface.

The selfcheck op is part of the ABI: it uses the same stdin/stdout JSON
shape, runs with the organ's own directory as cwd, and is an observed
dependency edge when invoked through the registry. An organ that cannot
selfcheck cannot be trusted after a mutation touches it.

## Growing one (Loop B)

1. Real work reveals a capability gap → recorded in `state/gaps.md`.
2. **A probe is written first.** The measuring stick precedes the capability.
3. The organ is grown in a worktree: manifest, implementation, probes.
4. Gates run — closure here is kernel + new contract + organ + its probes,
   not the whole tree.
5. Registered, then used in real work.
6. Utility measured. Kept, evolved, or pruned.
