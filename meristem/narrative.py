"""meristem.narrative -- narration, never a scorecard (SS3.2 S8 / SS10.1).

write_narrative only ever writes seed/narrative.md. print_status fulfills
the status CLI contract verbatim. REPORT.md is a soil derivative
(soil/report_renderer.py, projecting soil-owned facts plus this file's
narrative) -- the seed never writes REPORT.md (S8; SS4.3 forbidden table).
"""
from __future__ import annotations

import pathlib

from meristem.task import ReadOnlyFacts, take_task


def write_narrative(seed_dir: pathlib.Path) -> None:
    """Refresh seed/narrative.md: direction only -- open agenda item,
    parked/done counts. Nothing about scores.
    """
    facts = ReadOnlyFacts.load(seed_dir / "feedback.json")
    next_task = take_task(seed_dir / "agenda.md", facts)
    lines = [
        "# narrative",
        "",
        f"open agenda item: {next_task if next_task else '(none)'}",
        f"parked: {len(facts.parked_ids)}",
        f"done: {len(facts.done_ids)}",
    ]
    (seed_dir / "narrative.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_status(repo: pathlib.Path) -> int:
    """Emit, verbatim:

        core pressure: <float>
        open agenda item: <task text>   or   open agenda item: (none)

    The pressure number is computed by the soil; the seed only forwards it
    (S8).
    """
    seed_dir = repo / "seed"
    facts = ReadOnlyFacts.load(seed_dir / "feedback.json")
    item = take_task(seed_dir / "agenda.md", facts)
    print(f"core pressure: {facts.core_pressure:.2f}")
    print(f"open agenda item: {item if item else '(none)'}")
    return 0
