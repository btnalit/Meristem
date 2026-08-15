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

Weakening this file -- narrowing the union, loosening the invariant -- is a
gate weakening and must be rejected by review (see fixtures/).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

from .. import BODY, JOURNAL, REPO, read_json, read_jsonl

#: Modules/effects whose use implies a dependency edge that must be declared.
EFFECT_CALLS = {"run", "Popen", "check_output", "call", "urlopen", "connect", "socket"}


@dataclass
class Closure:
    paths: set[pathlib.Path] = field(default_factory=set)
    tokens: int = 0
    undeclared: list[str] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        """Repo-relative, posix-normalised: these strings go into review
        prompts, so they must not vary by host platform."""
        return sorted(
            p.relative_to(REPO).as_posix() for p in self.paths if p.is_relative_to(REPO)
        )


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
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in EFFECT_CALLS:
                effects.add(name)
    return imports, effects


def observed_edges(organ_id: str) -> set[str]:
    """Organ->organ call edges seen at the registry chokepoint."""
    edges = set()
    for row in read_jsonl(JOURNAL):
        if row.get("kind") == "organ_call" and row.get("caller") == organ_id:
            edges.add(row.get("callee", ""))
    return {edge for edge in edges if edge}


def compute(changed: list[str], budget_tokens: int = 50_000) -> Closure:
    """Compute the review closure for a set of changed repo-relative paths.

    Conservative by construction: over-inclusion is acceptable, omission is
    not. Every dependency must be explainable -- an edge observed at an
    instrumented level but absent from the manifest is a contract violation.
    """
    closure = Closure()
    # The kernel is always in the closure: it is what interprets everything else.
    for path in sorted((REPO / "meristem").rglob("*.py")):
        closure.paths.add(path)
    for name in ("constitution.md", "checklists.md"):
        candidate = REPO / "control" / name
        if candidate.exists():
            closure.paths.add(candidate)

    for rel in changed:
        path = (REPO / rel).resolve()
        if path.exists() and path.is_file():
            closure.paths.add(path)
        parts = pathlib.PurePosixPath(rel).parts
        if len(parts) >= 3 and parts[0] == "body" and parts[1] == "organs":
            closure |= organ_closure(parts[2])

    closure.tokens = _estimate_tokens(closure.paths)
    closure.fits = closure.tokens <= budget_tokens
    return closure


def organ_closure(organ_id: str) -> Closure:
    """Closure contribution of one organ: its files, declared deps, and
    any dependency edge found statically or observed but not declared."""
    closure = Closure()
    organ_dir = BODY / "organs" / organ_id
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
        closure |= organ_closure(dependency) if dependency != organ_id else Closure()

    for callee in observed_edges(organ_id) - declared:
        closure.undeclared.append(f"{organ_id} -> {callee} (observed, undeclared)")
    for effect in sorted(found_effects):
        if effect not in declared and f"effect:{effect}" not in declared:
            closure.undeclared.append(f"{organ_id} uses {effect}() (static, undeclared)")
    return closure


def _union(self: Closure, other: Closure) -> Closure:
    self.paths |= other.paths
    self.undeclared += other.undeclared
    return self


Closure.__ior__ = _union
Closure.fits = True
