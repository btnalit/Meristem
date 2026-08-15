"""Decay: activation scoring for memory-graph nodes.

Activation halves every N cycles since last_seen_cycle, plus a
reinforcement bonus for each inbound edge from a more recently seen node.
Decay ranks, it never deletes.
"""
from __future__ import annotations

HALF_LIFE = 40


def activation(nodes: list[dict], edges: list[dict],
               current_cycle: int) -> dict[str, float]:
    """Compute activation for each node id."""
    node_map = {n["id"]: n for n in nodes}
    result: dict[str, float] = {}
    for node in nodes:
        age = current_cycle - node.get("last_seen_cycle", 0)
        result[node["id"]] = 0.5 ** (age / HALF_LIFE)

    for edge in edges:
        src = node_map.get(edge["from"])
        dst_id = edge["to"]
        if src and dst_id in result:
            src_cycle = src.get("last_seen_cycle", 0)
            dst_cycle = node_map.get(dst_id, {}).get("last_seen_cycle", 0)
            if src_cycle > dst_cycle:
                result[dst_id] += 0.1 * edge.get("weight", 1.0)
    return result


def stale(nodes: list[dict], edges: list[dict],
          current_cycle: int, threshold: float) -> list[str]:
    """Return node ids whose activation is below threshold, ranked lowest first."""
    scores = activation(nodes, edges, current_cycle)
    ranked = sorted(
        ((nid, score) for nid, score in scores.items() if score < threshold),
        key=lambda x: x[1],
    )
    return [nid for nid, _ in ranked]
