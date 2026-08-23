"""meristem -- the seed's spine (v5 P0-a).

Path constants and read-only helpers. The seed determines its own
read/write boundary only through the constants below; their authority
lives in docs/MERISTEM-V5-SPEC.md SS10.1 / SS16, not in this file.
"""
from __future__ import annotations

import json
import pathlib

REPO: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
SEED_DIR: pathlib.Path = REPO / "seed"
BODY_DIR: pathlib.Path = REPO / "body"
TESTS_DIR: pathlib.Path = REPO / "tests"

#: Seed-writable files/prefixes (SS10.1). "/"-suffixed entries are directory
#: prefixes. Whitelist, not blacklist: anything unlisted is refused.
SEED_WRITABLE: tuple[str, ...] = (
    "seed/constitution.md",
    "seed/agenda.md",
    "seed/narrative.md",
    "seed/probe-proposals/",
    "body/organs/",
    "tests/",
)

#: Seed-readable-only, never writable (SS10.1: model-policy boundary /
#: projection-ownership rule).
SEED_READONLY: tuple[str, ...] = (
    "seed/model-interface.json",
    "seed/feedback.json",
)


def read_json_readonly(path: pathlib.Path) -> dict | None:
    """Read a JSON projection without ever writing it. Absent/malformed ->
    None; callers treat that as "no facts yet", never as a spine failure.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
