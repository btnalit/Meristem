#!/usr/bin/env python3
"""Extract nodes from Meristem's own state.

Reads state/patterns.md, state/gaps.md, state/backlog.md and
state/journal.jsonl from a workdir, and returns a list of node
dicts for the memory-graph organ.

Nodes are created for:
  - each pattern (P-NNN, kind "pattern")
  - each gap (G-NNN, kind "gap")
  - each backlog entry (B-NNN, kind "backlog")
  - each organ found in body/organs/ (kind "organ")
  - each cycle record in the journal (kind "cycle")

Each node carries id, kind, title, and last_seen_cycle.
Pattern and gap nodes derive their last_seen_cycle from journal
evidence: the highest cycle number among cycle records whose
"why" text mentions that id; if none, the highest cycle that
changed the containing register file; otherwise 0.
"""

from __future__ import annotations

import json
import pathlib
import re


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _parse_entries(text: str) -> list[dict]:
    """Parse headings and optional text from a markdown register file.

    Returns a list of dicts with 'id', 'title', 'kind'.
    """
    entries = []
    for match in re.finditer(r"^##\s+(\w+-\d+)\s*[—\-]\s*(.+)", text, re.M):
        entries.append({
            "id": match.group(1),
            "title": match.group(2).strip(),
        })
    return entries


def _build_cycle_map(workdir: pathlib.Path) -> tuple[dict[str, int], dict[str, int]]:
    """Build two maps from journal cycle records:

    - id_mentions: mapping from pattern/gap id to the highest cycle number
      whose "why" text contains that id.
    - register_changes: highest cycle number where "changed" list includes
      "state/patterns.md" or "state/gaps.md".
    """
    journal = _read_jsonl(workdir / "state" / "journal.jsonl")
    id_mentions: dict[str, int] = {}
    register_max_cycle = 0

    for row in journal:
        if row.get("kind") != "cycle":
            continue
        cycle = row.get("cycle", 0)
        why = row.get("why", "")
        # Check for P-NNN or G-NNN mentions in the why text
        for match in re.finditer(r"(\w+-\d+)", why):
            eid = match.group(1)
            # Only consider patterns and gaps (P- and G- prefixes)
            if eid.startswith(("P-", "G-")):
                if cycle > id_mentions.get(eid, 0):
                    id_mentions[eid] = cycle
        # Check if this cycle changed state/patterns.md or state/gaps.md
        changed = row.get("changed") or []
        if isinstance(changed, list):
            for f in changed:
                if f in ("state/patterns.md", "state/gaps.md"):
                    if cycle > register_max_cycle:
                        register_max_cycle = cycle

    return id_mentions, {"": register_max_cycle}


def extract(workdir: str = ".") -> list[dict]:
    """Return a list of node dicts from the given workdir."""
    wd = pathlib.Path(workdir).resolve()
    nodes: list[dict] = []

    # Build journal-derived cycle maps
    id_mentions, _ = _build_cycle_map(wd)
    # Find the highest cycle that changed either register file
    register_max_cycle = 0
    journal = _read_jsonl(wd / "state" / "journal.jsonl")
    for row in journal:
        if row.get("kind") != "cycle":
            continue
        changed = row.get("changed") or []
        if isinstance(changed, list):
            for f in changed:
                if f in ("state/patterns.md", "state/gaps.md"):
                    if row.get("cycle", 0) > register_max_cycle:
                        register_max_cycle = row.get("cycle", 0)

    # ---- Patterns ----
    patterns_path = wd / "state" / "patterns.md"
    if patterns_path.exists():
        text = patterns_path.read_text(encoding="utf-8")
        for entry in _parse_entries(text):
            eid = entry["id"]
            # Determine last_seen_cycle
            last = id_mentions.get(eid)
            if last is None:
                last = register_max_cycle
            nodes.append({
                "id": eid,
                "kind": "pattern",
                "title": entry["title"],
                "last_seen_cycle": last,
            })

    # ---- Gaps ----
    gaps_path = wd / "state" / "gaps.md"
    if gaps_path.exists():
        text = gaps_path.read_text(encoding="utf-8")
        for entry in _parse_entries(text):
            eid = entry["id"]
            last = id_mentions.get(eid)
            if last is None:
                last = register_max_cycle
            nodes.append({
                "id": eid,
                "kind": "gap",
                "title": entry["title"],
                "last_seen_cycle": last,
            })

    # ---- Backlog ----
    backlog_path = wd / "state" / "backlog.md"
    if backlog_path.exists():
        text = backlog_path.read_text(encoding="utf-8")
        for entry in _parse_entries(text):
            nodes.append({
                "id": entry["id"],
                "kind": "backlog",
                "title": entry["title"],
                "last_seen_cycle": 0,  # backlog entries have no default dating
            })

    # ---- Organs ----
    organs_dir = wd / "body" / "organs"
    if organs_dir.is_dir():
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
            organ_id = manifest.get("id", entry.name)
            nodes.append({
                "id": organ_id,
                "kind": "organ",
                "title": manifest.get("capability", ""),
                "last_seen_cycle": 0,  # organs don't have a cycle-based dating yet
            })

    # ---- Cycles ----
    for row in journal:
        if row.get("kind") != "cycle":
            continue
        cycle = row.get("cycle", 0)
        why = row.get("why", "")[:80]
        nodes.append({
            "id": f"cycle-{cycle}",
            "kind": "cycle",
            "title": why,
            "last_seen_cycle": cycle,
            "outcome": row.get("outcome", ""),
            "changed": row.get("changed", []),
        })

    return nodes


if __name__ == "__main__":
    import sys
    import json
    nodes = extract(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(json.dumps(nodes, ensure_ascii=False, indent=2))
