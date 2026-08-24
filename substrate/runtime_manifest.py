"""Fail-closed validation of the soil runtime manifest."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class RuntimeManifestError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(repo: Path, *, task_id: str) -> dict:
    repo = Path(repo)
    manifest_path = repo / "soil" / "runtime-manifest.json"
    ledger = repo / "state" / "soil-ledger.jsonl"
    feedback = repo / "seed" / "feedback.json"
    report = repo / "soil" / "report-facts.json"
    registry = repo / "soil" / "frozen-probe-registry.json"
    required = (manifest_path, ledger, feedback, report, registry)
    if any(not path.is_file() for path in required):
        raise RuntimeManifestError("runtime manifest or required projection is missing")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeManifestError("runtime manifest is invalid JSON") from exc
    if data.get("schema_version") != 1 or data.get("task_id") != task_id:
        raise RuntimeManifestError("runtime manifest schema/task identity mismatch")
    if data.get("ownership", {}).get("owner") != "soil" or data.get("ownership", {}).get("group") != "soil":
        raise RuntimeManifestError("runtime manifest ownership is not soil-owned")
    if data.get("fail_closed") != {"manifest_mismatch": True, "missing_projection": True}:
        raise RuntimeManifestError("runtime manifest fail_closed contract is invalid")
    if data.get("ledger_tail_hash") != _sha(ledger):
        raise RuntimeManifestError("runtime manifest ledger tail is stale")
    expected = data.get("projection_hashes", {})
    actual = {"seed_feedback": _sha(feedback), "report_facts": _sha(report),
              "frozen_probe_registry": _sha(registry)}
    if expected != actual:
        raise RuntimeManifestError("runtime manifest projection hashes are stale")
    for path, mode in ((feedback, 0o644), (report, 0o600),
                       (registry, 0o644), (manifest_path, 0o600)):
        if (os.stat(path).st_mode & 0o777) != mode:
            raise RuntimeManifestError(f"unexpected permissions: {path}")
    expected_modes = {
        "seed/feedback.json": "0644",
        "soil/report-facts.json": "0600",
        "soil/frozen-probe-registry.json": "0644",
    }
    if data.get("ownership", {}).get("modes") != expected_modes:
        raise RuntimeManifestError("runtime manifest ownership modes mismatch")
    return data
