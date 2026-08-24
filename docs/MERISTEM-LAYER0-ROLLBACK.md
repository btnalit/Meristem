# Layer 0 rollback and H1-preflight runbook

## Scope

This runbook applies only to the soil-owned, single-task, no-promotion
H1-preflight. Formal H1 remains frozen until its gate is separately approved.
The seed cannot invoke rollback, reset a parked task, or change authority.

## Required pre-cycle receipt

Record a soil-owned receipt containing:

- task_id and attempt_id;
- current HEAD / stable restore commit;
- generation and soil-cycle;
- ledger tail hash;
- seed feedback and frozen-probe-registry hashes;
- runtime-manifest hash;
- worker and gateway identities.

If any required projection or manifest is absent/mismatched, refuse mutation.

## Rollback phases

| Phase | Action | Promotion allowed |
|---|---|---:|
| `candidate_unmerged` | discard isolated candidate; preserve ledger evidence | no |
| `merged_pre_accept` | soil-autonomous restore to stable commit; append rollback receipt | no |
| `accepted_pre_commit` | soil-autonomous reconcile accepted event, restore stable generation, append receipt | no |
| `promoted_bad_candidate` | soil-autonomous restore stable generation and preserve bad candidate evidence | no |

A rollback receipt must pass `substrate.rollback.validate_receipt()` and bind:
`task_id`, `attempt_id`, restored commit, generation, soil cycle, ledger tail
hash, task state, and `status=rolled_back`.

## Parked task policy

A parked task is never retried implicitly. The preferred path is a new task
identity. Any reset/unpark must be a separate root/manual action with a reason,
old task id, new generation/reset id, and explicit cleanup/read-back evidence.
The seed may not edit agenda/task text to bypass the parked projection.

## Counterfactual drills

Before formal H1, exercise in a disposable controlled workspace:

1. candidate rejected before merge;
2. interruption after merge but before accepted fitness;
3. interruption after accepted fitness but before promotion commit;
4. post-promotion semantic regression;
5. missing/mismatched runtime projection.

The no-promotion counterfactual drills (1, 2, 5) must end with either
`candidate_unmerged` or a validated rollback receipt and must not create
`accepted_fitness`, `promotion_committed`, or an ignition event. The autonomous
promotion/recovery integration rehearsal (3, 4) intentionally creates and then
reconciles/rolls back those events in an isolated repository; it is not formal
production H1 acceptance.
