#!/usr/bin/env python3
"""Rubric for probe-memory-graph-basic.

Scores the memory-graph organ by INVOKING it over its ABI — never by
reading its source.  Sends {"op": "build"} and {"op": "stale", "args":
{"threshold": 0.5}} and checks the shapes that come back.

Discriminating case: a node whose last_seen_cycle is far in the past
must rank below a recent one, and a stale list that returns a node with
a fresh inbound edge is wrong.
"""
import json, pathlib, subprocess, sys, tempfile


def _call_organ(organ_dir, payload):
    """Invoke memory-graph/main.py over its ABI: stdin JSON -> stdout JSON."""
    proc = subprocess.run(
        [sys.executable, str(organ_dir / "main.py")],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
        cwd=str(organ_dir),
    )
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {proc.stderr[:200]}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"non-JSON stdout: {exc}"


def _jline(kind, cycle, why, outcome="rejected"):
    """One journal record as a JSON string."""
    return json.dumps({"kind": kind, "cycle": cycle,
                       "why": why, "outcome": outcome})


def _make_fixture(root):
    """Create a fixture workdir with discriminating data.

    G-001: seen at cycle 5, never reinforced — must be stale.
    P-001: seen at cycle 100 — must NOT be stale.
    P-002: seen at cycle 5 BUT P-001 (cycle 100) names it in text,
           creating a fresh inbound edge — must NOT be stale.
    """
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)

    (state / "patterns.md").write_text(
        "## P-001 — recent\n\nThis relates to P-002.\n\n"
        "## P-002 — old but reinforced\n\nOld.\n",
        encoding="utf-8")
    (state / "gaps.md").write_text(
        "## G-001 — old\n\nOld.\n", encoding="utf-8")

    with (state / "journal.jsonl").open("w", encoding="utf-8") as f:
        f.write(_jline("cycle", 5, "G-001") + "\n")
        f.write(_jline("cycle", 5, "P-002") + "\n")
        f.write(_jline("cycle", 100, "P-001", "candidate") + "\n")

    return root


def _stale_ids(result):
    """Extract node ids from a stale result, regardless of format."""
    ids = set()
    items = result
    if isinstance(result, dict):
        items = result.get("stale") or result.get("nodes") or list(result.keys())
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                ids.add(item)
            elif isinstance(item, dict):
                nid = item.get("id") or item.get("node_id") or item.get("node")
                if nid:
                    ids.add(str(nid))
    return ids


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    workdir = pathlib.Path(payload.get("workdir", ".")).resolve()
    organ_dir = workdir / "body" / "organs" / "memory-graph"
    score = 0.0
    notes = []

    if not (organ_dir / "main.py").exists():
        print(json.dumps({"score": 0.0,
                          "detail": "memory-graph main.py not found"}))
        return

    # --- Phase 1: shape checks on the real workdir ---
    build, err = _call_organ(organ_dir, {"op": "build",
                                          "args": {"workdir": str(workdir)}})
    if err:
        notes.append(f"build: {err}")
        print(json.dumps({"score": 0.0, "detail": "; ".join(notes)}))
        return
    if not build.get("ok") or not isinstance(build.get("result"), dict):
        notes.append(f"build shape wrong: {build}")
        print(json.dumps({"score": 0.0, "detail": "; ".join(notes)}))
        return
    score += 20.0
    notes.append("build ok")

    stale, err = _call_organ(organ_dir, {"op": "stale",
                                          "args": {"threshold": 0.5,
                                                   "workdir": str(workdir)}})
    if err:
        notes.append(f"stale: {err}")
        print(json.dumps({"score": score, "detail": "; ".join(notes)}))
        return
    if not stale.get("ok") or not isinstance(stale.get("result"), (list, dict)):
        notes.append(f"stale shape wrong: {stale}")
        print(json.dumps({"score": score, "detail": "; ".join(notes)}))
        return
    score += 20.0
    notes.append("stale ok")

    # --- Phase 2: discriminating case ---
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _make_fixture(pathlib.Path(tmp))

        fb, err = _call_organ(organ_dir, {"op": "build",
                                           "args": {"workdir": str(fixture)}})
        if err or not fb or not fb.get("ok"):
            notes.append(f"fixture build: {err or fb}")
            print(json.dumps({"score": score, "detail": "; ".join(notes)}))
            return

        fs, err = _call_organ(organ_dir, {"op": "stale",
                                           "args": {"threshold": 0.5,
                                                    "workdir": str(fixture)}})
        if err or not fs or not fs.get("ok"):
            notes.append(f"fixture stale: {err or fs}")
            print(json.dumps({"score": score, "detail": "; ".join(notes)}))
            return

        stale_set = _stale_ids(fs.get("result", {}))

        # G-001: old and unreinforced — must be stale
        if "G-001" in stale_set:
            score += 20.0
            notes.append("G-001 old/unreinforced: stale (correct)")
        else:
            notes.append("G-001 old/unreinforced: NOT stale (wrong)")

        # P-001: recent — must NOT be stale
        if "P-001" not in stale_set:
            score += 20.0
            notes.append("P-001 recent: not stale (correct)")
        else:
            notes.append("P-001 recent: stale (wrong)")

        # P-002: old but has fresh inbound edge — must NOT be stale
        if "P-002" not in stale_set:
            score += 20.0
            notes.append("P-002 old/reinforced: not stale (correct)")
        else:
            notes.append("P-002 old/reinforced: stale (wrong — fresh edge ignored)")

    print(json.dumps({"score": score, "detail": "; ".join(notes)}))


if __name__ == "__main__":
    main()
