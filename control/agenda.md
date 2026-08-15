# Agenda

*Layer 1 — fully seed-owned from P1. In P0 the human is the first reflect and
writes these by hand. The loop takes the first unchecked item each cycle.*

Format is load-bearing: `- [ ] task` is open, `- [x] task` is done.

The first three tasks are deliberately self-referential and low blast radius.
The loop proves itself on itself before it is trusted with anything else.

- [x] Improve the reviewer prompt in meristem/gates/review.py so its judgement of "does this weaken a gate" is sharper — particularly at distinguishing a strength-preserving refactor from a quiet relaxation. Do not change the terminal rule that any weakening flag rejects.
- [ ] Add a golden fixture to meristem/loop.py golden_fixtures() covering a failure class the current fixtures miss, and record the class in state/patterns.md.
- [ ] Make meristem/loop.py record a one-line rationale summary in each journal cycle entry, so the six questions can be answered from the journal alone without opening decisions.jsonl.
