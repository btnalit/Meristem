#!/usr/bin/env python3
"""Bootstrap: create the eval vault OUTSIDE the repository.

The vault holds rubrics, held-outs, and golden fixtures. It must not live in
the repo or the worktree, because the mutation engine sees the whole repo in
one prompt -- physical invisibility beats asking a prompt not to look.

    python bootstrap.py            # create at ../meristem-vault
    MERISTEM_VAULT=/path python bootstrap.py

The vault ships EMPTY. The human writes anchor probes directly into the
vault -- never into the repository. The seed cannot author anchors: they are
human-owned held-outs by design (Principle 4). Internal probes are staged
through state/probe-proposals/ and promoted by the gates; see
control/probe-protocol.md.

Anchor probe layout in the vault:

  anchors/<probe-id>/
    probe.json           # metadata: id, capability_domain, frozen
    statement/task.md    # what the probe asks
    rubric/check.py       # how the probe scores (executable)
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent
VAULT = pathlib.Path(os.environ.get("MERISTEM_VAULT", REPO.parent / "meristem-vault")).resolve()


def main() -> int:
    if VAULT.is_relative_to(REPO):
        print(f"REFUSED: vault {VAULT} is inside the repo -- rubrics would leak",
              file=sys.stderr)
        return 1

    for sub in ("anchors", "internal/active", "internal/archive", "internal/lineage",
                "fixtures"):
        (VAULT / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": 1,
        "note": "Frozen probe ids and content hashes. Revisions get new ids; "
                "nothing here is ever deleted.",
        "probes": {},
    }
    (VAULT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"vault ready: {VAULT}")
    print("  anchors/         (empty -- write your anchor probes here)")
    print("  internal/{{active,archive,lineage}}")
    print("  fixtures/")
    print("\nWrite anchor probes directly into the vault:")
    print("  anchors/<probe-id>/")
    print("    probe.json     # metadata: id, capability_domain, frozen")
    print("    statement/task.md   # what the probe asks")
    print("    rubric/check.py     # how the probe scores (executable)")
    print("\nSet MERISTEM_VAULT in your environment to make this permanent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
