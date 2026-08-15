#!/usr/bin/env python3
"""Memory-graph organ: ABI entrypoint.

Reads one JSON object from stdin with "op" and "args", prints
{"ok": bool, "result": {...}}.

Ops:
  build     — extract, derive edges, return counts
  query     — args {"id": "..."}: node plus immediate neighbours and activations
  stale     — args {"threshold": float}: ranked stale list
  explain   — args {"id": "..."}: node's activation decomposed into its inputs
  selfcheck — exercise each module and the full pipeline
"""

import json
import sys
import pathlib

# Import sibling modules from this organ's own directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import extract
import edges
import decay


def _current_cycle(workdir):
    """Highest cycle number from the journal, or 0 if none."""
    journal = pathlib.Path(workdir) / "state" / "journal.jsonl"
    try:
        rows = []
        for line in journal.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return max(
            (r.get("cycle", 0) for r in rows if r.get("kind") == "cycle"),
            default=0,
        )
    except (OSError, FileNotFoundError):
        return 0


def _build_graph(workdir):
    """Extract nodes, derive edges, return (nodes, edges, current_cycle)."""
    nodes = extract.extract(workdir)
    derived_edges = edges.derive(nodes)
    current = _current_cycle(workdir)
    return nodes, derived_edges, current


def _node_by_id(nodes, node_id):
    for n in nodes:
        if n.get("id") == node_id:
            return n
    return None


def op_build(args):
    workdir = args.get("workdir", ".")
    nodes, derived_edges, current = _build_graph(workdir)
    return {"ok": True, "result": {
        "nodes": len(nodes),
        "edges": len(derived_edges),
        "current_cycle": current,
    }}


def op_query(args):
    workdir = args.get("workdir", ".")
    node_id = args.get("id", "")
    nodes, derived_edges, current = _build_graph(workdir)
    activations = decay.activation(nodes, derived_edges, current)
    node = _node_by_id(nodes, node_id)
    if node is None:
        return {"ok": False, "result": {"error": f"node '{node_id}' not found"}}
    inbound = [e for e in derived_edges if e["to"] == node_id]
    outbound = [e for e in derived_edges if e["from"] == node_id]
    neighbour_ids = set()
    for e in inbound:
        neighbour_ids.add(e["from"])
    for e in outbound:
        neighbour_ids.add(e["to"])
    neighbours = []
    for nid in sorted(neighbour_ids):
        n = _node_by_id(nodes, nid)
        if n:
            neighbours.append({
                "id": nid,
                "kind": n.get("kind", ""),
                "title": n.get("title", ""),
                "activation": activations.get(nid, 0.0),
            })
    return {"ok": True, "result": {
        "node": node,
        "activation": activations.get(node_id, 0.0),
        "neighbours": neighbours,
        "current_cycle": current,
    }}


def op_stale(args):
    workdir = args.get("workdir", ".")
    threshold = args.get("threshold", 0.5)
    nodes, derived_edges, current = _build_graph(workdir)
    stale_ids = decay.stale(nodes, derived_edges, current, threshold)
    activations = decay.activation(nodes, derived_edges, current)
    ranked = sorted(stale_ids, key=lambda nid: activations.get(nid, 0.0))
    return {"ok": True, "result": {
        "stale": ranked,
        "threshold": threshold,
        "current_cycle": current,
    }}


def op_explain(args):
    """Decompose a node's activation into the inputs that produced it.

    Returns the node's activation alongside last_seen_cycle, the current
    cycle, how many cycles have elapsed, and the list of inbound edges with
    the last_seen_cycle of each source. A score nobody can decompose is a
    score nobody can trust — this is the organ's own instrument for showing
    why it ranked something the way it did.
    """
    workdir = args.get("workdir", ".")
    node_id = args.get("id", "")
    nodes, derived_edges, current = _build_graph(workdir)
    activations = decay.activation(nodes, derived_edges, current)
    node = _node_by_id(nodes, node_id)
    if node is None:
        return {"ok": False, "result": {"error": f"node '{node_id}' not found"}}
    last_seen = node.get("last_seen_cycle", 0)
    elapsed = current - last_seen
    inbound = [e for e in derived_edges if e["to"] == node_id]
    inbound_with_sources = []
    for e in inbound:
        source = _node_by_id(nodes, e["from"])
        source_last_seen = source.get("last_seen_cycle", 0) if source else 0
        inbound_with_sources.append({
            "from": e["from"],
            "type": e.get("type", ""),
            "weight": e.get("weight", 1.0),
            "source_last_seen_cycle": source_last_seen,
        })
    return {"ok": True, "result": {
        "id": node_id,
        "kind": node.get("kind", ""),
        "title": node.get("title", ""),
        "activation": activations.get(node_id, 0.0),
        "last_seen_cycle": last_seen,
        "current_cycle": current,
        "elapsed_cycles": elapsed,
        "inbound_edges": inbound_with_sources,
    }}


def op_selfcheck(args):
    """Exercise each module alone and the full pipeline in sequence.

    Runs extract then edges then decay over the real workdir and fails
    when edges returns an empty list while extract returned more than ten
    nodes, or when every pattern node has last_seen_cycle 0. A self-check
    that only tests parts in isolation cannot see a broken contract between
    them.
    """
    workdir = args.get("workdir", ".")
    modules = []
    try:
        nodes = extract.extract(workdir)
        modules.append("extract")
    except Exception as exc:
        return {"ok": False, "result": {"modules": modules,
                                       "failed": f"extract: {exc}"}}
    try:
        derived_edges = edges.derive(nodes)
        modules.append("edges")
    except Exception as exc:
        return {"ok": False, "result": {"modules": modules,
                                       "failed": f"edges: {exc}"}}
    try:
        current = _current_cycle(workdir)
        decay.activation(nodes, derived_edges, current)
        modules.append("decay")
    except Exception as exc:
        return {"ok": False, "result": {"modules": modules,
                                       "failed": f"decay: {exc}"}}
    # Pipeline contract: the assembly must work, not just the parts.
    if len(nodes) > 10 and len(derived_edges) == 0:
        return {"ok": False, "result": {
            "modules": modules,
            "failed": "pipeline: edges returned 0 while extract returned >10 nodes",
        }}
    pattern_nodes = [n for n in nodes if n.get("kind") == "pattern"]
    if pattern_nodes and all(n.get("last_seen_cycle", 0) == 0 for n in pattern_nodes):
        return {"ok": False, "result": {
            "modules": modules,
            "failed": "pipeline: every pattern node has last_seen_cycle 0",
        }}
    return {"ok": True, "result": {"modules": modules}}


HANDLERS = {
    "build": op_build,
    "query": op_query,
    "stale": op_stale,
    "explain": op_explain,
    "selfcheck": op_selfcheck,
}


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "result": {"error": "invalid JSON on stdin"}}))
        sys.exit(1)
    op = payload.get("op", "")
    args = payload.get("args", {}) or {}
    handler = HANDLERS.get(op)
    if handler is None:
        print(json.dumps({"ok": False, "result": {"error": f"unknown op '{op}'"}}))
        sys.exit(1)
    try:
        response = handler(args)
        print(json.dumps(response))
    except Exception as exc:
        print(json.dumps({"ok": False, "result": {"error": str(exc)}}))
        sys.exit(1)


if __name__ == "__main__":
    main()
