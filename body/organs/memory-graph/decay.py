"""Decay scoring for the memory graph.

Activation is a decaying signal: a node seen N cycles ago has half the
activation of one seen now.  Reinforcement comes from edges — a node pointed
at by something fresher than itself gets a bonus, because the graph says
"this relation is still live."

Decay never deletes.  It ranks, it does not forget.  A node with activation
0.001 is still in the dict; it is just very quiet.  The stale() function
returns ids below a threshold, but the threshold is the caller's choice —
the data is all still there.

This module is pure: it takes data in, returns data out, and touches no
files.  The graph is built by extract.py and edges.py; decay only scores it.
"""

from __future__ import annotations

#: Activation halves every this many cycles since last_seen_cycle.
HALF_LIFE = 40


def _node_map(nodes: list[dict]) -> dict[str, dict]:
    """Index nodes by id for O(1) lookup."""
    return {n["id"]: n for n in nodes}


def _decay_activation(last_seen_cycle: int, current_cycle: int) -> float:
    """Exponential decay: halves every HALF_LIFE cycles since last seen.

    A node seen right now has activation 1.0.  A node seen HALF_LIFE cycles
    ago has 0.5.  A node seen 2*HALF_LIFE cycles ago has 0.25.  The curve is
    continuous, not stepped, so partial half-lives produce partial decay.
    """
    age = current_cycle - last_seen_cycle
    if age <= 0:
        return 1.0
    return 0.5 ** (age / HALF_LIFE)


def activation(
    nodes: list[dict], edges: list[dict], current_cycle: int
) -> dict[str, float]:
    """Compute each node's activation as decay plus reinforcement.

    Base activation decays from 1.0 (just seen) by halving every HALF_LIFE
    cycles since last_seen_cycle.  Each edge pointing at a node from a source
    whose last_seen_cycle is more recent than the target's adds a bonus equal
    to the source's own decay activation times the edge weight.

    A node with no incoming fresh edges is ranked purely by its own age.
    A node with many fresh incoming edges stays alive even if it was last
    seen long ago — the graph says it still matters.
    """
    node_map = _node_map(nodes)
    result: dict[str, float] = {}

    for node in nodes:
        nid = node["id"]
        last = node.get("last_seen_cycle", current_cycle)
        result[nid] = _decay_activation(last, current_cycle)

    for edge in edges:
        src_id = edge.get("from", "")
        dst_id = edge.get("to", "")
        if src_id not in node_map or dst_id not in node_map:
            continue
        src_last = node_map[src_id].get("last_seen_cycle", current_cycle)
        dst_last = node_map[dst_id].get("last_seen_cycle", current_cycle)
        if src_last > dst_last:
            weight = float(edge.get("weight", 1.0))
            result[dst_id] += _decay_activation(src_last, current_cycle) * weight

    return result


def stale(
    nodes: list[dict],
    edges: list[dict],
    current_cycle: int,
    threshold: float,
) -> list[str]:
    """Node ids whose activation falls below the threshold, most stale first.

    Decay ranks; it does not forget.  Every node is still in the graph —
    this just returns the ones that are quietest right now.  A node with a
    fresh inbound edge will not appear here even if it was last seen long
    ago, because reinforcement keeps its activation above the threshold.
    """
    scores = activation(nodes, edges, current_cycle)
    below = [(nid, score) for nid, score in scores.items() if score < threshold]
    below.sort(key=lambda pair: pair[1])
    return [nid for nid, _ in below]
