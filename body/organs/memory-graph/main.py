"""Memory-graph organ: ABI entrypoint.

Reads one JSON object from stdin with "op" and "args", prints
{"ok": bool, "result": {...}} to stdout.
"""
from __future__ import annotations

import json
import sys

import extract as extract_mod
import edges as edges_mod
import decay as decay_mod


def _current_cycle(nodes: list[dict]) -> int:
    return max((n.get("last_seen_cycle", 0) for n in nodes), default=0)


def _build(args: dict) -> dict:
    workdir = args.get("workdir", ".")
    nodes = extract_mod.extract(workdir)
    edge_list = edges_mod.derive(nodes)
    return {"nodes": len(nodes), "edges": len(edge_list)}


def _query(args: dict) -> dict:
    workdir = args.get("workdir", ".")
    target_id = args.get("id", "")
    nodes = extract_mod.extract(workdir)
    edge_list = edges_mod.derive(nodes)
    current = _current_cycle(nodes)
    activations = decay_mod.activation(nodes, edge_list, current)

    node = next((n for n in nodes if n["id"] == target_id), None)
    if node is None:
        return {"found": False}

    neighbours = []
    for edge in edge_list:
        if edge["from"] == target_id or edge["to"] == target_id:
            other_id = edge["to"] if edge["from"] == target_id else edge["from"]
            neighbour = next((n for n in nodes if n["id"] == other_id), None)
            neighbours.append({
                "id": other_id,
                "edge": edge,
                "activation": activations.get(other_id, 0.0),
                "node": neighbour,
            })

    return {
        "found": True,
        "node": node,
        "activation": activations.get(target_id, 0.0),
        "neighbours": neighbours,
    }


def _stale(args: dict) -> dict:
    workdir = args.get("workdir", ".")
    threshold = args.get("threshold", 0.5)
    nodes = extract_mod.extract(workdir)
    edge_list = edges_mod.derive(nodes)
    current = _current_cycle(nodes)
    stale_ids = decay_mod.stale(nodes, edge_list, current, threshold)
    return {"stale": stale_ids}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "result": {"error": "invalid JSON"}}))
        return 1

    op = payload.get("op", "")
    args = payload.get("args", {})

    handlers = {"build": _build, "query": _query, "stale": _stale}
    handler = handlers.get(op)
    if handler is None:
        print(json.dumps({"ok": False, "result": {"error": f"unknown op '{op}'"}}))
        return 1

    try:
        result = handler(args)
        print(json.dumps({"ok": True, "result": result}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "result": {"error": str(exc)}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
