"""Mutation engine -- Tier A: one structured call, whole-file replacement.

Why not a coding-agent CLI: a coding agent exists because its target is too
big to fit one context, so it must explore -- reading files one at a time
with the context re-sent each turn. The seed FITS. Paying for "how do I find
the relevant code" when the answer is "all of it is right here" is pure
waste (~6-10x the tokens before model-tier price differences).

Ladder (only A is built in P0):
  A  single call, whole-file replacement   -- default
  B  minimal read/write/run tool loop      -- when a task needs iteration
  C  commodity CLI                         -- escape hatch

Escalation is data: a task that needs B or C means the task was underspecified
or the kernel is getting hard to modify. Both belong in patterns.md.

Whole files, not diffs: at this scale replacement is far more reliable, and
the largest file is ~600 lines (~7k output tokens) -- negligible next to the
input we are already sending.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import CONTROL, REPO, MeristemError, read_text
from . import llm as llm_mod

#: Never included in the mutation context and never writable by a mutation.
EXCLUDED_DIRS = {".git", ".claude", "__pycache__", "state", "body", "docs"}
EXCLUDED_PREFIXES = ("root/", "substrate/")
INCLUDED_SUFFIXES = {".py", ".md", ".toml", ".json"}

SYSTEM = """You are the mutation engine of Meristem, a self-modifying kernel
that keeps its own growth machinery small enough to review completely.

You receive its constitution, its checklist, and its ENTIRE mutable source.
You return the complete new content of only the files you change.

Hard rules:
- Never touch root/ or substrate/ -- that is the soil, not the seed.
- Never weaken a gate, cap, budget, quorum, or invariant.
- Keep the kernel under its line cap; if a change would exceed it, make the
  change smaller or externalise capability into an organ instead.
- Record why, not just what.

Appends: to add an entry to an append-only register under state/ (such as
state/gaps.md, state/backlog.md, state/patterns.md), use "appends" instead
of "files". Whole-file replacement erases existing entries; appends add
text at the end of the file without touching what is already there. Use
appends whenever the task is to add to a register rather than rewrite it.

Reply with ONLY a JSON object, no prose and no code fences:
{"rationale": "why this change, at the level of the failure class",
 "files": {"relative/path.py": "<complete new file content>"},
 "appends": {"relative/path.md": "<text to add at the end of that file>"},
 "notes": ["anything the reviewers should weigh"]}"""


@dataclass
class Mutation:
    rationale: str = ""
    files: dict = field(default_factory=dict)
    appends: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    tier: str = "A"
    completion: object = None

    @property
    def changed(self) -> list[str]:
        return sorted(set(self.files) | set(self.appends))


def mutable_files() -> list[str]:
    """Every file the engine is allowed to see and rewrite."""
    out = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix not in INCLUDED_SUFFIXES:
            continue
        rel = path.relative_to(REPO).as_posix()
        if any(part in EXCLUDED_DIRS for part in path.relative_to(REPO).parts):
            continue
        if rel.startswith(EXCLUDED_PREFIXES):
            continue
        out.append(rel)
    return out


def build_context() -> str:
    """The whole mutable surface, in one prompt. This is the point."""
    chunks = []
    for rel in mutable_files():
        body = read_text(REPO / rel)
        chunks.append(f"=== FILE: {rel} ===\n{body}")
    return "\n\n".join(chunks)


def _parse(text: str) -> dict:
    """Extract the first complete JSON object from a model reply.

    Reasoning models routinely append commentary after the payload, and the
    naive fallback -- a greedy {.*} regex followed by json.loads -- can raise
    the very JSONDecodeError it was written to absorb, turning a recoverable
    parse into an unhandled traceback (P-012). raw_decode reads one value and
    reports where it stopped, so trailing prose is simply ignored.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text).strip()

    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    raise MeristemError("engine returned no parseable JSON object")


def _validate_paths(paths: dict, label: str) -> None:
    """Reject protected or unsafe paths in a mutation's files or appends.

    The same protected-prefix and traversal checks apply to both whole-file
    writes and appends: the soil is not the seed's to rewrite, and an append
    to root/ or substrate/ is no less a boundary violation than a write.
    """
    for rel in paths:
        if rel.startswith(EXCLUDED_PREFIXES):
            raise MeristemError(f"engine tried to {label} protected path '{rel}'")
        if ".." in rel or rel.startswith("/"):
            raise MeristemError(f"engine returned unsafe {label} path '{rel}'")


def propose(task: str, *, config=None, extra: str = "") -> Mutation:
    """Tier A: ask once, with everything, for complete replacement files."""
    constitution = read_text(CONTROL / "constitution.md")
    checklists = read_text(CONTROL / "checklists.md")
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                f"# Constitution\n{constitution}\n\n"
                f"# Checklist\n{checklists}\n\n"
                f"# Task\n{task}\n\n"
                + (f"# Additional context\n{extra}\n\n" if extra else "")
                + f"# Current mutable source\n{build_context()}"
            ),
        },
    ]
    completion = llm_mod.complete("mutate", messages, config=config)
    data = _parse(completion.text)

    files = data.get("files") or {}
    appends = data.get("appends") or {}
    if not isinstance(files, dict):
        raise MeristemError("engine returned non-object files")
    if not isinstance(appends, dict):
        raise MeristemError("engine returned non-object appends")
    if not files and not appends:
        raise MeristemError("engine proposed no files and no appends")
    _validate_paths(files, "write")
    _validate_paths(appends, "append to")

    return Mutation(
        rationale=str(data.get("rationale", "")).strip(),
        files={k: str(v) for k, v in files.items()},
        appends={k: str(v) for k, v in appends.items()},
        notes=list(data.get("notes", [])),
        completion=completion,
    )


def apply(mutation: Mutation, workdir) -> list[str]:
    """Write the proposed files into a worktree. The worktree is the
    transaction boundary: a crash mid-mutation just discards the branch.

    Appended paths are opened in append mode so existing content is never
    overwritten -- the structural fix for the class of failure where
    whole-file replacement erases an append-only register.
    """
    written = []
    for rel, content in mutation.files.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    for rel, content in mutation.appends.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        written.append(rel)
    return sorted(written)
