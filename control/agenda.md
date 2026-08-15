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

- [ ] Collect organ utility. germline.invoke already journals an "organ_call" record on every call and nothing reads them. Add a `utility` command to meristem/loop.py printing, per organ: total calls, successful calls, and the cycle last used. An organ nobody calls is a pruning candidate; until this exists no pruning decision rests on evidence. Add a unit test.
- [ ] Advance body/organs/word-count/organ.json from "active" through "deprecating" to "archive" IF the utility figures show text-stats subsumes it -- text-stats already returns a word count among its outputs. If they do not, append the finding to state/backlog.md with the reason instead. Use appends for the register. This is prune's first firing and it must rest on measured utility, not on tidiness.
- [ ] Make meristem/breaker.py delegate counting to the journal-query organ when that organ is active, falling back to its own inline counting when it is absent or inactive. Use meristem.germline.invoke. The fallback is not optional: the kernel keeps working when the body does not. Add a unit test for both paths.
- [ ] Add a `report` command to meristem/loop.py writing REPORT.md at the repo root from state/journal.jsonl, state/scoreboard.jsonl, state/proposals.md and state/mailbox.md: cycles since the last report with outcomes, both pressures with a direction arrow, AGR as accepted-self-proposed over accepted-total, open proposal count, parked tasks, mailbox items. Print the path. REPORT.md is generated -- add it to .gitignore beside state/*.jsonl. Add a unit test.
- [ ] Add ONE function to body/organs/memory-graph/main.py named pipeline_check(workdir) that runs extract, then edges, then decay in sequence and returns problem strings: "no edges from N nodes" when edges is empty while extract returned more than ten nodes, and "all pattern nodes undated" when every pattern node has last_seen_cycle 0. Call it from the existing selfcheck op and merge its problems in. ADD ONLY -- keep every existing error path, structured error return and non-zero exit code exactly as it is. Under 25 lines.
