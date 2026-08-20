#!/usr/bin/env python3
"""Germline: the capability registry (bookkeeping half).

The seed is born knowing HOW to grow an organ, never WHICH organs to grow.
body/ ships empty; organs appear only when real work demands them.

Security note: this module is the bookkeeping half and lives at layer 1.
The VALIDATION half -- what counts as a legitimate organ -- is
admission-defining code and lives in meristem/gates/germline_validate.py at
the immune tier, so relaxing it trips the same monotonicity review as any
other gate weakening.
"""

from __future__ import annotations

import subprocess
import json
from dataclasses import dataclass

from . import BODY, JOURNAL, MeristemError, append_jsonl, read_json

LIFECYCLE = ("candidate", "calibrate", "register", "active", "deprecating", "archive")


@dataclass
class Organ:
    id: str
    version: str
    capability: str
    entrypoint: list[str]
    lifecycle: str
    dependencies: list[str]
    probes: list[str]
    path: object

    @property
    def is_active(self) -> bool:
        return self.lifecycle == "active"


def manifest_path(organ_id: str):
    return BODY / "organs" / organ_id / "organ.json"


def load(organ_id: str) -> Organ | None:
    data = read_json(manifest_path(organ_id))
    if not data:
        return None
    return Organ(
        id=data.get("id", organ_id),
        version=str(data.get("version", "0")),
        capability=data.get("capability", ""),
        entrypoint=list(data.get("entrypoint", [])),
        lifecycle=data.get("lifecycle", "candidate"),
        dependencies=list(data.get("dependencies", [])),
        probes=list(data.get("probes", [])),
        path=manifest_path(organ_id).parent,
    )


def registry() -> list[Organ]:
    """Every organ present in the body, in any lifecycle stage."""
    organs_dir = BODY / "organs"
    if not organs_dir.is_dir():
        return []
    found = [load(entry.name) for entry in sorted(organs_dir.iterdir()) if entry.is_dir()]
    return [organ for organ in found if organ is not None]


def advance(organ_id: str, stage: str) -> Organ:
    """Move an organ one step along the fixed lifecycle. Skipping is refused."""
    if stage not in LIFECYCLE:
        raise MeristemError(f"unknown lifecycle stage '{stage}'")
    organ = load(organ_id)
    if organ is None:
        raise MeristemError(f"organ '{organ_id}' has no manifest")
    current, target = LIFECYCLE.index(organ.lifecycle), LIFECYCLE.index(stage)
    if target != current + 1:
        raise MeristemError(
            f"organ '{organ_id}': {organ.lifecycle} -> {stage} skips the lifecycle"
        )
    path = manifest_path(organ_id)
    data = read_json(path) or {}
    data["lifecycle"] = stage
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    organ.lifecycle = stage
    return organ


def invoke(organ_id: str, payload: dict, *, caller: str = "kernel",
           timeout: int = 120, cycle: int = 0) -> dict:
    """Call an active organ over the fixed ABI: stdin JSON -> stdout JSON.

    Every call is an observed dependency edge (closure calculator reads these
    from the journal). The journal record captures the full execution outcome
    -- exit code, return value, error, and outcome classification -- so that
    silent organ failures are visible and the circuit breaker can distinguish
    failure modes rather than seeing only a boolean.
    """
    organ = load(organ_id)
    if organ is None or not organ.is_active:
        raise MeristemError(f"organ '{organ_id}' is not active")

    exit_code, outcome, result, error = None, "exception", {}, ""
    try:
        proc = subprocess.run(
            organ.entrypoint, input=json.dumps(payload),
            capture_output=True, text=True, timeout=timeout, cwd=str(organ.path),
        )
        exit_code = proc.returncode
        if proc.returncode != 0:
            outcome, error = "failure", proc.stderr[:400]
            raise MeristemError(f"organ '{organ_id}' exited {proc.returncode}: {proc.stderr[:400]}")
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            outcome, error = "bad_output", f"non-JSON stdout: {exc}"[:300]
            raise MeristemError(f"organ '{organ_id}' returned non-JSON on stdout") from exc
        outcome = "success"
    except subprocess.TimeoutExpired:
        outcome, error = "timeout", f"timed out after {timeout}s"
        raise
    except MeristemError:
        raise
    except Exception as exc:
        outcome, error = "exception", f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        append_jsonl(JOURNAL, {
            "kind": "organ_call", "caller": caller, "callee": organ_id,
            "cycle": cycle, "outcome": outcome, "success": outcome == "success",
            "exit_code": exit_code,
            "return_value": result if outcome == "success" else None,
            "error": error,
        })
    return result
