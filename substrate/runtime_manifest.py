"""Fail-closed validation and refresh of the soil runtime manifest."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import grp
import tempfile
from pathlib import Path

from substrate import feedback_projection
from substrate import soil_state


class RuntimeManifestError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paths(repo: Path) -> tuple[Path, Path, Path, Path, Path]:
    return (repo / "soil" / "runtime-manifest.json",
            repo / "state" / "soil-ledger.jsonl",
            repo / "seed" / "feedback.json",
            repo / "soil" / "report-facts.json",
            repo / "soil" / "frozen-probe-registry.json")


def refresh(repo: Path, *, task_id: str) -> dict:
    repo = Path(repo)
    manifest_path, ledger, feedback, report, registry = _paths(repo)
    required = (manifest_path, ledger, feedback, report, registry)
    if any(not path.is_file() for path in required):
        raise RuntimeManifestError("runtime manifest or required projection is missing")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeManifestError("runtime manifest is invalid JSON") from exc
    if data.get("schema_version") != 1:
        raise RuntimeManifestError("runtime manifest schema mismatch")
    data["task_id"] = task_id
    data["ledger_tail_hash"] = _sha(ledger)
    data["projection_hashes"] = {
        "seed_feedback": _sha(feedback),
        "report_facts": _sha(report),
        "frozen_probe_registry": _sha(registry),
    }
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, name = tempfile.mkstemp(prefix=".runtime-manifest.", dir=str(manifest_path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(name, pwd.getpwnam("soil").pw_uid, grp.getgrnam("soil").gr_gid)
        os.chmod(name, 0o600)
        os.replace(name, manifest_path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
    return data


def verify(repo: Path, *, task_id: str) -> dict:
    repo = Path(repo)
    manifest_path, ledger, feedback, report, registry = _paths(repo)
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


def bootstrap(repo: Path, *, task_id: str) -> dict:
    """Create the minimal state a fresh checkout is missing so the runtime
    manifest gate can pass for the first time (P1-4).

    Nothing today creates `soil/runtime-manifest.json`, and both `refresh()`
    and `verify()` require it -- and the rest of the chain -- to already
    exist. Traced dependency chain and what fills each gap:

      - `state/soil-ledger.jsonl`: no writer creates it empty; an
        append-only ledger with zero rows is a legitimate starting state,
        created here if absent.
      - `seed/feedback.json` / `soil/report-facts.json`: produced by
        `feedback_projection.write_projection()` from whatever the ledger
        holds -- an empty ledger derives an all-defaults projection, which
        is exactly what a fresh checkout should read.
      - `soil/frozen-probe-registry.json`: `FrozenProbeRegistry.read()`
        already treats a missing file as an empty registry (`{}`), but
        `refresh()`/`verify()` require the file itself to exist -- this
        writes that same `{}` shape through the registry's own atomic
        writer, so there is one writer for the file's shape, not a second
        one that can drift from it.
      - `soil/runtime-manifest.json`: written here as the minimal valid
        skeleton (`schema_version`, `ownership`, `fail_closed`); `refresh()`
        then fills in `task_id` / `ledger_tail_hash` / `projection_hashes`.

    Refuses outright if a manifest already exists -- silently rebuilding it
    would defeat the fail-closed contract this module exists for.
    """
    repo = Path(repo)
    manifest_path, ledger, feedback, report, registry = _paths(repo)
    if manifest_path.exists():
        raise RuntimeManifestError(
            f"runtime manifest already exists, refusing to bootstrap over it: {manifest_path}")

    ledger.parent.mkdir(parents=True, exist_ok=True)
    if not ledger.is_file():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(ledger), flags, 0o600)
        os.close(fd)
        os.chown(ledger, pwd.getpwnam("soil").pw_uid, grp.getgrnam("soil").gr_gid)

    feedback_projection.write_projection(repo, task_id=task_id)

    if not registry.is_file():
        soil_state.FrozenProbeRegistry(registry)._write_all({})

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    skeleton = {
        "schema_version": 1,
        "ownership": {
            "owner": "soil", "group": "soil",
            "modes": {
                "seed/feedback.json": "0644",
                "soil/report-facts.json": "0600",
                "soil/frozen-probe-registry.json": "0644",
            },
        },
        "fail_closed": {"manifest_mismatch": True, "missing_projection": True},
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(manifest_path), flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeManifestError(
            f"runtime manifest already exists, refusing to bootstrap over it: {manifest_path}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(skeleton, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    refresh(repo, task_id=task_id)
    return verify(repo, task_id=task_id)
