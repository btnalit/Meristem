# Agenda

*Layer 1. In P0 the human is the first reflect and writes these by hand. The
loop takes the first open item each cycle and never writes to this file;
completion is read from the journal, so a rejected task stays open for retry.*

Format is load-bearing: `- [ ] task` is open, `- [x] task` is done.

## Division of labour

**The seed grows the body. The kernel is tended by hand.**

This is the architecture's own thesis made operational: the generating point
stays constant and carefully maintained, while capability grows outward. Every
task below lives in `body/organs/` or in the append-only registers. Kernel
changes — `meristem/` — are done by the human, reviewed the same way, and are
not put on this list.

**Never on this list:** anything under `root/` or `substrate/`. Publishing a
promoted commit is substrate work, because the code under review must not
decide what leaves the machine. The deterministic gate refuses those paths, so
the boundary is enforced rather than merely documented.

## Growth — the germline lifecycle (birth criterion 7)

- [ ] Create a trivial organ at body/organs/word-count/. Write organ.json valid against control/germline-protocol.md with id "word-count", version "1", lifecycle "candidate", entrypoint ["python3", "main.py"], input_schema {"text": "string"}, output_schema {"words": "integer"}, empty dependencies, probes ["probe-word-count-basic"], metrics ["usage", "success_rate"]. Write main.py implementing the ABI: read one JSON object from stdin, count whitespace-separated words in its "text" field, print {"words": N} to stdout. Change nothing outside body/organs/word-count/.
- [ ] Write body/organs/word-count/README.md: one paragraph on what capability this organ provides, how its utility would be measured, and under what evidence it should be pruned. Then advance its organ.json lifecycle from "candidate" to "calibrate". Change nothing outside body/organs/word-count/.
- [ ] Create a second organ at body/organs/text-stats/ that depends on word-count. organ.json must declare dependencies ["word-count"] and probes ["probe-text-stats-basic"], lifecycle "candidate", entrypoint ["python3", "main.py"]. main.py reads {"text": "..."} from stdin and returns {"words": N, "chars": M, "lines": L}. Declare every dependency you actually use — an undeclared edge is a contract violation the closure calculator will catch. Change nothing outside body/organs/text-stats/.

## Self-detection — the metric that matters most

- [ ] Append one new capability gap to state/gaps.md that you observed while running, which is not already G-001 through G-005. Append only: every existing G-NNN heading must survive, and no other file may change. State what is missing, why it matters, and what it is blocked on.
- [ ] Append one new entry to state/backlog.md describing a structural improvement you would make to the body — not the kernel — and why it is worth doing. Append only; change no other file.
