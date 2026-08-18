# Proposals

Tasks the seed proposed for itself. A human promotes a line into
control/agenda.md; the loop never writes to the agenda directly.

- [ ] Change the cap. per-file: meristem/loop.py 904, meristem/journal.py 306, meristem/gates/deterministic.py 245, meristem/engine.py 244, meristem/gates/probes.py 198, meristem/gates/review.py 196, meristem/gates/closure.py 181, meristem/llm.py 187, meristem/germline.py 139, meristem/ledger.py 136, meristem/gates/germline_validate.py 66, meristem/__init__.py 84, meristem/breaker.py 81, meristem/gates/__init__.py 15. core pressure is 2982/3000 (0.99). closure pressure is 38916/50000 (0.78). The failure-aggregator and selfcheck-runner organs are already externalized but their kernel call sites remain unwired (G-008, G-009), and wiring them requires loop.py lines that cannot fit under the current cap. proposed: 3200. expected: no direct closure impact; the 200-line relief allows wiring the selfcheck-runner call site and failure-aggregator journal recording without fragmenting loop.py's control flow into premature externalizations that would increase closure pressure through new organ files.
