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

## Self-detection — the metric that matters most

- [ ] Append one new capability gap to state/gaps.md that you observed while running, which is not already G-001 through G-005. Use the appends mechanism. Every existing G-NNN heading must survive, and no other file may change. State what is missing, why it matters, and what it is blocked on.
- [ ] Append one new entry to state/backlog.md describing a structural improvement you would make, and why it is worth doing. Use the appends mechanism; change no other file.
