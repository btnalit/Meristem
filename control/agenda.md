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

- [ ] Move the journal-reading helpers out of meristem/loop.py into a new module meristem/journal.py: next_cycle(), done_tasks(), parked_tasks(), and any other function whose entire job is to read state/journal.jsonl and answer a question about it. loop.py imports them from there and keeps no copy. This is compression: it does not lower core pressure by itself, it isolates what leaves next. Change only meristem/loop.py and meristem/journal.py. Weaken no check.
- [ ] Move the report and utility command bodies out of meristem/loop.py into a new module meristem/views.py, leaving only the argparse dispatch in loop.py. Views are read-only presentations of state, not loop machinery; they are the clearest thing in the kernel that is capability rather than mechanism. Change only meristem/loop.py and meristem/views.py. Weaken no check.
