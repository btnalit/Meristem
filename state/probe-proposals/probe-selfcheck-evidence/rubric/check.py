#!/usr/bin/env python3
"""Probe: verify each organ's selfcheck exercises its primary function.

Runs each organ's selfcheck and checks that the output includes data that
could only come from the organ's primary function. Catches the class of
failure where a selfcheck is weakened to 'just runs' without testing the
core logic (FA-019).

Evidence requirements are EXPLICIT per organ. There is NO generic fallback:
an organ not in ORGAN_EVIDENCE fails the probe, not passes it silently.
Single-file organs are exempt from selfcheck per the germline protocol and
are noted in the detail, not silently skipped. Organs with missing or
unparseable manifests FAIL, not silently skipped.
"""
import json
import pathlib
import subprocess
import sys

# Explicit evidence requirements per organ id.
#
# "evidence_keys": keys that must appear in at least one result dict of the
# selfcheck output, with a TRUTHY value. This is data that could ONLY come
# from exercising the organ's primary function — not just any extra key
# beyond "name" and "ok".
#
# "require_all": if True, all keys must be present in the same result;
# if False (default), any one key suffices.
#
# An organ NOT in this mapping causes a FAILURE. This is deliberate: a
# generic "any extra key" fallback is the exact weakness that let FA-019
# through (cycle 257 rejections).
ORGAN_EVIDENCE = {
    "failure-aggregator": {
        "evidence_keys": ["class", "classification", "label"],
        "require_all": False,
        "description": "classification results",
    },
    "memory-graph": {
        "evidence_keys": ["edges", "nodes"],
        "require_all": False,
        "description": "edge/node data",
    },
}


def find_organs(workdir):
    """Find all organ directories under body/organs/."""
    organs_dir = workdir / "body" / "organs"
    if not organs_dir.is_dir():
        return []
    return sorted(d for d in organs_dir.iterdir() if d.is_dir())


def count_py_files(organ_dir):
    """Count .py files directly in the organ directory (not subdirs)."""
    return sum(1 for f in organ_dir.iterdir() if f.suffix == ".py" and f.is_file())


def run_selfcheck(organ_dir, entrypoint):
    """Run the organ's selfcheck op.

    Returns (success, output_dict, error).
    """
    try:
        result = subprocess.run(
            entrypoint,
            input=json.dumps({"op": "selfcheck"}),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(organ_dir),
        )
        if result.returncode != 0:
            return False, None, f"exit {result.returncode}: {result.stderr[:200]}"
        output = json.loads(result.stdout or "{}")
        if not isinstance(output, dict):
            return False, None, f"selfcheck returned non-dict: {str(output)[:100]}"
        return True, output, ""
    except subprocess.TimeoutExpired:
        return False, None, "timeout after 30s"
    except (json.JSONDecodeError, OSError) as exc:
        return False, None, f"{type(exc).__name__}: {exc}"[:200]


def check_evidence(organ_id, output):
    """Check that the selfcheck output contains evidence of the primary function.

    Returns (ok, detail_string).
    """
    if organ_id not in ORGAN_EVIDENCE:
        return False, (
            f"organ '{organ_id}' has no evidence requirement defined — "
            "add it to ORGAN_EVIDENCE or the selfcheck cannot be verified"
        )

    req = ORGAN_EVIDENCE[organ_id]
    evidence_keys = req["evidence_keys"]
    require_all = req.get("require_all", False)

    results = output.get("results")
    if not results or not isinstance(results, list):
        return False, (
            f"selfcheck returned no results list — "
            f"cannot verify {req['description']}"
        )

    for result in results:
        if not isinstance(result, dict):
            continue
        if require_all:
            if all(k in result and result[k] for k in evidence_keys):
                return True, (
                    f"found all evidence keys {evidence_keys} in "
                    f"result '{result.get('name', '?')}'"
                )
        else:
            for k in evidence_keys:
                if k in result and result[k]:
                    return True, (
                        f"found evidence key '{k}' in "
                        f"result '{result.get('name', '?')}'"
                    )

    return False, (
        f"no evidence of {req['description']} in any of {len(results)} "
        f"result(s) — looked for {evidence_keys}; the selfcheck may be "
        f"weakened to 'just runs' without testing core logic (FA-019)"
    )


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"score": 0.0, "detail": "invalid stdin JSON"}))
        return

    workdir = pathlib.Path(payload.get("workdir", ".")).resolve()
    organs = find_organs(workdir)

    if not organs:
        print(json.dumps({"score": 100.0, "detail": "no organs to check"}))
        return

    failures = []
    checked = 0
    exempt = []

    for organ_dir in organs:
        organ_id = organ_dir.name

        # Missing manifest: FAIL, not silently skip.
        manifest_path = organ_dir / "organ.json"
        if not manifest_path.exists():
            failures.append(f"organ '{organ_id}': missing organ.json")
            continue

        # Unparseable manifest: FAIL, not silently skip.
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"organ '{organ_id}': unparseable organ.json: {exc}")
            continue

        # Missing entrypoint: FAIL, not silently skip.
        entrypoint = manifest.get("entrypoint")
        if not isinstance(entrypoint, list) or not entrypoint:
            failures.append(f"organ '{organ_id}': missing or invalid entrypoint")
            continue

        # Single-file organs are exempt from selfcheck per the germline
        # protocol. They are noted here, not silently skipped.
        py_count = count_py_files(organ_dir)
        if py_count <= 1:
            exempt.append(f"'{organ_id}' (single-file, exempt)")
            continue

        # Multi-part organ: must have selfcheck with evidence.
        success, output, error = run_selfcheck(organ_dir, entrypoint)
        if not success:
            failures.append(f"organ '{organ_id}': selfcheck failed to run: {error}")
            continue

        if not output.get("ok"):
            failures.append(f"organ '{organ_id}': selfcheck returned ok=false")
            continue

        ok, detail = check_evidence(organ_id, output)
        if not ok:
            failures.append(f"organ '{organ_id}': {detail}")
            continue

        checked += 1

    if failures:
        score = 0.0
        detail = f"{len(failures)} failure(s): " + "; ".join(failures[:5])
        if exempt:
            detail += f" | exempt: {', '.join(exempt)}"
    else:
        score = 100.0
        detail = f"{checked} organ(s) verified"
        if exempt:
            detail += f"; exempt: {', '.join(exempt)}"

    print(json.dumps({"score": score, "detail": detail}))


if __name__ == "__main__":
    main()
