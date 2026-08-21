# Probe: mailbox-ack-expiry

## What this probe tests

The mailbox-ack organ must correctly handle expiry of acknowledged items.
Given an acknowledged item with a short expiry window, the organ should:

1. Report the item as "acknowledged" immediately after acknowledgment.
2. Report the item as "expired" after the expiry period has passed.
3. Support querying the status of an item by its identifier.

## How to invoke

1. Acknowledge an item with a short expiry:

       {"op": "ack", "item": "test-expiry-item", "expiry_seconds": 1}

2. Immediately query its status:

       {"op": "status", "item": "test-expiry-item"}

   Expected: {"ok": true, "status": "acknowledged", ...}

3. After the expiry period (>= 1 second), query again:

       {"op": "status", "item": "test-expiry-item"}

   Expected: {"ok": true, "status": "expired", ...}

## Scoring criteria

- Score 100 when the organ correctly transitions an item from "acknowledged"
  to "expired" after the expiry period, and reports "acknowledged" before.
- Score 50 when the organ supports expiry but does not support status queries.
- Score 0 when the organ does not support expiry or reports an incorrect status.

## Rubric location

The scoring rubric (check.py) MUST be authored directly in the eval vault,
not in this repository. See rubric/README.md in this directory.
