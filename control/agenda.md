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

- [ ] EXTERNALIZE: Move generate_report() formatting logic from meristem/loop.py into body/organs/reporter/main.py. Create body/organs/reporter/organ.json (id "reporter", lifecycle "candidate", entrypoint ["python3","main.py"], capability "formats REPORT.md from pre-computed data", probes [], dependencies []) and body/organs/reporter/main.py. The organ reads a JSON object from stdin with keys: workdir, core_pressure, closure_pressure, core_cap, closure_cap, kernel_loc, outcomes, recent_count, last_report_cycle, agr, open_proposals, parked, mailbox_items, probe_scores, organs. It formats REPORT.md at workdir root and prints {"ok":true} to stdout. It must NOT import from meristem. In loop.py keep generate_report() but gut it: compute the data dict (~20 lines), run the organ directly via subprocess (stdin JSON, read stdout), write the journal report record, print the path. Do NOT use germline.invoke -- the organ is lifecycle "candidate" and invoke requires "active". Delete all formatting logic from loop.py. Acceptance: kernel LOC drops by >=100 lines, test_report_journals_report_record must still pass. Touch only meristem/loop.py and body/organs/reporter/.
