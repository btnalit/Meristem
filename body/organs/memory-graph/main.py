#!/usr/bin/env python3
"""Memory-graph organ: ABI entrypoint.

Reads one JSON object from stdin with "op" and "args", prints
{"ok": bool, "result": {...}} to stdout.

Ops:
  build     — extract nodes, derive edges, return counts
  query     — return a node plus its immediate neighbours and their activations
  stale     — return ranked stale list
  explain   — return a node's activation with its decomposition
  selfcheck — prove the organ's own seams hold
"""

from __future__ import annotations

import json
import os
import sys

import extract
import edges
import decay


def _current_cycle(workdir: str) -> int:
    """Read the highest cycle number from the journal."""
    journal_path = os.path.join(workdir, "state", "journal.jsonl")
    max_cycle = 0
    try:
        with open(journal_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("kind") == "cycle":
                        cycle = record.get("cycle", 0)
                        if cycle > max_cycle:
                            max_cycle = cycle
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, NotADirectoryError):
        pass
    return max_cycle


def op_build(args):
    workdir = args.get("workdir", ".")
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    return {
        "ok": True,
        "result": {
            "nodes": len(nodes),
            "edges": len(edge_list),
        },
    }


def op_query(args):
    workdir = args.get("workdir", ".")
    node_id = args.get("id", "")
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    current_cycle = _current_cycle(workdir)
    activations = decay.activation(nodes, edge_list, current_cycle)

    node = None
    for n in nodes:
        if n.get("id") == node_id:
            node = n
            break

    if node is None:
        return {"ok": False, "result": {"error": f"node '{node_id}' not found"}}

    neighbours = []
    for e in edge_list:
        if e["from"] == node_id or e["to"] == node_id:
            other_id = e["to"] if e["from"] == node_id else e["from"]
            for n in nodes:
                if n.get("id") == other_id:
                    neighbours.append({
                        "node": n,
                        "edge": e,
                        "activation": activations.get(other_id, 0.0),
                    })
                    break

    return {
        "ok": True,
        "result": {
            "node": node,
            "activation": activations.get(node_id, 0.0),
            "neighbours": neighbours,
        },
    }


def op_stale(args):
    workdir = args.get("workdir", ".")
    threshold = args.get("threshold", 0.5)
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    current_cycle = _current_cycle(workdir)
    stale_list = decay.stale(nodes, edge_list, current_cycle, threshold)
    return {
        "ok": True,
        "result": {
            "stale": stale_list,
            "threshold": threshold,
        },
    }


def op_explain(args):
    workdir = args.get("workdir", ".")
    node_id = args.get("id", "")
    nodes = extract.extract(workdir)
    edge_list = edges.derive(nodes)
    current_cycle = _current_cycle(workdir)
    activations = decay.activation(nodes, edge_list, current_cycle)

    node = None
    for n in nodes:
        if n.get("id") == node_id:
            node = n
            break

    if node is None:
        return {"ok": False, "result": {"error": f"node '{node_id}' not found"}}

    last_seen = node.get("last_seen_cycle", 0)
    elapsed = current_cycle - last_seen

    inbound = []
    for e in edge_list:
        if e["to"] == node_id:
            source_id = e["from"]
            source_last_seen = 0
            for n in nodes:
                if n.get("id") == source_id:
                    source_last_seen = n.get("last_seen_cycle", 0)
                    break
            inbound.append({
                "from": source_id,
                "type": e.get("type", ""),
                "weight": e.get("weight", 1.0),
                "source_last_seen_cycle": source_last_seen,
            })

    return {
        "ok": True,
        "result": {
            "id": node_id,
            "activation": activations.get(node_id, 0.0),
            "last_seen_cycle": last_seen,
            "current_cycle": current_cycle,
            "elapsed": elapsed,
            "inbound_edges": inbound,
        },
    }


def op_selfcheck(args):
    """Prove the organ's own seams hold.

    Imports extract, edges and decay, calls each one's main entry with
    tiny in-memory fixtures, and returns ok true with the list of modules
    checked — or ok false naming the module that failed.

    This is the organ proving its own seams hold, so a mismatch between
    its parts is caught by the organ rather than by whoever calls it.
    """
    import tempfile
    import pathlib

    modules_checked = []

    # --- extract ---
    # extract reads from files, so we create a tiny temporary workdir.
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            state = tmpdir / "state"
            state.mkdir(parents=True)
            (state / "patterns.md").write_text(
                "## P-001 — test pattern\n", encoding="utf-8")
            (state / "gaps.md").write_text(
                "## G-001 — test gap\n", encoding="utf-8")
            (state / "backlog.md").write_text(
                "## B-001 — test backlog\n", encoding="utf-8")
            (state / "journal.jsonl").write_text(
                json.dumps({"kind": "cycle", "cycle": 1,
                            "outcome": "candidate", "why": "test",
                            "what": []}) + "\n",
                encoding="utf-8")
            nodes = extract.extract(str(tmpdir))
            assert isinstance(nodes, list), \
                f"extract returned {type(nodes).__name__}, expected list"
            modules_checked.append("extract")
    except Exception as exc:
        return {"ok": False,
                "result": {"module": "extract", "error": str(exc)}}

    # --- edges ---
    # Feed the nodes from extract into edges.derive to verify the seam.
    try:
        test_nodes = nodes if nodes else [
            {"id": "P-001", "kind": "pattern", "title": "test",
             "last_seen_cycle": 1},
            {"id": "G-001", "kind": "gap", "title": "test",
             "last_seen_cycle": 1},
        ]
        edge_list = edges.derive(test_nodes)
        assert isinstance(edge_list, list), \
            f"derive returned {type(edge_list).__name__}, expected list"
        modules_checked.append("edges")
    except Exception as exc:
        return {"ok": False,
                "result": {"module": "edges", "error": str(exc)}}

    # --- decay ---
    # Feed the nodes and edges into decay to verify the seam.
    try:
        test_edges = edge_list if edge_list else [
            {"from": "P-001", "to": "G-001", "type": "relates_to",
             "weight": 1.0},
        ]
        activations = decay.activation(test_nodes, test_edges, 10)
        assert isinstance(activations, dict), \
            f"activation returned {type(activations).__name__}, expected dict"
        stale_list = decay.stale(test_nodes, test_edges, 10, 0.5)
        assert isinstance(stale_list, list), \
            f"stale returned {type(stale_list).__name__}, expected list"
        modules_checked.append("decay")
    except Exception as exc:
        return {"ok": False,
                "result": {"module": "decay", "error": str(exc)}}

    return {"ok": True, "result": {"modules": modules_checked}}


OPS = {
    "build": op_build,
    "query": op_query,
    "stale": op_stale,
    "explain": op_explain,
    "selfcheck": op_selfcheck,
}


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    op = payload.get("op", "")
    args = payload.get("args") or {}

    handler = OPS.get(op)
    if handler is None:
        print(json.dumps({"ok": False,
                          "result": {"error": f"unknown op '{op}'"}}))
        return 1

    try:
        result = handler(args)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False,
                          "result": {"error": str(exc)}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
