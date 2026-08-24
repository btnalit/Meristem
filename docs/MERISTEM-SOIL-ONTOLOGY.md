# Meristem Soil Ontology

## Purpose

This document defines the entities and state boundaries used by the soil-learning loop. It is a design contract, not a prompt for the seed.

## Entities

- **Task**: a content-addressed objective declaration. Its identity is derived from the agenda declaration and is immutable for the task lifetime.
- **Attempt**: one soil-owned execution of one task at one soil cycle. It links provider/model telemetry, candidate output, measurement, outcome, and feedback.
- **Candidate**: a commit produced in an isolated worker worktree. A candidate is not the main branch and is not progress by itself.
- **Measurement**: soil-owned probe output for a candidate, represented by `observed_fitness`. It may show improvement without being accepted.
- **Accepted fitness**: measurement whose candidate passed all promotion gates, was merged, and is linked to the complete promotion transaction. This is the only progress counted by ignition/H1.
- **Outcome**: the terminal soil decision for a candidate. `REJECTED` is a semantic/panel rejection and may consume task quota. `PREFLIGHT_GATED` means the candidate was measured but the no-promotion test mode intentionally blocked autonomous promotion; it never consumes semantic quota.
- **Projection**: replaceable, bounded views derived from the append-only ledger. It cannot create facts and must carry the current ledger fingerprint.
- **Runtime manifest**: a soil-owned snapshot binding task identity, ledger hash, projection hashes, ownership, modes, and fail-closed policy. It must be refreshed after ledger/projection writes and verified before mutation.
- **Authority**: `soil` owns measurement, task state, and promotion transaction. A manual panel is a test adapter only; formal H1 requires a soil-owned autonomous panel.

## State transitions

```text
open -> unfulfilled        semantic failure
open -> parked             semantic failure threshold reached
open -> blocked            mechanism failure
open -> promotion_gated   valid measured candidate blocked by preflight policy
open -> fulfilled          accepted_fitness after merge
```

`promotion_gated` is terminal for the current preflight attempt: the soil must preserve the candidate evidence and refuse blind replacement mutation. Formal autonomous H1 uses a different authority/mode and may transition a valid candidate through the normal promotion transaction.

## Invariants

1. `observed_fitness` is evidence, not progress.
2. `accepted_fitness` requires a matching `promotion_intent`, scoreboard commit, and `promotion_committed` for the same attempt/source/commit/parent.
3. No preflight execution may write accepted or promotion-committed events.
4. A preflight-gated candidate must not consume semantic task quota.
5. A normal rejected/regressed/unfulfilled/canary-rejected candidate with `counts_against_task_quota=true` does consume semantic quota.
6. Projection and manifest hashes must match the current ledger before the next mutation.
7. A promotion-gated task cannot start another blind mutation cycle.
8. Formal H1 must not depend on `input()` or human approval; autonomous soil authority is a separate, explicitly tested panel adapter.
9. Rollback receipts describe and bind a real operator-executed rollback; receipt validation is not rollback execution.
10. Provider success, parser success, strategy variation, or candidate creation alone never establishes H1.

## H1 readiness boundary

The system may leave preflight only when the autonomous panel, promotion transaction, rollback/recovery paths, projection/manifest lifecycle, and the complete acceptance predicate have deterministic tests and a real end-to-end rehearsal. A single improved observation in preflight proves candidate capability evidence; it does not by itself prove that autonomous H1 is safe.
