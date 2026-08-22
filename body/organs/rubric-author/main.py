#!/usr/bin/env python3
"""Rubric author: writes probe rubrics into the vault.

Receives the vault path as input -- never references it by env var or
constant, preserving the vault-reference invariant. Ops: author, verify,
selfcheck.
"""
from __future__ import annotations
import json, sys, pathlib


def _rubric_path(vault_path, probe_id):
    if "/" in probe_id or "\\" in probe_id or ".." in probe_id:
        raise ValueError(f"unsafe probe_id: {probe_id}")
    return pathlib.Path(vault_path) / "internal" / "active" / probe_id / "rubric" / "check.py"


def op_author(args):
    vault = pathlib.Path(args["vault_path"]).resolve()
    repo = pathlib.Path(__file__).resolve().parents[3]
    if vault.is_relative_to(repo):
        return {"ok": False, "error": "vault is inside the repo -- rubrics would leak"}
    path = _rubric_path(args["vault_path"], args["probe_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["rubric_code"], encoding="utf-8")
    return {"ok": True, "path": str(path)}


def op_verify(args):
    path = _rubric_path(args["vault_path"], args["probe_id"])
    return {"ok": path.exists(), "path": str(path), "exists": path.exists()}


def op_selfcheck(args):
    import tempfile
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        r1 = op_author({"vault_path": tmp, "probe_id": "sc-test", "rubric_code": "# test\n"})
        results.append({"op": "author", "ok": r1["ok"]})
        r2 = op_verify({"vault_path": tmp, "probe_id": "sc-test"})
        results.append({"op": "verify", "ok": r2["ok"]})
    body = pathlib.Path(__file__).resolve().parents[2]
    found = [str(p) for p in body.rglob("check.py")]
    results.append({"op": "no_check_in_body", "ok": not found, "found": found})
    return {"ok": all(r["ok"] for r in results), "results": results}


def main():
    data = json.loads(sys.stdin.read())
    ops = {"author": op_author, "verify": op_verify, "selfcheck": op_selfcheck}
    handler = ops.get(data.get("op", ""))
    if not handler:
        print(json.dumps({"ok": False, "error": f"unknown op '{data.get('op')}'"}))
        sys.exit(1)
    try:
        result = handler(data)
        print(json.dumps(result))
        sys.exit(0 if result.get("ok") else 1)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:300]}))
        sys.exit(1)


if __name__ == "__main__":
    main()
