"""Extraction module for the memory-graph organ.

Reads Meristem's own state files and returns a flat list of node dicts.
No graph logic, no edges — extraction only. This is the first step of
building a self-updating knowledge graph: get the nodes right before
deriving relationships.

Each node carries:
  - id: a unique identifier (P-013, G-006, organ id, or C-001)
  - kind: "pattern", "gap", "organ", or "cycle"
  - title: a human-readable label
  - last_seen_cycle: the most recent cycle number that referenced this node

Cycle nodes additionally carry "outcome" and "changed" (the files the
cycle modified), as specified by the task.

state/backlog.md is read for completeness but does not yet produce nodes;
it will feed into edge derivation in a later step.
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


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return None


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


def _extract_markdown_entries(text: str, prefix: str) -> list[tuple[str, str]]:
    """Extract (id, title) pairs from markdown headings like '## P-013 — title'.

    The em-dash, en-dash, and hyphen are all accepted as separators since
    different authors (and different model outputs) use different dashes.
    """
    pattern = re.compile(
        r"^##\s+(" + prefix + r"-\d+)\s*[—–-]\s*(.+)$", re.MULTILINE
    )
    return [(m.group(1), m.group(2).strip()) for m in pattern.finditer(text)]


def _last_cycle_mentioning(node_id: str, journal: list[dict]) -> int:
    """Highest cycle number whose journal record text mentions node_id.

    Searches the text-valued fields of every journal entry (not just cycle
    records) because a pattern or gap may be referenced in a usage row's
    rationale or a fault record's error text.
    """
    best = 0
    for row in journal:
        cycle = row.get("cycle", 0)
        if not isinstance(cycle, (int, float)):
            continue
        haystack = " ".join(
            str(row.get(k, ""))
            for k in ("why", "rationale", "reason", "what", "error", "task")
        )
        if node_id in haystack:
            best = max(best, int(cycle))
    return best


def _last_cycle_touching_organ(organ_id: str, journal: list[dict]) -> int:
    """Highest cycle number whose 'what' list includes a path under
    body/organs/<organ_id>/."""
    prefix = f"body/organs/{organ_id}/"
    best = 0
    for row in journal:
        if row.get("kind") != "cycle":
            continue
        cycle = row.get("cycle", 0)
        if not isinstance(cycle, (int, float)):
            continue
        changed = row.get("what")
        if not isinstance(changed, list):
            continue
        for path in changed:
            if isinstance(path, str) and path.startswith(prefix):
                best = max(best, int(cycle))
                break
    return best


def extract(workdir) -> list[dict]:
    """Read state files from *workdir* and return a list of node dicts.

    Produces one node per pattern (P-NNN), per gap (G-NNN), per organ found
    in body/organs/, and per cycle record in the journal. Each node carries
    id, kind, title, and last_seen_cycle. Cycle nodes also carry outcome and
    changed. No edges, no graph logic.
    """
    workdir = pathlib.Path(workdir)
    journal = _read_jsonl(workdir / "state" / "journal.jsonl")
    nodes: list[dict] = []

    # --- Patterns (state/patterns.md) ---
    for pid, title in _extract_markdown_entries(
        _read_text(workdir / "state" / "patterns.md"), "P"
    ):
        nodes.append({
            "id": pid,
            "kind": "pattern",
            "title": title,
            "last_seen_cycle": _last_cycle_mentioning(pid, journal),
        })

    # --- Gaps (state/gaps.md) ---
    for gid, title in _extract_markdown_entries(
        _read_text(workdir / "state" / "gaps.md"), "G"
    ):
        nodes.append({
            "id": gid,
            "kind": "gap",
            "title": title,
            "last_seen_cycle": _last_cycle_mentioning(gid, journal),
        })

    # --- Backlog (state/backlog.md) ---
    # Read for completeness; no nodes produced yet. Backlog entries do not
    # carry the P-NNN / G-NNN id convention and are not listed as a node kind
    # in the task specification. The text is available for future edge
    # derivation without re-reading the file.
    _read_text(workdir / "state" / "backlog.md")

    # --- Organs (body/organs/*/organ.json) ---
    organs_dir = workdir / "body" / "organs"
    if organs_dir.is_dir():
        for entry in sorted(organs_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest = _read_json(entry / "organ.json")
            if manifest is None:
                continue
            organ_id = manifest.get("id", entry.name)
            nodes.append({
                "id": organ_id,
                "kind": "organ",
                "title": manifest.get("capability", organ_id),
                "last_seen_cycle": _last_cycle_touching_organ(organ_id, journal),
            })

    # --- Cycle records (state/journal.jsonl) ---
    for row in journal:
        if row.get("kind") != "cycle":
            continue
        cycle_num = row.get("cycle", 0)
        if not isinstance(cycle_num, (int, float)):
            continue
        cycle_num = int(cycle_num)
        changed = row.get("what", [])
        if not isinstance(changed, list):
            changed = []
        nodes.append({
            "id": f"C-{cycle_num:03d}",
            "kind": "cycle",
            "title": row.get("why", f"cycle {cycle_num}"),
            "last_seen_cycle": cycle_num,
            "outcome": row.get("outcome", ""),
            "changed": [str(p) for p in changed],
        })

    return nodes
