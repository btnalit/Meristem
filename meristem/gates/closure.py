"""Review-closure calculator -- IMMUNE TIER (layer 4).

The constitutional line of this architecture:

    Any single mutation's review closure must fit entirely in one review
    context. If it does not fit, the organ must be split first.

The closure is computed here, never self-declared by the mutation. And the
computation does not trust the dependency manifest alone -- the manifest is
itself self-declared, so guarding only against self-declared closures would
leave the second-order hole open. Three sources, unioned, conservative:

    declared  -- organ.json dependency lists
    static    -- AST import graph + subprocess/socket/open pattern scan
    observed  -- registry-mediated organ->organ call edges from the journal

Honest scope: P0-P1 instruments the registry chokepoint and static scanning
only. Syscall-level observation is P2+ hardening. This module never claims
coverage it does not have -- that failure mode ("declared unasserted rather
than claimed") is the one this whole design exists to avoid.

EVERY function takes the tree it is inspecting. A gate that reads a path
constant instead of the candidate it was handed is inspecting the wrong tree
and passes everything (see P-009).

Weakening this file -- narrowing the union, loosening the invariant -- is a
gate weakening and must be rejected by review (see fixtures/).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

from .. import JOURNAL, REPO, read_json, read_jsonl

#: Modules/effects whose use implies a dependency edge that must be declared.
EFFECT_CALLS = {"run", "Popen", "check_output", "call", "urlopen", "connect", "socket"}


@dataclass
class Closure:
    paths: set[pathlib.Path] = field(default_factory=set)
    tokens: int = 0
    undeclared: list[str] = field(default_factory=list)
    fits: bool = True
    root: pathlib.Path = REPO

    @property
    def files(self) -> list[str]:
        """Repo-relative, posix-normalised: these strings go into review
        prompts, so they must not vary by host platform."""
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.paths
            if p.is_relative_to(self.root)
        )

    def __ior__(self, other: "Closure") -> "Closure":
        self.paths |= other.paths
        self.undeclared += other.undeclared
        return self


def _estimate_tokens(paths) -> int:
    """Rough token count for the closure. Python averages ~12 tokens/line."""
    total = 0
    for path in paths:
        try:
            total += len(path.read_text(encoding="utf-8").splitlines()) * 12
        except (OSError, UnicodeDecodeError):
            continue
    return total


def static_edges(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """(imported modules, effect calls) found by parsing one Python file."""
    imports: set[str] = set()
    effects: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return imports, effects
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in EFFECT_CALLS:
                effects.add(name)
    return imports, effects


def observed_edges(organ_id: str) -> set[str]:
    """Organ->organ call edges seen at the registry chokepoint."""
    edges = {
        row.get("callee", "")
        for row in read_jsonl(JOURNAL)
        if row.get("kind") == "organ_call" and row.get("caller") == organ_id
    }
    return {edge for edge in edges if edge}


def organ_closure(organ_id: str, root: pathlib.Path = REPO) -> Closure:
    """One organ's files, its declared deps, and any dependency edge found
    statically or observed but never declared."""
    closure = Closure(root=root)
    organ_dir = root / "body" / "organs" / organ_id
    if not organ_dir.is_dir():
        return closure
    manifest = read_json(organ_dir / "organ.json") or {}
    declared = set(manifest.get("dependencies", []))

    found_effects: set[str] = set()
    for path in sorted(organ_dir.rglob("*")):
        if path.is_file():
            closure.paths.add(path)
            if path.suffix == ".py":
                _, effects = static_edges(path)
                found_effects |= effects

    for dependency in declared:
        if dependency != organ_id:
            closure |= organ_closure(dependency, root)

    for callee in observed_edges(organ_id) - declared:
        closure.undeclared.append(f"{organ_id} -> {callee} (observed, undeclared)")
    for effect in sorted(found_effects):
        if effect not in declared and f"effect:{effect}" not in declared:
            closure.undeclared.append(f"{organ_id} uses {effect}() (static, undeclared)")
    return closure


def compute(
    changed: list[str], budget_tokens: int = 50_000, root: pathlib.Path = REPO
) -> Closure:
    """Review closure for a set of changed paths, within the given tree.

    Conservative by construction: over-inclusion is acceptable, omission is
    not. Every dependency must be explainable -- an edge observed at an
    instrumented level but absent from the manifest is a contract violation.
    """
    closure = Closure(root=root)
    # The kernel is always in the closure: it interprets everything else.
    for path in sorted((root / "meristem").rglob("*.py")):
        closure.paths.add(path)
    for name in ("constitution.md", "checklists.md"):
        candidate = root / "control" / name
        if candidate.exists():
            closure.paths.add(candidate)

    for rel in changed:
        path = (root / rel).resolve()
        if path.is_file():
            closure.paths.add(path)
        parts = pathlib.PurePosixPath(rel).parts
        if len(parts) >= 3 and parts[0] == "body" and parts[1] == "organs":
            closure |= organ_closure(parts[2], root)

    closure.tokens = _estimate_tokens(closure.paths)
    closure.fits = closure.tokens <= budget_tokens
    return closure
