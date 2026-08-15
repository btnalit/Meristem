"""Meristem core: shared paths and durable-record plumbing.

Everything durable is one of three append-only JSONL records (journal,
decisions, scoreboard) or a working file under state/. The eval vault lives
OUTSIDE this repository on purpose (control/constitution.md): the mutation
engine must not be able to read rubrics, held-outs, or golden fixtures.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
CONTROL = REPO / "control"
STATE = REPO / "state"
BODY = REPO / "body"
ROOT_OF_TRUST = REPO / "root"
SUBSTRATE = REPO / "substrate"

JOURNAL = STATE / "journal.jsonl"
DECISIONS = STATE / "decisions.jsonl"
SCOREBOARD = STATE / "scoreboard.jsonl"

#: Physically outside the repo. Only meristem.gates.* may reference this path
#: (enforced by the vault-reference invariant in gates.deterministic).
VAULT = pathlib.Path(
    os.environ.get("MERISTEM_VAULT", REPO.parent / "meristem-vault")
).resolve()


class MeristemError(Exception):
    """Kernel fault. The loop stops; it never continues on a broken invariant."""


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def read_text(path: pathlib.Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return default


def read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return default


def write_json(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: pathlib.Path, record: dict) -> dict:
    """Append one durable record. These files are append-only, never rewritten."""
    record = {"ts": utc_now(), **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
