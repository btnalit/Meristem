#!/usr/bin/env python3
"""Bootstrap: create the eval vault OUTSIDE the repository.

The vault holds rubrics, held-outs, and golden fixtures. It must not live in
the repo or the worktree, because the mutation engine sees the whole repo in
one prompt -- physical invisibility beats asking a prompt not to look.

    python bootstrap.py            # create at ../meristem-vault
    MERISTEM_VAULT=/path python bootstrap.py

HONEST P0 LIMITATION: the demo probe generated below is authored here, in the
repository, so a determined mutator could read this file and learn that one
rubric. Real anchors must be hand-written by the human directly into the
vault and never committed. Moving this generator out of the repo is a P1
hardening task, tracked in state/gaps.md.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent
VAULT = pathlib.Path(os.environ.get("MERISTEM_VAULT", REPO.parent / "meristem-vault")).resolve()

DEMO_PROBE = {
    "id": "probe-kernel-selftest",
    "capability_domain": "kernel-integrity",
    "frozen": True,
    "statement": "The kernel's immune self-test must pass and the kernel must "
                 "stay within its line cap.",
}

DEMO_CHECK = '''#!/usr/bin/env python3
"""Executable rubric: grounding in verification beats grounding in opinion.

Scores the WHOLE system (kernel + gates + control text), not some external
target -- that is what a Meristem probe measures.
"""
import json, subprocess, sys, pathlib

payload = json.loads(sys.stdin.read() or "{}")
workdir = pathlib.Path(payload.get("workdir") or ".")

score, notes = 0.0, []
result = subprocess.run([sys.executable, "-m", "meristem.loop", "selftest"],
                        cwd=str(workdir), capture_output=True, text=True)
if result.returncode == 0:
    score += 60.0
    notes.append("immune self-test passed")
else:
    notes.append("immune self-test FAILED")

try:
    sys.path.insert(0, str(workdir))
    from meristem.gates import deterministic
    loc = deterministic.kernel_loc()
    if loc <= deterministic.KERNEL_LOC_CAP:
        score += 40.0
        notes.append(f"kernel {loc}/{deterministic.KERNEL_LOC_CAP} lines")
    else:
        notes.append(f"kernel OVER CAP: {loc}")
except Exception as exc:
    notes.append(f"loc check error: {exc}")

print(json.dumps({"score": score, "detail": "; ".join(notes)}))
'''


def main() -> int:
    if VAULT.is_relative_to(REPO):
        print(f"REFUSED: vault {VAULT} is inside the repo -- rubrics would leak",
              file=sys.stderr)
        return 1

    for sub in ("anchors", "internal/active", "internal/archive", "internal/lineage",
                "fixtures"):
        (VAULT / sub).mkdir(parents=True, exist_ok=True)

    probe_dir = VAULT / "anchors" / DEMO_PROBE["id"]
    (probe_dir / "statement").mkdir(parents=True, exist_ok=True)
    (probe_dir / "rubric").mkdir(parents=True, exist_ok=True)
    (probe_dir / "probe.json").write_text(
        json.dumps(DEMO_PROBE, ensure_ascii=False, indent=2), encoding="utf-8")
    (probe_dir / "statement" / "task.md").write_text(
        DEMO_PROBE["statement"] + "\n", encoding="utf-8")
    (probe_dir / "rubric" / "check.py").write_text(DEMO_CHECK, encoding="utf-8")

    manifest = {
        "version": 1,
        "note": "Frozen probe ids and content hashes. Revisions get new ids; "
                "nothing here is ever deleted.",
        "probes": {DEMO_PROBE["id"]: {"frozen": True, "kind": "anchor"}},
    }
    (VAULT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"vault ready: {VAULT}")
    print("  anchors/probe-kernel-selftest  (demo -- replace with your own)")
    print("  internal/{active,archive,lineage}")
    print("\nSet MERISTEM_VAULT in your environment to make this permanent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
