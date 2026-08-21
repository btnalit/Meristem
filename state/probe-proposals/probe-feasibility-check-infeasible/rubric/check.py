#!/usr/bin/env python3
"""Rubric: verify feasibility-check returns feasible=false for an infeasible task.

Scores by behaviour: invokes the organ over its ABI with a clearly
infeasible task and checks the verdict. Does not inspect source.
"""
import json
import subprocess
import sys
import pathlib


def main():
    payload = json.loads(sys.stdin.read())
    workdir = pathlib.Path(payload["workdir"])
    organ_dir = workdir / "body" / "organs" / "feasibility-check"

    if not organ_dir.is_dir():
        print(json.dumps({"score": 0.0, "detail": "organ directory not found"}))
        return

    manifest_path = organ_dir / "organ.json"
    if not manifest_path.exists():
        print(json.dumps({"score": 0.0, "detail": "organ.json not found"}))
        return

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"score": 0.0, "detail": f"cannot read organ.json: {exc}"}))
        return

    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, list) or not entrypoint:
        print(json.dumps({"score": 0.0, "detail": "manifest entrypoint is not a non-empty list"}))
        return

    # A clearly infeasible task: exceeds both the kernel line cap and the
    # closure budget by orders of magnitude.
    organ_input = {
        "task": "Rewrite every file in meristem/ and every organ in body/organs/ simultaneously in one mutation, adding 5000 lines of new kernel code and 10 new organs",
        "repo_path": str(workdir),
    }

    try:
        result = subprocess.run(
            entrypoint,
            input=json.dumps(organ_input),
            capture_output=True, text=True, timeout=30,
            cwd=str(organ_dir),
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"score": 0.0, "detail": "organ timed out after 30s"}))
        return
    except FileNotFoundError:
        print(json.dumps({"score": 0.0, "detail": f"entrypoint not found: {entrypoint}"}))
        return

    if result.returncode != 0:
        print(json.dumps({"score": 0.0, "detail": f"organ exited {result.returncode}: {result.stderr[:200]}"}))
        return

    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"score": 0.0, "detail": f"non-JSON output: {result.stdout[:200]}"}))
        return

    feasible = output.get("feasible")
    if feasible is False:
        print(json.dumps({"score": 100.0, "detail": "correctly identified infeasible task"}))
    elif feasible is True:
        print(json.dumps({"score": 0.0, "detail": "incorrectly accepted an infeasible task (false positive) -- conservative-by-construction violated"}))
    else:
        print(json.dumps({"score": 0.0, "detail": f"no 'feasible' field in output: keys={list(output.keys())}"}))


if __name__ == "__main__":
    main()
