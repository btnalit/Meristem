# Proposals

Tasks the seed proposed for itself. A human promotes a line into
control/agenda.md; the loop never writes to the agenda directly.

- [ ] EXTERNALIZE: Move the proposal engine from meristem/engine.py (250 LOC) into body/organs/proposal-engine/. The engine handles build_context() (constructing the review context from kernel+control+state files) and apply() (applying diffs to the filesystem). These are self-contained capabilities that follow the same pattern as already externalized organs (feasibility-check, selfcheck-runner, failure-aggregator). Proposed: new organ at body/organs/proposal-engine/ with op_build_context and op_apply, called from loop.py in place of the current inline engine. Expected: core pressure drops from 2999/3000 to 2749/3000 (251 lines of headroom), closure pressure drops by approximately 3000-4000 tokens as engine.py's content moves out of the kernel tree.
