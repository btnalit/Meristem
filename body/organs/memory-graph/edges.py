"""Derive edges between nodes by simple substring matching on ids.

Four rules, each a short loop over nodes:
  - pattern mentions another node's id -> "relates_to"
  - cycle title mentions a pattern/gap id -> "addresses"
  - cycle changed files under body/organs/<name>/ -> "touched"
  - organ -> "measured_by" each probe it declares
"""
from __future__ import annotations


def derive(nodes: list[dict]) -> list[dict]:
    """Return a list of {"from", "to", "type", "weight"} edge dicts."""
    edges = []
    ids = {n["id"] for n in nodes}
    node_map = {n["id"]: n for n in nodes}

    for node in nodes:
        nid = node["id"]
        kind = node.get("kind", "")
        title = node.get("title", "")

        if kind == "pattern":
            for other_id in ids:
                if other_id != nid and other_id in title:
                    edges.append({"from": nid, "to": other_id,
                                  "type": "relates_to", "weight": 1.0})

        if kind == "cycle":
            for other_id in ids:
                if other_id != nid and other_id in title:
                    other = node_map.get(other_id)
                    if other and other.get("kind") in ("pattern", "gap"):
                        edges.append({"from": nid, "to": other_id,
                                      "type": "addresses", "weight": 1.0})
            for path in node.get("changed", []):
                parts = path.split("/")
                if len(parts) >= 3 and parts[0] == "body" and parts[1] == "organs":
                    if parts[2] in ids:
                        edges.append({"from": nid, "to": parts[2],
                                      "type": "touched", "weight": 1.0})

        if kind == "organ":
            for probe_id in node.get("probes", []):
                edges.append({"from": nid, "to": probe_id,
                              "type": "measured_by", "weight": 1.0})

    return edges
