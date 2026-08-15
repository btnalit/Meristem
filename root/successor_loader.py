"""ROOT OF TRUST -- succession. P0 STUB: refuses everything.

The design principle this will eventually implement:

    The current generation may not rewrite the ground it stands on, but it
    may produce the next generation and migrate to it.

        generation N -> candidate substrate N+1 -> parallel boot
        -> cross-generation evaluation -> promote pointer -> generation N+1

Two things must live here and nowhere else, permanently:
  * the generation registry (which substrate is live, which is last-good)
  * the AUTHORITY to run cross-generation checks and count their votes

The CONTENT of those checks may evolve at the substrate tier. The fact that
they run, and who counts, may not -- otherwise a candidate could grade its
own succession.

P0 ships this refusing every call on purpose. A protocol that has never run
is spec fiction, and succession is deliberately designed now, exercised late
(P3). Wiring it before the loop has any track record would be the fastest
way to lose the ground.
"""

from __future__ import annotations

import json
import pathlib

REGISTRY = pathlib.Path(__file__).resolve().parent / "generations.json"


class SuccessionRefused(RuntimeError):
    pass


def registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"live": "gen-0", "last_good": "gen-0", "generations": ["gen-0"]}


def propose(_candidate: str) -> None:
    raise SuccessionRefused(
        "succession is not enabled in P0 (see control/constitution.md, layer 7). "
        "The substrate is frozen in practice until P3; the thaw path is specified, "
        "not open."
    )


def promote(_candidate: str) -> None:
    raise SuccessionRefused("succession is not enabled in P0")


if __name__ == "__main__":
    print(json.dumps(registry(), indent=2))
