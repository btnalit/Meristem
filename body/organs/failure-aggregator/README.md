# failure-aggregator

Detects repeated rejection patterns from the journal and writes structured
entries to `state/patterns.md`.  This is the memory of Principle 2
(Meta-over-Patch): before closing any failure, check the pattern register.

Purely additive to the kernel's circuit breaker (`meristem/breaker.py`):
the breaker decides whether to park a task; this organ records failure
*classes* so the loop can ask "what structural change would make this entire
class of failure impossible?"

## Lifecycle

`candidate` — grown but not yet calibrated.  The kernel breaker remains
the authoritative gate; this organ's signals are surfaced but not yet
acted upon for blocking or escalation.  Promotion to `calibrate` requires
a probe that verifies classification accuracy.

## ABI

- **Input:** `{"op": "aggregate", "journal_path": "...", "repo_path": "...", "cycle": N}`
- **Output:** `{"ok": true, "signals": [...], "patterns_written": [...]}`
- **Signals:** `surface` (2+ rejections), `escalate` (3+), `block` (4+)
