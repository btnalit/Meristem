# selfcheck-runner

Discovers all multi-part organs in `body/organs/`, invokes each one's
`selfcheck` op, and reports pass/fail per organ.

This is the runner half of G-SELFCHECK. The validator half — enforcing
that multi-part organs declare and support selfcheck — lives in the
germline validator (`meristem/gates/germline_validate.py`) at the immune
tier and is deferred until core pressure drops below the cap.

## ABI

- **Input**: `{"op": "run"}` (default) or `{"op": "selfcheck"}`
- **Output**: `{"ok": true/false, "results": [...], "failures": [...]}`

When `op` is `run`, the organ discovers all multi-part organs (those with
more than one `.py` source file), invokes each one's `selfcheck` op via
its declared entrypoint, and aggregates the results.

## Retirement path

If selfcheck enforcement moves into the germline validator (making the
runner redundant), this organ can be pruned. Its utility measure is
`usage` (how often it is invoked) and `success_rate` (fraction of
discovered organs that pass).
