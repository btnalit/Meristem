"""Extract nodes from Meristem's records.

Reads state/patterns.md, state/gaps.md, state/backlog.md, and
state/journal.jsonl from a workdir given as an argument, and returns a
list of node dicts.

Each node carries id, kind, title, and last_seen_cycle.
Pattern and gap nodes additionally carry "text" (the body of their entry).
Cycle nodes additionally carry "changed" (list of changed files) and
"why" (the task text).
Organ nodes additionally carry "probes" (declared probe ids).
"""

from __future__ import annotations

import json
import pathlib
import re


def _read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return ""


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _date_node(node_id: str, register_file: str, journal_rows: list[dict]) -> int:
    """Find last_seen_cycle for a pattern or gap node from journal evidence.

    1. Highest cycle number among journal cycle records whose "why" text
       mentions this node id.
    2. Fallback: highest cycle whose changed-file list ("what") includes
       the register file that holds this entry.
    3. Fallback: 0.
    """
    mentioned = [
        row.get("cycle", 0)
        for row in journal_rows
        if row.get("kind") == "cycle"
        and node_id in str(row.get("why", ""))
    ]
    if mentioned:
        return max(mentioned)

    changed_cycles = [
        row.get("cycle", 0)
        for row in journal_rows
        if row.get("kind") == "cycle"
        and any(register_file in str(f) for f in (row.get("what") or []))
    ]
    if changed_cycles:
        return max(changed_cycles)

    return 0


def _parse_register(
    text: str, prefix: str, kind: str, register_file: str,
    journal_rows: list[dict],
) -> list[dict]:
    """Parse a register file for entries like ## P-013 — Title.

    Each entry produces a node with id, kind, title, text (the body after
    the heading line), and last_seen_cycle (dated from journal evidence).
    """
    nodes: list[dict] = []
    entries = re.split(r"^##\s+", text, flags=re.M)
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        match = re.match(rf"({prefix}-\d+)\s*[—–\-]\s*(.+)", entry)
        if not match:
            continue
        node_id = match.group(1)
        title = match.group(2).strip()
        body = entry[match.end():].strip()
        last_seen = _date_node(node_id, register_file, journal_rows)
        nodes.append({
            "id": node_id,
            "kind": kind,
            "title": title,
            "text": body,
            "last_seen_cycle": last_seen,
        })
    return nodes


def _extract_organs(workdir: pathlib.Path) -> list[dict]:
    """Find organs in body/organs/ and create nodes for them."""
    nodes: list[dict] = []
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


def _extract_cycles(journal_rows: list[dict]) -> list[dict]:
    """Create nodes from journal cycle records.

    Each cycle node carries "changed" (the list of files it changed, from
    the journal's "what" field) and "why" (its task text), in addition to
    id, kind, title, and last_seen_cycle. The title is set to the task text
    so that edge rules matching on a cycle's title can find pattern/gap ids
    mentioned in it.
    """
    nodes: list[dict] = []
    for row in journal_rows:
        if row.get("kind") != "cycle":
            continue
        cycle_num = row.get("cycle", 0)
        why = str(row.get("why", ""))
        changed = row.get("what") or []
        nodes.append({
            "id": f"cycle-{cycle_num}",
            "kind": "cycle",
            "title": why,
            "last_seen_cycle": cycle_num,
            "changed": changed,
            "why": why,
            "outcome": row.get("outcome", ""),
        })
    return nodes


def extract(workdir: str | pathlib.Path = ".") -> list[dict]:
    """Read Meristem's records and return a list of node dicts.

    One node per pattern (P-NNN), per gap (G-NNN), per organ found in
    body/organs/, and per cycle record in the journal.
    """
    workdir = pathlib.Path(workdir).resolve()

    journal_rows = _read_jsonl(workdir / "state" / "journal.jsonl")

    nodes: list[dict] = []

    # Patterns
    patterns_text = _read_text(workdir / "state" / "patterns.md")
    nodes += _parse_register(
        patterns_text, "P", "pattern", "state/patterns.md", journal_rows
    )

    # Gaps
    gaps_text = _read_text(workdir / "state" / "gaps.md")
    nodes += _parse_register(
        gaps_text, "G", "gap", "state/gaps.md", journal_rows
    )

    # Backlog
    backlog_text = _read_text(workdir / "state" / "backlog.md")
    nodes += _parse_register(
        backlog_text, "B", "backlog", "state/backlog.md", journal_rows
    )

    # Organs
    nodes += _extract_organs(workdir)

    # Cycles
    nodes += _extract_cycles(journal_rows)

    return nodes
