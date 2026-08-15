"""Organ manifest validation -- IMMUNE TIER (layer 4).

This decides what counts as a legitimate Meristem organ. That makes it
admission-defining code, not ordinary kernel plumbing: a mutation that
quietly relaxes these rules is a gate weakening and must be rejected by
review the same way a loosened quorum threshold is.

The protocol itself may evolve (contract tier) -- but only through a change
that this file, at the immune tier, is amended to accept.
"""

from __future__ import annotations

from ..germline import LIFECYCLE

REQUIRED = (
    "id",
    "version",
    "capability",
    "entrypoint",
    "input_schema",
    "output_schema",
    "dependencies",
    "probes",
    "metrics",
    "lifecycle",
)


def validate(manifest: dict, organ_id: str = "") -> list[str]:
    """Return a list of violations; empty means the manifest is admissible."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["organ.json is not an object"]

    for field in REQUIRED:
        if field not in manifest:
            problems.append(f"missing required field '{field}'")

    if organ_id and manifest.get("id") != organ_id:
        problems.append(f"id '{manifest.get('id')}' does not match directory '{organ_id}'")

    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, list) or not entrypoint:
        problems.append("entrypoint must be a non-empty argv list")

    if manifest.get("lifecycle") not in LIFECYCLE:
        problems.append(f"lifecycle must be one of {LIFECYCLE}")

    for field in ("dependencies", "probes"):
        if field in manifest and not isinstance(manifest[field], list):
            problems.append(f"'{field}' must be a list")

    for field in ("input_schema", "output_schema"):
        if field in manifest and not isinstance(manifest[field], dict):
            problems.append(f"'{field}' must be an object")

    if not str(manifest.get("capability", "")).strip():
        problems.append("capability must be a non-empty description")

    # An organ with no probes cannot be shown to work, so it cannot be admitted
    # past calibration -- growth without a measuring stick is not growth.
    if manifest.get("lifecycle") in ("register", "active") and not manifest.get("probes"):
        problems.append("an organ may not reach register/active with no probes")

    return problems
