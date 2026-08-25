# P0-c pulse runbook

## Scope

This runbook covers the unattended pulse mechanism shipped in this wave:
`substrate/pulse-beat.sh`, `substrate/systemd/meristem-pulse.{service,timer}`,
and the breaker (`substrate/breaker.py`). The mechanism ships **disabled**.
Enabling the timer is a separate, deliberate owner decision -- not part of
deploying this wave -- and is not performed by this runbook.

The pulse only ever starts single bounded `manual-cycle --autonomous` beats.
It never rotates tasks, never edits the agenda, and never clears the panic
latch. A parked task stops the loop (notify + timer disabled); minting a new
task identity remains a manual owner action (see
docs/MERISTEM-LAYER0-ROLLBACK.md, "Parked task policy").

## Install (mechanism only -- does not enable anything)

```
ln -sf /RSI/Meristem/substrate/systemd/meristem-pulse.service /etc/systemd/system/meristem-pulse.service
ln -sf /RSI/Meristem/substrate/systemd/meristem-pulse.timer   /etc/systemd/system/meristem-pulse.timer
systemctl daemon-reload
```

`ExecStart` in the service unit is a hardcoded absolute path
(`/RSI/Meristem/substrate/pulse-beat.sh`), matching this repo's
server-is-the-deployment stance (the same assumption `/RSI/meristem-env`
already makes). A relocated checkout must edit the unit file.

## Enabling the timer is the owner's P0-c unfreeze act

Installing the units above does **not** start unattended beats. Only

```
systemctl enable --now meristem-pulse.timer
```

does that, and it is a deliberate decision by the owner, not a deployment
step. Before running it, confirm: a real webhook is reachable
(`MERISTEM_WEBHOOK_URL` in `/RSI/meristem-env`), the panic latch path
(`MERISTEM_CONTROL`) resolves and is currently clear, and H1 preflight
evidence is where you expect it to be. This document does not itself
authorize enabling it.

## Exit codes

| rc | Meaning | Timer action | Notified |
|---|---|---|---|
| 0 | Normal beat (candidate processed, or none produced but no refusal) | left running | only if `ignition-status` changed (good news) |
| 1 | Normal beat, seed produced no candidate this cycle | left running | no |
| 2 | Refused: needs operator (task guarded, panic latch unlocatable, dangling rollback intent, bad invocation, ...) | `disable --now` | yes |
| 3 | Panic latch already engaged | `disable --now` | yes |
| 4 | Breaker tripped: `FUTILE_BEAT_THRESHOLD` consecutive futile autonomous beats; the beat itself engaged the latch | `disable --now` | yes |

0/1 are the only codes that leave the timer running. Every other code stops
the timer and sends exactly one webhook notification before exiting with
that code, by design (see `pulse-beat.sh`'s "CRITICAL" comment on that path):
the human must get the ping even if `systemctl` or `curl` themselves fail --
each step there is individually error-tolerant.

## What each stop notification means, and the operator response

- **rc=2, refused.** The soil declined to start this beat for a reason a
  human needs to read (stderr in the unit's journal:
  `journalctl -u meristem-pulse.service`). Common causes: the task is
  guarded (parked/fulfilled/promotion_gated -- see the Parked task policy
  below), a dangling `rollback_intent` needs `root_manual` recovery
  (docs/MERISTEM-LAYER0-ROLLBACK.md), or the runtime manifest/projection
  failed to verify. Diagnose from the printed reason, fix the underlying
  condition, then re-enable per the recovery loop below.
- **rc=3, latched.** The panic latch is engaged -- by the breaker, by
  another path, or by a prior human `engage`. Read the latch content
  (`cat "$MERISTEM_CONTROL/PANIC"`) for the reason before clearing anything.
- **rc=4, breaker tripped.** `FUTILE_BEAT_THRESHOLD` (5) consecutive
  autonomous beats produced no candidate at all, whatever the reason
  (`propose_failed`, `path_violation`, `worker_error`, `task_guarded`, ...).
  This is an environment/loop-level judgment (`substrate/breaker.py`), not a
  verdict on any one task. The beat itself already engaged the panic latch
  before this notification was sent, so rc=3 will show on the *next*
  invocation too -- that's expected, not a second incident.

## Recovery loop

1. Diagnose from the notification + `journalctl -u meristem-pulse.service`
   + `python -m substrate.supervisor learning-status` / `ignition-status`.
2. Fix the underlying condition (unpark/mint a new task identity per the
   parked-task policy, resolve a dangling rollback intent, restore provider
   credentials, fix whatever made every beat futile).
3. Clear the latch -- human-only, never scripted:
   ```
   set -a && . /RSI/meristem-env && set +a && python3 root/panic.py clear
   ```
4. Re-enable the timer once you are satisfied the underlying condition is
   actually fixed, not just that the latch is clear:
   ```
   systemctl enable --now meristem-pulse.timer
   ```

Do not re-enable the timer as a reflex response to a notification. The
notification exists so a human makes this call; the mechanism's job stops at
"stop and tell someone."

## Parked task policy

A parked task (via `contract_failures` or `semantic_failures` reaching
threshold) is never retried implicitly, autonomous or not. The *first* beat
against a guarded task returns rc=2 (`task_guarded`) -- the pulse stops and
notifies right there, same as any other rc=2; it does not sit there
repeatedly hitting the guard. (A `task_guarded` row still counts as futile
if the breaker's streak is later evaluated across it -- e.g. an operator
re-enabling the timer without fixing the underlying condition -- but that is
a backstop, not the primary path for a parked task.) The fix is a new task
identity (docs/MERISTEM-LAYER0-ROLLBACK.md, "Parked task policy"), a manual
owner action -- the pulse does not and must not do this itself.

## Disable

```
systemctl disable --now meristem-pulse.timer
```

(This is exactly what `pulse-beat.sh` does automatically on any non-normal
rc; use this manually for planned maintenance.) To remove the mechanism
entirely: also `rm /etc/systemd/system/meristem-pulse.{service,timer}` and
`systemctl daemon-reload`.
