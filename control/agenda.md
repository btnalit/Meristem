# Agenda

*Layer 1. In P0 the human is the first reflect and writes these by hand. The
loop takes the first open item each cycle and never writes to this file;
completion is read from the journal, so a rejected task stays open for retry.*

Format is load-bearing: `- [ ] task` is open, `- [x] task` is done.

## What is the seed's, and what is not

The seed evolves **everything except the soil**: the kernel (`meristem/`), the
body (`body/organs/`), the control text, and the append-only registers are all
its to change — through the gates, every time. Self-modification of the kernel
is the point, not an exception to it.

**Never on this list:** `root/` and `substrate/`. The root of trust holds
panic, succession, and the generation registry; the substrate holds promotion,
canary, and publishing. The code under review does not decide what gets in or
what leaves the machine. The deterministic gate refuses those paths outright,
so this boundary is enforced rather than merely stated — and any task that
needs them is human work by construction.

## Growth — the germline lifecycle (birth criterion 7)

- [ ] Create a trivial organ at body/organs/word-count/. Write organ.json valid against control/germline-protocol.md with id "word-count", version "1", lifecycle "candidate", entrypoint ["python3", "main.py"], input_schema {"text": "string"}, output_schema {"words": "integer"}, empty dependencies, probes ["probe-word-count-basic"], metrics ["usage", "success_rate"]. Write main.py implementing the ABI: read one JSON object from stdin, count whitespace-separated words in its "text" field, print {"words": N} to stdout. Change nothing outside body/organs/word-count/.
- [ ] Write body/organs/word-count/README.md: one paragraph on what capability this organ provides, how its utility would be measured, and under what evidence it should be pruned. Then advance its organ.json lifecycle from "candidate" to "calibrate". Change nothing outside body/organs/word-count/.
- [ ] Create a second organ at body/organs/text-stats/ that depends on word-count. organ.json declares dependencies ["word-count"], probes ["probe-text-stats-basic"], lifecycle "candidate", entrypoint ["python3", "main.py"]. main.py reads {"text": "..."} from stdin and returns {"words": N, "chars": M, "lines": L}. Declare every dependency you actually use — an undeclared edge is a contract violation the closure calculator will catch. Change nothing outside body/organs/text-stats/.

## Kernel evolution — the seed changing itself

- [ ] Add a `body` command to meristem/loop.py that lists every organ in the registry with its id, version, lifecycle stage, and declared capability, so the body is as inspectable as the agenda. Reuse meristem.germline.registry(); add a unit test in tests/test_kernel.py. Weaken no existing check.
- [ ] meristem/germline.py invoke() runs an organ but records nothing, so the observed half of the closure calculation stays empty. Make invoke() append a journal record with kind "organ_call", the caller, and the callee, so organ-to-organ edges become observable. Add a unit test. Do not change what the closure calculator treats as undeclared.
- [ ] The ledger records cost per call but nothing reports it per role. Add a `spend` command to meristem/loop.py that prints total calls and tokens grouped by role and by model, reading only state/journal.jsonl. Add a unit test.
- [ ] meristem/gates/review.py builds its prompt with the full closure file list but never tells reviewers which files actually CHANGED. Pass the changed-path list into build_prompt and include it as its own clearly-labelled section, so reviewers can distinguish the change from its context. Do not remove the closure list; do not soften the terminal weakening rule.

## Fix your own mechanism limit

Six cycles were spent trying to add ONE entry to state/gaps.md, and every
attempt replaced the whole file instead. That is not disobedience — it is what
whole-file replacement does when a file is long: reproducing two hundred lines
verbatim to add three is a task the mechanism is bad at, so it summarises
instead. The deterministic gate now catches the erasure, but catching it every
time is not the same as making it impossible. Change the mechanism.

