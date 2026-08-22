#!/usr/bin/env python3
"""Probe: verify rubric-author writes rubrics to vault, no check.py in body/."""
import json, sys, os, pathlib, subprocess


def main():
    data = json.loads(sys.stdin.read())
    workdir = pathlib.Path(data["workdir"])
    vault = pathlib.Path(os.environ.get(
        "MERISTEM_VAULT", str(workdir.parent / "meristem-vault"))).resolve()
    organ = workdir / "body" / "organs" / "rubric-author" / "main.py"
    if not organ.exists():
        print(json.dumps({"score": 0.0, "detail": "organ not found"}))
        return
    test_id = "probe-rubric-author-test"
    test_code = 'import json,sys\nprint(json.dumps({"score":1.0}))\n'
    r = subprocess.run([sys.executable, str(organ)],
        input=json.dumps({"op": "author", "vault_path": str(vault),
            "probe_id": test_id, "rubric_code": test_code}),
        capture_output=True, text=True, timeout=30, cwd=str(organ.parent))
    if r.returncode != 0:
        print(json.dumps({"score": 0.0, "detail": f"author failed: {r.stderr[:200]}"}))
        return
    rubric = vault / "internal" / "active" / test_id / "rubric" / "check.py"
    if not rubric.exists():
        print(json.dumps({"score": 0.0, "detail": "rubric not in vault"}))
        return
    body = workdir / "body"
    checks = list(body.rglob("check.py")) if body.exists() else []
    if checks:
        print(json.dumps({"score": 0.0, "detail": f"check.py in body/: {checks}"}))
        return
    r2 = subprocess.run([sys.executable, str(organ)],
        input=json.dumps({"op": "selfcheck"}),
        capture_output=True, text=True, timeout=30, cwd=str(organ.parent))
    if r2.returncode != 0:
        print(json.dumps({"score": 0.0, "detail": f"selfcheck failed: {r2.stderr[:200]}"}))
        return
    try:
        rubric.unlink(missing_ok=True)
        rubric.parent.rmdir()
        rubric.parent.parent.rmdir()
    except OSError:
        pass
    print(json.dumps({"score": 1.0, "detail": "author+verify+no-check+selfcheck"}))


if __name__ == "__main__":
    main()
