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

- [ ] Move the journal-reading helpers OUT of meristem/loop.py into a new kernel module meristem/journal.py: done_tasks(), parked_tasks(), next_cycle(), and any other function whose whole job is to read state/journal.jsonl and answer a question about it. loop.py imports them from there. This is compression, not externalization -- it does not lower core pressure by itself, but it makes the next step possible by isolating what will leave. Change only meristem/loop.py and meristem/journal.py. Weaken no check.
- [ ] Collect organ utility. germline.invoke already journals an "organ_call" record on every call and nothing reads them. Add a `utility` command to meristem/loop.py printing, per organ: total calls, successful calls, and the cycle last used. An organ nobody calls is a pruning candidate; until this exists no pruning decision rests on evidence. Add a unit test.
- [ ] Add a `report` command to meristem/loop.py writing REPORT.md at the repo root from state/journal.jsonl, state/scoreboard.jsonl, state/proposals.md and state/mailbox.md: cycles since the last report with outcomes, both pressures with a direction arrow, AGR as accepted-self-proposed over accepted-total, open proposal count, parked tasks, mailbox items. Print the path. REPORT.md is generated -- add it to .gitignore beside state/*.jsonl. Add a unit test.