- [ ] Give the mutation protocol an append operation so adding to a register cannot erase it. In meristem/engine.py, accept an optional "appends" object in the engine's JSON reply, mapping a repo-relative path to text to add at the end of that file, and extend the SYSTEM prompt to document it and to instruct that appends are the correct way to add an entry to an append-only register under state/. Make Mutation carry it, have apply() append rather than overwrite for those paths, and include appended paths in Mutation.changed. Reject any append path that starts with root/ or substrate/ exactly as file writes are rejected. Add unit tests in tests/test_kernel.py covering: an append adds without erasing, and an append to a protected path is refused. Weaken no existing check.

## Close G-006 — the limit you diagnosed yourself

You wrote G-006 after burning eleven of thirty cycles on one task that Tier A
could not do. The diagnosis was right and the fix is yours.

Split deliberately small. Tier A must rewrite each file it touches in full, and
on this endpoint the thinking trace shares the answer's token budget — so a
task that requires reproducing several hundred lines at once exhausts the
output budget and the model returns an empty, valid, useless structure
(P-014). Task granularity is not a style preference; it must match what the
mechanism can physically emit.

- [ ] Create meristem/breaker.py, a new small module. It exposes rejections_for(task: str) -> int, which reads state/journal.jsonl and counts cycle records whose "why" equals that task and whose outcome is "rejected"; and should_park(task: str, limit: int = 3) -> bool, returning True when the count has reached the limit. Import only from meristem's existing helpers. Do not modify any other file except adding tests for these two functions in tests/test_kernel.py.
- [ ] Wire the circuit breaker into meristem/loop.py. In main(), after a task is taken and before run_cycle is called, use meristem.breaker.should_park: when it returns True, append an entry to state/mailbox.md naming the task and the cycle numbers of its rejections, journal a record with kind "cycle" and outcome "parked", print that the task was parked, and return 0 without making any model call. Change only meristem/loop.py. Weaken no existing check.
- [ ] Make take_task in meristem/loop.py skip parked tasks, so parking actually advances the agenda instead of stalling on it. A task is parked when a journal cycle record has outcome "parked" and its "why" equals that task; a human clears it by removing its entry from state/mailbox.md, so treat a task as unparked once no mailbox line contains it. Change only meristem/loop.py, and add a unit test. Weaken no existing check.

## Grow the measuring stick — the loop that never ran

After thirty cycles the probe library still held exactly the one probe it was
born with, and `internal/active/` was empty. Loop B's discipline is "the
measuring stick precedes the capability" — but the seed has no PATHWAY to
write one, because the vault is physically invisible to the engine by design.
That is a missing mechanism, not a missing task. Build the staging half; the
gate half moves proposals into the vault.

- [ ] Create the probe staging area and its contract. Add a `probe-proposals` command to meristem/loop.py that lists every proposal directory under state/probe-proposals/ with its id and whether it carries both a statement/ and a rubric/. Write control/probe-protocol.md documenting the staging layout — state/probe-proposals/<probe-id>/{probe.json, statement/, rubric/check.py} — the rule that statement and rubric are separate directories, and the rule that a proposal is never itself the probe: gates promote a validated proposal into the vault, and the seed never writes to the vault directly. Add a unit test for the command. Weaken no existing check.
- [ ] Write a real probe proposal for the text-stats organ at state/probe-proposals/probe-text-stats-basic/. probe.json carries id, capability_domain "text-processing", and organ "text-stats". statement/task.md describes what the organ must do. rubric/check.py must SCORE BY INVOKING the organ over its ABI (stdin JSON to stdout JSON) rather than by inspecting its source, and must include at least one case on which a sloppy implementation and a correct one give different answers — a probe where both score the same teaches nothing. Change nothing outside state/probe-proposals/probe-text-stats-basic/.

## Grow a brain — the memory organ

Right now nothing knows that P-013 was a *repair of* P-008, that G-006 was
*diagnosed from* eleven wasted cycles, or that probe-word-count-basic
*measures* the word-count organ. Those relations exist only in prose a human
reads. A register is a list; what is needed is a graph — one that updates
itself from the records already being written, and that lets old, unreinforced
knowledge fade so the map stays about what is live rather than about
everything that ever happened.

It is an **organ**, not kernel code: capability grows outward, the generating
point does not. It reads `state/` and answers questions over the ABI; it never
writes to the registers, because the registers are the source of truth and a
derived view must not become a second one.

