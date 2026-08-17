# mailbox-ack

Mailbox acknowledgment protocol organ. Provides structured tracking of
mailbox entries with timestamp, status (pending/acknowledged/expired),
and expiry. Replaces free-text mailbox lines with queryable, expirable
records.

## Operations

- `add` — create a tracked entry with TTL (default 24h)
- `ack` — acknowledge an entry by id (idempotent)
- `list` — return all entries (optionally filtered by status)
- `expire` — mark entries past their TTL as expired
- `selfcheck` — exercise all entry points with tiny fixtures

## State

State persists in `state.json` within the organ's directory. Each
invocation is a fresh process; the state file is the continuity.

## Lifecycle

Currently at `candidate` stage. Probes will be staged through
`state/probe-proposals/` and promoted to the vault by the gates before
the organ advances to `calibrate`. No rubric logic enters the mutation
surface.

## Retirement path

If the kernel grows its own mailbox tracking, this organ's pattern can
be internalized (budget-neutral) or pruned. Utility is measured by
invocation count and success rate from the journal.
