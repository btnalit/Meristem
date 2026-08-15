"""Extract nodes from Meristem's state records.

Reads state/patterns.md, state/gaps.md, state/backlog.md and
state/journal.jsonl from a workdir, returning a list of node dicts.
No graph logic, no edges — extraction only.
"""
from __future__ import annotations

import json
import pathlib
import re


def _parse_register(text: str, kind: str, prefix: str) -> list[dict]:
    """Parse ## PREFIX-NNN — title headings into node dicts."""
    nodes = []
    for m in re.finditer(rf"^##\s+({prefix}-\d+)\s*[—–\-]\s*(.+)$", text, re.M):
        nodes.append({
            "id": m.group(1),
            "kind": kind,
            "title": m.group(2).strip(),
            "last_seen_cycle": 0,
        })
    return nodes


def _parse_organs(workdir: pathlib.Path) -> list[dict]:
    nodes = []
    organs_dir = workdir / "body" / "organs"
    if not organs_dir.is_dir():
        return nodes
    for entry in sorted(organs_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "organ.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        nodes.append({
            "id": manifest.get("id", entry.name),
            "kind": "organ",
            "title": manifest.get("capability", entry.name),
            "last_seen_cycle": 0,
            "probes": manifest.get("probes", []),
        })
    return nodes


def _parse_cycles(workdir: pathlib.Path) -> list[dict]:
    nodes = []
    journal = workdir / "state" / "journal.jsonl"
    if not journal.exists():
        return nodes
    for line in journal.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") != "cycle":
            continue
        cycle_num = record.get("cycle", 0)
        nodes.append({
            "id": f"cycle-{cycle_num}",
            "kind": "cycle",
            "title": record.get("why", ""),
            "last_seen_cycle": cycle_num,
            "outcome": record.get("outcome", ""),
            "changed": record.get("what", []),
        })
    return nodes


def extract(workdir) -> list[dict]:
    """Read state records and return a list of node dicts."""
    workdir = pathlib.Path(workdir)
    nodes: list[dict] = []
    state = workdir / "state"

    for filename, kind, prefix in [
        ("patterns.md", "pattern", "P"),
        ("gaps.md", "gap", "G"),
        ("backlog.md", "backlog", "B"),
    ]:
        path = state / filename
        if path.exists():
            nodes.extend(_parse_register(
                path.read_text(encoding="utf-8"), kind, prefix))

    nodes.extend(_parse_organs(workdir))
    nodes.extend(_parse_cycles(workdir))
    return nodes