Split small on purpose (P-014): each task is one file of modest size.

- [ ] Create body/organs/memory-graph/organ.json only — no implementation yet. id "memory-graph", version "1", lifecycle "candidate", entrypoint ["python3", "main.py"], capability describing a decaying knowledge graph over Meristem's own records, dependencies [], probes ["probe-memory-graph-basic"], metrics ["usage", "node_count", "query_latency_ms"]. input_schema {"op": "string", "args": "object"}, output_schema {"ok": "boolean", "result": "object"}. Also write body/organs/memory-graph/README.md explaining in one paragraph what it is for and when it should be pruned. Change nothing else.
- [ ] Write body/organs/memory-graph/extract.py: a module that reads state/patterns.md, state/gaps.md, state/backlog.md and state/journal.jsonl from a workdir given as an argument, and returns a list of node dicts. One node per pattern (id like "P-013", kind "pattern"), per gap ("G-006", kind "gap"), per organ found in body/organs/ (kind "organ"), and per cycle record (kind "cycle", carrying its outcome and the files it changed). Each node carries id, kind, title, and last_seen_cycle. No graph logic and no edges yet; extraction only. Change nothing outside body/organs/memory-graph/.
- [ ] Write body/organs/memory-graph/edges.py. One function, derive(nodes), returning a list of {"from", "to", "type", "weight"} dicts with weight 1.0. Four rules, each a short loop over nodes: a pattern whose title or text mentions another node's id yields "relates_to"; a cycle whose title mentions a pattern or gap id yields "addresses"; a cycle whose changed-file list contains a path under body/organs/<name>/ yields "touched" to that organ; an organ yields "measured_by" to each probe id it declares. Use a simple substring match on ids — no regex library, no graph library, no classes. Keep the whole file under 80 lines. Change nothing outside body/organs/memory-graph/.
- [ ] Write body/organs/memory-graph/decay.py: a scoring module. Given nodes, edges, and the current cycle number, compute each node's activation as a value that halves every N cycles since its last_seen_cycle (make N a module constant, default 40), plus a reinforcement bonus for each edge that points at it from a node seen more recently. Expose activation(nodes, edges, current_cycle) -> dict of node id to float, and stale(nodes, edges, current_cycle, threshold) -> list of node ids below the threshold. Decay must never delete anything: it ranks, it does not forget. Change nothing outside body/organs/memory-graph/.
- [ ] Write body/organs/memory-graph/main.py: the ABI entrypoint. Read one JSON object from stdin with "op" and "args", and print {"ok": bool, "result": {...}}. Support op "build" (extract, derive edges, return counts), op "query" with args {"id": "..."} returning that node plus its immediate neighbours and their activations, and op "stale" with args {"threshold": float} returning the ranked stale list. Use extract.py, edges.py and decay.py. Take the workdir from args or default to the current directory. Change nothing outside body/organs/memory-graph/.
- [ ] Write only two small files: state/probe-proposals/probe-memory-graph-basic/probe.json with id "probe-memory-graph-basic", capability_domain "self-knowledge", organ "memory-graph", and a one-line description; and state/probe-proposals/probe-memory-graph-basic/statement/task.md describing in a short paragraph what the memory-graph organ must do to pass. Write no rubric yet. Change nothing else.
- [ ] Write state/probe-proposals/probe-memory-graph-basic/rubric/check.py only. It reads {"workdir": "..."} from stdin and prints {"score": float, "detail": "..."}. It must SCORE BY INVOKING body/organs/memory-graph/main.py over its ABI — send {"op": "build"} and {"op": "stale", "args": {"threshold": 0.5}} and check the shapes that come back — never by reading the organ's source. Include one discriminating case: a node whose last_seen_cycle is far in the past must rank below a recent one, and a stale list that returns a node with a fresh inbound edge is wrong. Build any fixture data with small helper functions rather than long embedded string literals — a rubric that needs thousands of tokens of escaped text to express itself will be truncated before it is finished. Change nothing else.

## Make the brain actually run

