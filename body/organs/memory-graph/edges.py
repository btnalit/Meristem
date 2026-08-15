"""Edge derivation for the memory graph.

Four rules, each a short loop over nodes. Substring match on ids only —
no regex, no graph library, no classes.
"""

from __future__ import annotations


def derive(nodes: list[dict]) -> list[dict]:
    """Return edges derived from node relationships.

    Each edge is {"from", "to", "type", "weight"} with weight 1.0.
    """
    edges: list[dict] = []
    ids = [n["id"] for n in nodes]
    pg_ids = [n["id"] for n in nodes if n.get("kind") in ("pattern", "gap")]

    # Rule 1: a pattern whose title or text mentions another node's id
    for n in nodes:
        if n.get("kind") != "pattern":
            continue
        hay = f"{n.get('title', '')} {n.get('text', '')}"
        for oid in ids:
            if oid != n["id"] and oid in hay:
                edges.append({"from": n["id"], "to": oid,
                              "type": "relates_to", "weight": 1.0})

    # Rule 2: a cycle whose title mentions a pattern or gap id
    for n in nodes:
        if n.get("kind") != "cycle":
            continue
        title = n.get("title", "")
        for pid in pg_ids:
            if pid in title:
                edges.append({"from": n["id"], "to": pid,
                              "type": "addresses", "weight": 1.0})

    # Rule 3: a cycle whose changed files include body/organs/<name>/
    for n in nodes:
        if n.get("kind") != "cycle":
            continue
        for path in n.get("changed", []):
            if path.startswith("body/organs/"):
                parts = path.split("/")
                if len(parts) >= 3 and parts[2]:
                    edges.append({"from": n["id"], "to": parts[2],
                                  "type": "touched", "weight": 1.0})

    # Rule 4: an organ measured_by each probe it declares
    for n in nodes:
        if n.get("kind") != "organ":
            continue
        for probe in n.get("probes", []):
            edges.append({"from": n["id"], "to": probe,
                          "type": "measured_by", "weight": 1.0})

    return edges
