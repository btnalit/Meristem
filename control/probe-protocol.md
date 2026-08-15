# Probe staging protocol

The seed cannot write to the eval vault — rubrics are physically invisible
to the mutation engine by design (Principle 4). But the seed must be able
to author probes: Loop B's discipline is "the measuring stick precedes the
capability," and without a pathway to write one, the probe library stays
empty forever.

The staging area is the resolution: the seed writes proposals into the
repository; the gates promote validated proposals into the vault. The seed
never touches the vault directly.

## Staging layout

```
state/probe-proposals/<probe-id>/
├── probe.json          # metadata: id, capability_domain, organ, etc.
├── statement/          # what the probe asks — visible to the engine
│   └── task.md
└── rubric/             # how the probe scores — staging, not the vault
    └── check.py
```

## Rules

1. **Statement and rubric are separate directories.** The statement
   describes what is being tested; the rubric determines how it is scored.
   Keeping them separate mirrors the vault's own structure and ensures the
   scoring logic can be reviewed independently of the task description.

2. **A proposal is never itself the probe.** A proposal lives in the
   repository, visible to the mutation engine. A probe lives in the vault,
   invisible to it. The gates promote a validated proposal into the vault;
   the seed never writes to the vault directly. This preserves physical
   invisibility while giving the seed a pathway to grow its measuring sticks.

3. **probe.json carries at minimum:** `id`, `capability_domain`, and (for
   organ-specific probes) `organ`. Additional metadata may be added as the
   probe library evolves.

4. **rubric/check.py scores by behaviour, not by source inspection.** A
   probe that inspects an organ's source rather than invoking it over its
   ABI teaches nothing about whether the organ actually works. The rubric
   receives `{"workdir": "...", "probe": "..."}` on stdin and returns
   `{"score": float, "detail": "..."}` on stdout, matching the vault's
   executable rubric contract.

## Lifecycle

```
proposal (state/probe-proposals/) → gate review → promotion into vault
```

A proposal that passes gate review is copied into the vault by the gate
layer (the only code permitted to write there). The proposal directory
remains in the repository as lineage; revisions get new ids; nothing is
deleted.
