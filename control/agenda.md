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

- [ ] FIX-AGENDA-DONE: In meristem/loop.py, the agenda command (around line 850) checks outcome=='candidate' to decide if a task is done. Replace with a call to journal.done_tasks() which correctly computes (candidates - canary_rejects) | promoted. Touch only meristem/loop.py. Acceptance: a task rejected at canary no longer shows as done in `agenda` output.
- [ ] FIX-WANTS-TESTS: In meristem/engine.py build_context(), the heuristic `'test' in task.lower()` false-positives on words like 'latest', 'contest', 'attest'. Replace with a word-boundary check: `re.search(r'\btest', task, re.IGNORECASE)`. Touch only meristem/engine.py. Acceptance: build_context("fix the latest bug") does not include test files.
- [ ] FIX-ROUTE-PROPOSAL: In meristem/loop.py route_proposal(), the substrate/root path check uses case-sensitive substring matching that paraphrasing can bypass. Normalize paths to lowercase and use pathlib comparison or startswith on each changed path. Touch only meristem/loop.py. Acceptance: a proposal touching 'Substrate/supervisor.py' or 'ROOT/panic.py' is caught.
- [ ] FIX-CLOSURE-TESTS: In meristem/gates/closure.py, the test-directory detection '/tests/' in rel matches organ-internal test paths under body/organs/X/tests/. Exclude paths that start with 'body/' from the test-directory check. Touch only meristem/gates/closure.py. Acceptance: body/organs/foo/tests/test_foo.py is not flagged as needing closure.
- [ ] FIX-APPLY-OVERLAP: In meristem/engine.py apply(), a path can appear in both files and appends, producing unexpected content. Deduplicate: if a path is in files, skip it in appends. Touch only meristem/engine.py. Acceptance: apply() with the same path in files and appends uses only the files version.
- [ ] EXTERNALIZE: Move generate_report() formatting logic from meristem/loop.py into body/organs/reporter/main.py. Create body/organs/reporter/organ.json (id "reporter", lifecycle "candidate", entrypoint ["python3","main.py"], capability "formats REPORT.md from pre-computed data", probes [], dependencies []) and body/organs/reporter/main.py. The organ reads a JSON object from stdin with keys: workdir, core_pressure, closure_pressure, core_cap, closure_cap, kernel_loc, outcomes, recent_count, last_report_cycle, agr, open_proposals, parked, mailbox_items, probe_scores, organs. It formats REPORT.md at workdir root and prints {"ok":true} to stdout. It must NOT import from meristem. In loop.py keep generate_report() but gut it: compute the data dict (~20 lines), run the organ directly via subprocess (stdin JSON, read stdout), write the journal report record, print the path. Do NOT use germline.invoke -- the organ is lifecycle "candidate" and invoke requires "active". Delete all formatting logic from loop.py. Acceptance: kernel LOC drops by >=100 lines, test_report_journals_report_record must still pass. Touch only meristem/loop.py and body/organs/reporter/.
