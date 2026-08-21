#!/usr/bin/env python3
"""Rubric: verify feasibility-check returns feasible=true for a simple task.

Scores by behaviour: invokes the organ over its ABI with a clearly feasible
task and checks the verdict. Does not inspect source.
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

    # A clearly feasible task: small, one-file, well within both caps.
    organ_input = {
        "task": "Fix a typo in README.md",
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
    if feasible is True:
        print(json.dumps({"score": 100.0, "detail": "correctly identified feasible task"}))
    elif feasible is False:
        print(json.dumps({"score": 0.0, "detail": "incorrectly rejected a feasible task (false negative)"}))
    else:
        print(json.dumps({"score": 0.0, "detail": f"no 'feasible' field in output: keys={list(output.keys())}"}))


if __name__ == "__main__":
    main()
