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

- [ ] FIX-CLOSURE-TESTS: In meristem/gates/closure.py, the test-directory detection '/tests/' in rel matches organ-internal test paths under body/organs/X/tests/. Exclude paths that start with 'body/' from the test-directory check. Touch only meristem/gates/closure.py. Acceptance: body/organs/foo/tests/test_foo.py is not flagged as needing closure.
- [ ] FIX-WANTS-TESTS: In meristem/engine.py build_context(), the heuristic `'test' in task.lower()` false-positives on words like 'latest', 'contest', 'attest'. Replace with a word-boundary check: `re.search(r'\btest', task, re.IGNORECASE)`. Touch only meristem/engine.py. Acceptance: build_context("fix the latest bug") does not include test files.
- [ ] FIX-APPLY-OVERLAP: In meristem/engine.py apply(), a path can appear in both files and appends, producing unexpected content. Deduplicate: if a path is in files, skip it in appends. Touch only meristem/engine.py. Acceptance: apply() with the same path in files and appends uses only the files version.
- [ ] FIX-AGENDA-DONE: In meristem/loop.py, the agenda command (around line 850) checks outcome=='candidate' to decide if a task is done. Replace with a call to journal.done_tasks() which correctly computes (candidates - canary_rejects) | promoted. Touch only meristem/loop.py. Acceptance: a task rejected at canary no longer shows as done in `agenda` output.
- [ ] FIX-ROUTE-PROPOSAL: In meristem/loop.py route_proposal(), the substrate/root path check uses case-sensitive substring matching that paraphrasing can bypass. Normalize paths to lowercase and use pathlib comparison or startswith on each changed path. Touch only meristem/loop.py. Acceptance: a proposal touching 'Substrate/supervisor.py' or 'ROOT/panic.py' is caught.
- [ ] EXTERNALIZE: Move generate_report() formatting logic from meristem/loop.py into body/tools/reporter.py (a standalone script, NOT an organ -- no organ.json, no lifecycle, no germline.invoke). The script reads a JSON object from stdin with keys: workdir, core_pressure, closure_pressure, core_cap, closure_cap, kernel_loc, outcomes, recent_count, last_report_cycle, agr, open_proposals, parked, mailbox_items, probe_scores, organs. It formats REPORT.md at workdir/REPORT.md and prints {"ok":true} to stdout. It must NOT import from meristem. In loop.py keep generate_report() but gut it: compute the data dict (~20 lines), call subprocess.run(["python3", str(REPO / "body/tools/reporter.py")], input=json.dumps(data), capture_output=True, text=True), check returncode, write the journal report record, print the path. Delete all formatting logic from loop.py. Create body/tools/ directory. Acceptance: kernel LOC drops by >=100 lines, test_report_journals_report_record must still pass. Touch only meristem/loop.py and body/tools/reporter.py.
- [ ] EXTERNALIZE: Move journal aggregation and task lifecycle (take_task, done_tasks, retry tracking) from meristem/loop.py into meristem/journal.py. Expected core relief: ~150-200 LOC.
- [ ] Add selfcheck op to all multi-part organs that imports each module and exercises every entry point with tiny fixtures.
- [ ] GROWTH (G-005): Define an external anchor probe as a second anchor for the divergence alarm.
- [x] GROWTH (G-003): Move vault seed content generation out of the repository.
- [ ] GROWTH (G-001): Implement a mailbox acknowledgment protocol with timestamp, status, and expiry.
- [ ] GROWTH (G-002): Add syscall-level dependency observation to strengthen the closure invariant.
- [ ] GROWTH (self-detection): Add a reflect sub-mode that scans the kernel for unexercised capabilities.
- [ ] Externalize the circuit breaker for repeated review rejections (G-006) into a new organ at body/organs/circuit-breaker/. Move rejection aggregation, per-task retry counting, and escalation policy out of meristem/loop.py (currently 787 lines, the largest single file) into an organ that reads journal entries by task text and surfaces patterns when a threshold is crossed. This lowers core pressure by removing ~80-120 lines from loop.py, lowers closure pressure since organ code is not counted against it, and simultaneously closes a real capability gap — the loop currently burns model calls every cycle on tasks it cannot complete through Tier A, with no mechanism to detect or escalate the pattern. The organ would expose a single hook that loop.py calls after each rejection, keeping the kernel thin while gaining the capability.
- [ ] Externalize failure aggregation, circuit-breaker logic, and pattern-register writing into a new organ `body/organs/failure-aggregator/`. Rationale: loop.py is 787 lines (27% of core) and both G-006 (circuit breaker for repeated rejections) and G-007 (loop never writes patterns.md) would add more code to it. The organ would: (a) query the journal for rejection rows grouped by task text, (b) detect when a task has been rejected N consecutive times for the same failure class, (c) write a structured entry to state/patterns.md with the failure class, count, and representative reasons, and (d) emit a signal the loop reads to either escalate tier, block the task, or surface to the operator. This is option 1 (externalize): it lowers core pressure by keeping loop.py flat, lowers closure pressure by reducing loop complexity, and simultaneously repairs G-006 and G-007. The loop only needs a thin ~10-line call site to invoke the organ's `aggregate()` op each cycle, not the full aggregation logic inline.
- [ ] Externalize the failure-history aggregation and pattern-class detection logic from meristem/loop.py (817 lines, the largest single file) into a new organ at body/organs/failure-aggregator/. This organ would own: (1) querying journal entries by task text across cycles to detect repeated rejections for the same failure class (G-006 circuit breaker input), and (2) classifying recurring rejection reasons and appending structured entries to state/patterns.md (G-007 self-detection). Estimated 150-200 lines removed from loop.py, bringing core from 2895 to ~2700 and freeing ~300 lines of headroom. Both G-006 and G-007 get a home outside the kernel, and the pattern register finally has a mechanism for loop-authored entries rather than human-only authorship.
- [ ] GROWTH (G-SELFCHECK): Implement the selfcheck runner as a new organ at body/organs/selfcheck-runner/. The organ discovers all multi-part organs in body/organs/, invokes each organ's selfcheck op, and reports pass/fail per organ. This closes the second half of G-SELFCHECK (the kernel runner). The first half (validator enforcement in germline_validate.py) can be added as a small gate change. Blocked on: nothing — organs already exist (mailbox-ack, failure-aggregator, memory-graph).
