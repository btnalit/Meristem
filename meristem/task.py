"""meristem.task -- topic selection (SS10.1).

Agenda reading / taking one task / marking completion. The seed has no
ledger-write interface: done/parked status comes only from the soil's
read-only projection (seed/feedback.json).
"""
from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass, field

from meristem import SEED_DIR, read_json_readonly


def task_id(text: str) -> str:
    """Task identity = content hash (SS4.1: same text is the same task)."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ReadOnlyFacts:
    """The soil-rendered projection. Tolerant of absence: the soil's
    freshness gate (CA-12) runs before the seed is invoked, not here --
    the seed has no read access to the ledger it would check against.
    """

    done_ids: frozenset[str] = field(default_factory=frozenset)
    parked_ids: frozenset[str] = field(default_factory=frozenset)
    core_pressure: float = 0.0
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, feedback_path: pathlib.Path | None = None) -> "ReadOnlyFacts":
        path = feedback_path if feedback_path is not None else (SEED_DIR / "feedback.json")
        doc = read_json_readonly(path) or {}
        facts = doc.get("facts", {}) if isinstance(doc, dict) else {}
        if not isinstance(facts, dict):
            facts = {}
        return cls(
            done_ids=frozenset(facts.get("done_task_ids", []) or []),
            parked_ids=frozenset(facts.get("parked_task_ids", []) or []),
            core_pressure=float(facts.get("core_pressure", 0.0) or 0.0),
            raw=facts,
        )


def _agenda_lines(agenda: pathlib.Path) -> list[str]:
    """seed/agenda.md -> ordered task texts. Format convention (not spec'd
    elsewhere): a non-empty, non "#"-comment line is a task; an optional
    "- "/"* " list marker is stripped.
    """
    if not agenda.exists():
        return []
    lines: list[str] = []
    for raw in agenda.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line[:2] in ("- ", "* "):
            line = line[2:].strip()
        if line:
            lines.append(line)
    return lines


def done_tasks(facts: ReadOnlyFacts) -> set[str]:
    return set(facts.done_ids)


def parked_tasks(facts: ReadOnlyFacts) -> set[str]:
    return set(facts.parked_ids)


def take_task(agenda: pathlib.Path, facts: ReadOnlyFacts) -> str | None:
    """First agenda task that is neither done nor parked; else None."""
    done, parked = done_tasks(facts), parked_tasks(facts)
    for text in _agenda_lines(agenda):
        tid = task_id(text)
        if tid in done or tid in parked:
            continue
        return text
    return None
