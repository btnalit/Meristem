# Agenda

*Layer 1. The loop takes the first open item each cycle and never writes to
this file; completion is read from the journal, so a rejected task stays open
for retry and a parked one waits for a human to clear its mailbox entry.*

Format is load-bearing: `- [ ] task` is open, `- [x] task` is done.

**Keep this file short.** It is sent to the engine in full on every mutation,
so every stale line is paid for again on every cycle. Forty accumulated tasks
at ~500 characters each was 20KB of prompt, nearly all of it describing work
already finished -- accumulation rather than metabolism, in the one file that
charges by the token. Completed work lives in the journal; retired text in
`state/agenda-archive.md`.

**Never on this list:** `root/` and `substrate/`. The soil holds panic,
succession, promotion and publishing; the code under review does not decide
what gets in or what leaves the machine. The gate refuses those paths, so the
boundary is enforced rather than merely stated.

## Open

- [ ] COMPRESSION: Extract the status-view functions from loop.py into meristem/views.py. Move cmd_status, cmd_spend, cmd_utility, cmd_body, cmd_report, and cmd_gaps -- every function that prints a read-only view and never mutates state. loop.py imports and dispatches to them. Touch only meristem/loop.py and meristem/views.py. Weaken no gate.
