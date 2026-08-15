#!/usr/bin/env python3
"""Memory-graph organ: ABI entrypoint.

Reads one JSON object from stdin with "op" and "args", prints
{"ok": bool, "result": {...}} on stdout.

Operations:
  build   — extract nodes, derive edges, return counts
  query   — args {"id": "..."}: return that node plus immediate neighbours
            and their activations
  stale   — args {"threshold": float}: return the ranked stale list

Uses extract.py, edges.py, and decay.py. Takes the workdir from args
or defaults to the current directory.
"""

from __future__ import annotations

import json
import pathlib
import sys

import decay
import edges
import extract


def _current_cycle(workdir: pathlib.Path) -> int:
    """Latest cycle number from the journal, or 0 if none."""
    journal = workdir / "state" / "journal.jsonl"
    if not journal.exists():
        return 0
    max_cycle = 0
    for line in journal.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        c = row.get("cycle")
        if isinstance(c, int) and c > max_cycle:
            max_cycle = c
    return max_cycle


def _build_graph(workdir: pathlib.Path):
    """Extract nodes and derive edges from the records in workdir."""
    nodes = extract.extract(workdir)
    edge_list = edges.derive_edges(nodes)
    return nodes, edge_list


def _op_build(args: dict, workdir: pathlib.Path) -> dict:
    nodes, edge_list = _build_graph(workdir)
    by_kind: dict[str, int] = {}
    for node in nodes:
        kind = node.get("kind", "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "nodes": len(nodes),
        "edges": len(edge_list),
        "by_kind": by_kind,
    }


def _op_query(args: dict, workdir: pathlib.Path) -> dict:
    node_id = args.get("id", "")
    if not node_id:
        return {"error": "query requires 'id' in args"}
    nodes, edge_list = _build_graph(workdir)
    cycle = _current_cycle(workdir)
    activations = decay.activation(nodes, edge_list, cycle)

    target = None
    for node in nodes:
        if node.get("id") == node_id:
            target = node
            break
    if target is None:
        return {"error": f"node '{node_id}' not found"}

    neighbour_ids: set[str] = set()
    for edge in edge_list:
        if edge.get("from") == node_id:
            neighbour_ids.add(edge.get("to", ""))
        if edge.get("to") == node_id:
            neighbour_ids.add(edge.get("from", ""))

    neighbours = []
    for node in nodes:
        nid = node.get("id", "")
        if nid in neighbour_ids:
            neighbours.append({
                **node,
                "activation": activations.get(nid, 0.0),
            })

    return {
        "node": {**target, "activation": activations.get(node_id, 0.0)},
        "neighbours": neighbours,
    }


def _op_stale(args: dict, workdir: pathlib.Path) -> dict:
    threshold = float(args.get("threshold", 0.1))
    nodes, edge_list = _build_graph(workdir)
    cycle = _current_cycle(workdir)
    stale_ids = decay.stale(nodes, edge_list, cycle, threshold)
    activations = decay.activation(nodes, edge_list, cycle)
    # Rank stalest first (lowest activation)
    ranked = sorted(stale_ids, key=lambda nid: activations.get(nid, 0.0))
    return {
        "threshold": threshold,
        "stale": ranked,
        "count": len(ranked),
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "result": {"error": f"invalid JSON: {exc}"}}))
        return 1

    op = payload.get("op", "")
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    workdir = pathlib.Path(args.get("workdir") or ".").resolve()

    handlers = {
        "build": _op_build,
        "query": _op_query,
        "stale": _op_stale,
    }

    handler = handlers.get(op)
    if handler is None:
        print(json.dumps({"ok": False, "result": {"error": f"unknown op '{op}'"}}))
        return 1

    try:
        result = handler(args, workdir)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "result": {"error": str(exc)}},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
