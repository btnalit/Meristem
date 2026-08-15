#!/usr/bin/env python3
"""Rubric for probe-journal-query-basic.

Scores by INVOKING body/organs/journal-query/main.py over its ABI.
Never reads the organ's source.
"""
import json
import pathlib
import subprocess
import sys
import tempfile


def jline(**kw):
    """Build one JSONL journal entry as a string."""
    return json.dumps(kw)


def write_journal(path, lines):
    """Write fixture journal lines to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def call_organ(organ_path, payload):
    """Invoke the organ over its ABI: stdin JSON -> stdout JSON."""
    proc = subprocess.run(
        [sys.executable, str(organ_path)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
        cwd=str(organ_path.parent),
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None


def extract_count(resp):
    """Extract a count from the organ's response, handling shape variants."""
    if resp is None or not resp.get("ok"):
        return None
    result = resp.get("result", {})
    if isinstance(result, dict):
        for key in ("count", "rejections_for", "faults_for", "n", "total"):
            if key in result:
                return result[key]
    elif isinstance(result, (int, float)):
        return result
    return None


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    workdir = pathlib.Path(payload.get("workdir", ".")).resolve()
    organ = workdir / "body" / "organs" / "journal-query" / "main.py"

    if not organ.exists():
        print(json.dumps({"score": 0.0, "detail": "organ main.py not found"}))
        return

    score = 0.0
    notes = []

    # --- Basic correctness: mixed rejections, faults, and other outcomes ---
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        jpath = tmpdir / "state" / "journal.jsonl"
        write_journal(jpath, [
            jline(kind="cycle", cycle=1, why="alpha", outcome="rejected"),
            jline(kind="cycle", cycle=2, why="alpha", outcome="rejected"),
            jline(kind="cycle", cycle=3, why="alpha", outcome="rejected"),
            jline(kind="fault", cycle=3, error="unparseable"),
            jline(kind="cycle", cycle=4, why="beta", outcome="candidate"),
        ])

        # rejections_for("alpha") should be 2: cycles 1,2 are rejected without
        # fault; cycle 3 is rejected BUT has a fault record, so it must NOT
        # count as a judged rejection.
        resp = call_organ(organ, {"op": "rejections_for",
                                  "args": {"task": "alpha", "workdir": str(tmpdir)}})
        count = extract_count(resp)
        if count == 2:
            score += 35.0
            notes.append("rejections_for(alpha)=2 correct")
        else:
            notes.append(f"rejections_for(alpha)={count} (expected 2)")

        # faults_for("alpha") should be 1: only cycle 3 has a fault record.
        resp = call_organ(organ, {"op": "faults_for",
                                  "args": {"task": "alpha", "workdir": str(tmpdir)}})
        count = extract_count(resp)
        if count == 1:
            score += 35.0
            notes.append("faults_for(alpha)=1 correct")
        else:
            notes.append(f"faults_for(alpha)={count} (expected 1)")

    # --- Discriminating case: a single cycle that is BOTH "rejected" AND has
    #     a fault record. It must count toward faults_for and NOT toward
    #     rejections_for. An implementation that ignores fault records gets
    #     rejections_for=1 (wrong) and faults_for=0 (wrong). ---
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        jpath = tmpdir / "state" / "journal.jsonl"
        write_journal(jpath, [
            jline(kind="cycle", cycle=1, why="gamma", outcome="rejected"),
            jline(kind="fault", cycle=1, error="timeout"),
        ])

        resp = call_organ(organ, {"op": "rejections_for",
                                  "args": {"task": "gamma", "workdir": str(tmpdir)}})
        count = extract_count(resp)
        if count == 0:
            score += 15.0
            notes.append("discriminating: rejected+fault NOT in rejections (correct)")
        else:
            notes.append(f"discriminating: rejections_for(gamma)={count} (expected 0)")

        resp = call_organ(organ, {"op": "faults_for",
                                  "args": {"task": "gamma", "workdir": str(tmpdir)}})
        count = extract_count(resp)
        if count == 1:
            score += 15.0
            notes.append("discriminating: rejected+fault IN faults (correct)")
        else:
            notes.append(f"discriminating: faults_for(gamma)={count} (expected 1)")

    print(json.dumps({"score": score, "detail": "; ".join(notes)}))


if __name__ == "__main__":
    main()
