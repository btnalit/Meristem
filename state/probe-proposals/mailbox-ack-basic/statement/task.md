# Probe: mailbox-ack-basic

## What this probe tests

The mailbox-ack organ must correctly acknowledge a mailbox item. Given a
mailbox item identifier, the organ should:

1. Record an acknowledgment with an ISO-8601 timestamp.
2. Set the item's status to "acknowledged".
3. Return the acknowledgment record including timestamp, status, and item.

## How to invoke

Call the mailbox-ack organ over its ABI with:

    {"op": "ack", "item": "PARKED: test item (test reason)"}

The organ should return:

    {"ok": true, "acknowledged": true, "timestamp": "<ISO-8601>", "status": "acknowledged", "item": "PARKED: test item (test reason)"}

## Scoring criteria

- Score 100 when the organ returns a valid acknowledgment with a non-empty
  timestamp and status set to "acknowledged".
- Score 0 when the organ fails to acknowledge, returns missing fields, or
  returns an invalid timestamp.

## Rubric location

The scoring rubric (check.py) MUST be authored directly in the eval vault,
not in this repository. See rubric/README.md in this directory.
