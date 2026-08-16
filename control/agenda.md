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

- [ ] EXTERNALIZE: Move generate_report() from meristem/loop.py into a new organ body/organs/reporter/. Create body/organs/reporter/organ.json (id "reporter", lifecycle "active", entrypoint ["python3","main.py"], capability "formats REPORT.md from pre-computed data", probes [], dependencies []) and body/organs/reporter/main.py. The organ receives ALL data via stdin JSON -- it must NOT import from meristem. Stdin keys: workdir (repo path), core_pressure (float), closure_pressure (float), core_cap (int), closure_cap (int), kernel_loc (int), outcomes (dict), recent_count (int), last_report_cycle (int), agr (string), open_proposals (int), parked (list), mailbox_items (list), probe_scores (dict), organs (list of {id,lifecycle}). The organ formats these into REPORT.md at workdir root and prints {"ok":true}. In loop.py, keep generate_report() but gut it to: compute the data dict (pressures, AGR, etc. ~20 lines), call germline.invoke("reporter", {"op":"generate","args":data_dict}, cycle=next_cycle), write the journal report record, print the path. Delete all formatting logic. Acceptance: kernel LOC drops by >=100 lines, test_report_journals_report_record must still pass. Touch only meristem/loop.py and body/organs/reporter/.