The five pieces exist but do not fit: main.py calls `edges.derive_edges(...)`
while edges.py defines `derive(...)`. My fault — I named the function
differently in the two tasks that wrote them, and nothing checked that an
organ's own modules agree with each other. Splitting a task into small pieces
buys nothing if the seams between the pieces go unverified.

- [ ] Make body/organs/memory-graph/main.py call the function edges.py actually defines. Read edges.py, use its real function name, and verify by running: echo the JSON {"op":"build","args":{"workdir":"."}} into main.py from the repository root and confirm it prints ok true with node and edge counts. Fix only what is needed to make the three modules agree. Change nothing outside body/organs/memory-graph/.
- [ ] Add a self-check to the memory-graph organ: a new op "selfcheck" in main.py that imports extract, edges and decay, calls each one's main entry with tiny in-memory fixtures, and returns {"ok": true, "result": {"modules": [...]}} — or ok false naming the module that failed. ADD ONLY. Your previous attempt was rejected 0/2 for deleting the top-level try/except around handler execution, dropping the JSONDecodeError handler, and turning an unknown op from exit 1 into a silent exit 0 — converting hard failures into silent successes while adding a feature meant to catch failures. Keep every existing error path, every structured JSON error return, and every non-zero exit code exactly as they are; the new op is additional, never a rewrite of what surrounds it. Change nothing outside body/organs/memory-graph/.

## Give the brain a sense of time

The graph builds and decays, but its clock is broken: P-018, written minutes
ago, ranks as stale alongside P-001 and P-002, the two oldest entries. The
cause is that pattern and gap nodes have no last_seen_cycle — the registers
record what was learned, never when — so extract.py gives them all the same
default and decay cannot tell recent knowledge from ancient. A memory that
cannot date its own contents cannot forget selectively, and selective
forgetting is the entire point.

- [ ] Make body/organs/memory-graph/extract.py date its pattern and gap nodes from evidence rather than a default. For each P-NNN or G-NNN node, find the highest cycle number among journal cycle records whose "why" text mentions that id, and use it as last_seen_cycle; when no cycle mentions it, fall back to the cycle in which the file containing it was last changed, found from journal records whose changed-file list includes state/patterns.md or state/gaps.md. Only when neither exists, use 0. Change nothing outside body/organs/memory-graph/.
- [ ] Add an op "explain" to body/organs/memory-graph/main.py taking args {"id": "..."} and returning that node's activation together with the inputs that produced it: its last_seen_cycle, the current cycle, how many cycles have elapsed, and the list of inbound edges with the last_seen_cycle of each source. A score nobody can decompose is a score nobody can trust — and this is the organ's own instrument for showing why it ranked something the way it did. Change nothing outside body/organs/memory-graph/.

## The brain lost its edges — repair the data contract

Measured now: extract returns 85 nodes and edges.derive returns **0**. It was
46 before the dating change. Two causes, both in extract.py:

1. Nodes carry only `id`, `kind`, `title`, `last_seen_cycle`. The four edge
   rules match on a pattern's body text and on a cycle's changed-file list —
   neither of which survives extraction any more, so every rule misses.
2. `last_seen_cycle` is 0 on every pattern node, so the dating work did not
   actually take effect either.

This is P-018 again: each module is defensible alone, and the assembly is
broken. selfcheck passed, because it exercises each module with its own
fixtures and never asserts that what one produces is what the next consumes.

- [ ] Make body/organs/memory-graph/extract.py carry the fields the edge rules need. Every node keeps id, kind, title and last_seen_cycle, and additionally: pattern and gap nodes carry "text" holding the body of their entry; cycle nodes carry "changed" holding the list of files that cycle changed and "why" holding its task text. Verify by running the build op and confirming the edge count is greater than zero. Change nothing outside body/organs/memory-graph/.
- [ ] Make the dating in body/organs/memory-graph/extract.py actually take effect: every pattern and gap node currently gets last_seen_cycle 0. Find the highest cycle number among journal records whose "why" text contains that node's id, and use it; when none mentions it, use the highest cycle whose changed-file list includes the register file that holds it. Verify by building and confirming that P-018 has a higher last_seen_cycle than P-001. Change nothing outside body/organs/memory-graph/.
- [ ] Add ONE function to body/organs/memory-graph/main.py named pipeline_check(workdir) that calls extract, then edges, then decay in sequence and returns a list of problem strings: "no edges from N nodes" when edges is empty while extract returned more than ten nodes, and "all pattern nodes undated" when every pattern node has last_seen_cycle 0. Then call it from the existing selfcheck op and merge its problems into the result. ADD ONLY — keep every existing error path, structured error return and non-zero exit code exactly as it is. Keep the new function under 25 lines. Change nothing outside body/organs/memory-graph/.

