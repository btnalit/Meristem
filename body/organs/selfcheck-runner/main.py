#!/usr/bin/env python3
"""Selfcheck runner organ.

Discovers all multi-part organs in body/organs/, invokes each one's
selfcheck op, and reports pass/fail per organ. This is the runner half
of G-SELFCHECK; the validator half (enforcing selfcheck support in
multi-part organs) lives in the germline validator at the immune tier.

Single-file organ: the entry point IS the whole surface, so it is
exempt from the selfcheck requirement. It still supports the op for
robustness and uniformity.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ORGANS_DIR = pathlib.Path(__file__).resolve().parent.parent


def discover_multipart_organs() -> list[str]:
    """Find all multi-part organs (more than one .py source file)."""
    found: list[str] = []
    if not ORGANS_DIR.is_dir():
        return found
    for entry in sorted(ORGANS_DIR.iterdir()):
        if not entry.is_dir() or entry.name == "selfcheck-runner":
            continue
        manifest = entry / "organ.json"
        if not manifest.is_file():
            continue
        py_files = [
            p for p in entry.rglob("*.py")
            if p.is_file() and "__pycache__" not in p.parts
        ]
        if len(py_files) > 1:
            found.append(entry.name)
    return found


def run_selfcheck(organ_id: str) -> dict:
    """Invoke one organ's selfcheck op via its declared entrypoint."""
    organ_dir = ORGANS_DIR / organ_id
    manifest_path = organ_dir / "organ.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"organ": organ_id, "ok": False, "error": f"bad manifest: {exc}"}

    entrypoint = manifest.get("entrypoint", [])
    if not isinstance(entrypoint, list) or not entrypoint:
        return {"organ": organ_id, "ok": False, "error": "no entrypoint in manifest"}

    try:
        result = subprocess.run(
            entrypoint,
            input=json.dumps({"op": "selfcheck"}),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(organ_dir),
        )
    except subprocess.SubprocessError as exc:
        return {"organ": organ_id, "ok": False, "error": str(exc)[:300]}
    except Exception as exc:
        return {"organ": organ_id, "ok": False, "error": str(exc)[:300]}

    if result.returncode != 0:
        return {
            "organ": organ_id,
            "ok": False,
            "error": f"exit {result.returncode}: {result.stderr[:300]}",
        }
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "organ": organ_id,
            "ok": False,
            "error": f"non-JSON stdout: {result.stdout[:200]}",
        }
    return {
        "organ": organ_id,
        "ok": bool(data.get("ok", False)),
        "results": data.get("results", []),
        "failures": data.get("failures", []),
    }


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    op = payload.get("op", "run")

    if op == "selfcheck":
        # Single-file organ: the entry point IS the whole surface.
        # Verify discovery logic executes without error.
        organs = discover_multipart_organs()
        print(json.dumps({
            "ok": True,
            "results": [f"discovered {len(organs)} multi-part organ(s): {organs}"],
        }))
        return

    organs = discover_multipart_organs()
    results = [run_selfcheck(oid) for oid in organs]
    all_ok = all(r.get("ok") for r in results) if results else True
    failures = [r["organ"] for r in results if not r.get("ok")]
    output: dict = {"ok": all_ok, "results": results}
    if failures:
        output["failures"] = failures
    print(json.dumps(output))


if __name__ == "__main__":
    main()
