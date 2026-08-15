#!/usr/bin/env python3
"""Memory-graph organ: ABI entrypoint.

Reads one JSON object from stdin with "op" and "args",
prints {"ok": bool, "result": {...}}.

Ops:
  build     — extract nodes, derive edges, return counts
  query     — return a node plus its immediate neighbours and their activations
  stale     — return ranked stale list
  selfcheck — exercise extract, edges, and decay with tiny fixtures
"""

import json
import pathlib
import sys
import tempfile

import extract
import edges
import decay


def _build(args):
    workdir = pathlib.Path(args.get("workdir", "."))
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    return {"ok": True, "result": {"nodes": len(nodes), "edges": len(edge_list)}}


def _query(args):
    workdir = pathlib.Path(args.get("workdir", "."))
    node_id = args.get("id", "")
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    node = None
    for n in nodes:
        if n.get("id") == node_id:
            node = n
            break
    if node is None:
        return {"ok": False, "result": {"error": f"node '{node_id}' not found"}}
    neighbours = []
    for e in edge_list:
        if e["from"] == node_id:
            for n in nodes:
                if n.get("id") == e["to"]:
                    neighbours.append(n)
        elif e["to"] == node_id:
            for n in nodes:
                if n.get("id") == e["from"]:
                    neighbours.append(n)
    current_cycle = max((n.get("last_seen_cycle", 0) for n in nodes), default=0)
    activations = decay.activation(nodes, edge_list, current_cycle)
    return {"ok": True, "result": {"node": node, "neighbours": neighbours, "activations": activations}}


def _stale(args):
    workdir = pathlib.Path(args.get("workdir", "."))
    threshold = float(args.get("threshold", 0.5))
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    current_cycle = max((n.get("last_seen_cycle", 0) for n in nodes), default=0)
    stale_list = decay.stale(nodes, edge_list, current_cycle, threshold)
    return {"ok": True, "result": {"stale": stale_list}}


def _selfcheck(args):
    """Exercise extract, edges, and decay with tiny in-memory fixtures.

    Returns {"ok": true, "result": {"modules": ["extract", "edges", "decay"]}}
    on success, or {"ok": false, "result": {"failed": "<module>"}} naming
    the first module that raised.
    """
    modules = []

    # extract: build a tiny workdir with minimal state files
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            state_dir = tmpdir / "state"
            state_dir.mkdir()
            (state_dir / "patterns.md").write_text(
                "## P-001 — test pattern\n\nbody text\n")
            (state_dir / "gaps.md").write_text(
                "## G-001 — test gap\n\nbody text\n")
            (state_dir / "backlog.md").write_text(
                "## B-001 — test backlog\n\nbody text\n")
            (state_dir / "journal.jsonl").write_text("")
            nodes = extract.extract(tmpdir)
        modules.append("extract")
    except Exception:
        return {"ok": False, "result": {"failed": "extract"}}

    # edges: derive from a tiny node list
    try:
        test_nodes = [
            {"id": "P-001", "kind": "pattern", "title": "test",
             "last_seen_cycle": 1},
            {"id": "C-1", "kind": "cycle", "title": "cycle 1",
             "last_seen_cycle": 1,
             "changed": ["body/organs/test/"], "why": "P-001"},
        ]
        edge_list = edges.derive(test_nodes)
        modules.append("edges")
    except Exception:
        return {"ok": False, "result": {"failed": "edges"}}

    # decay: activation and stale from tiny inputs
    try:
        test_nodes = [
            {"id": "P-001", "kind": "pattern", "title": "test",
             "last_seen_cycle": 1},
        ]
        test_edges = []
        activations = decay.activation(test_nodes, test_edges, 10)
        stale_list = decay.stale(test_nodes, test_edges, 10, 0.5)
        modules.append("decay")
    except Exception:
        return {"ok": False, "result": {"failed": "decay"}}

    return {"ok": True, "result": {"modules": modules}}


HANDLERS = {
    "build": _build,
    "query": _query,
    "stale": _stale,
    "selfcheck": _selfcheck,
}


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "result": {"error": "invalid JSON on stdin"}}))
        return 1

    op = payload.get("op", "")
    args = payload.get("args") or {}

    handler = HANDLERS.get(op)
    if handler is None:
        print(json.dumps({"ok": False, "result": {"error": f"unknown op '{op}'"}}))
        return 1

    try:
        result = handler(args)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "result": {"error": str(exc)}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