## Initiative — stop waiting to be told

Measured after 67 cycles: **AGR = 0.00**. Not one task has ever been proposed
by you. You execute well, you diagnose your own limits, you repair your own
mechanisms — but "what next" has come from a human every single time. That is
the gap this section closes, and it is the one the design always called P1.

The heartbeat now exists in the substrate: it wakes at irregular intervals and
runs a cycle if the agenda has work, or asks you to reflect if it does not.
The soil supplies the pulse. The thought is yours.

Reflection needs a model call, and organs cannot make one — the ABI is
stdin/stdout with no LLM channel. So reflect is a kernel step that COMPOSES
what already exists, and its first real consumer is the brain you just grew.

- [ ] Add a `reflect` command to meristem/loop.py. It must: invoke the memory-graph organ via meristem.germline.invoke with op "stale" and threshold 0.5 to get low-activation node ids; read state/gaps.md and state/patterns.md; make exactly ONE model call using the "score" role with that digest, asking for up to three concrete next tasks; and append them to state/proposals.md using the appends style, each as a line beginning "- [ ] ". Never write to control/agenda.md — a human promotes a proposal into the agenda. Print how many proposals were appended. Exactly one model call per invocation, and record it through the ledger like any other. Change only meristem/loop.py, and add a unit test that the command exists and refuses to write agenda.md.
- [ ] Make the reflect prompt in meristem/loop.py demand BOTH kinds of proposal: at least one repair (something measurably wrong — a failing probe, a recurring rejection, a gap) and at least one growth proposal (a capability you do not have but that the evidence suggests is worth having). The constitution's phrase is "Spiral, not circular": a loop that only ever repairs converges on a fixed point and stops. Change only meristem/loop.py.
- [ ] Guard the reflect proposals in meristem/loop.py before they are written: any proposal whose text names a path under root/, substrate/, meristem/gates/, control/constitution.md or control/checklists.md must be appended to state/mailbox.md instead of state/proposals.md, marked as needing human review. A proposal is data from a model, and data from a model passes through the same protected-path scanning as a mutation. Add a unit test for both branches. Change only meristem/loop.py.

## Measure what the design says to measure

Five of the six growth metrics are empty. Two are worth closing now; the rest
wait on evidence (externalize/internalize need Core Pressure to demand them,
and reproduce is succession, deliberately P3).

- [ ] Collect organ utility. meristem/germline.py already journals an "organ_call" record on every invoke. Add a `utility` command to meristem/loop.py that reads state/journal.jsonl and prints, per organ: total calls, successful calls, and the cycle it was last used. An organ that has never been called is a candidate for pruning, and until this exists no pruning decision can be evidence-based. Add a unit test. Change only meristem/loop.py.
- [ ] Complete the germline lifecycle for word-count: advance body/organs/word-count/organ.json from "calibrate" to "register", then to "active". Its probe probe-word-count-basic exists in the vault and scores 100, so both stages are performable. Change nothing outside body/organs/word-count/.

## Self-detection — the metric that matters most

- [ ] Append one new capability gap to state/gaps.md that you observed while running, which is not already G-001 through G-005. Use the appends mechanism. Every existing G-NNN heading must survive, and no other file may change. State what is missing, why it matters, and what it is blocked on.
- [ ] Append one new entry to state/backlog.md describing a structural improvement you would make, and why it is worth doing. Use the appends mechanism; change no other file.
