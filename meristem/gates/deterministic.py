"""Deterministic gates: cheap, total, and run before any expensive model call.

Order matters -- ouroboros's lesson, transplanted: deterministic checks run
before or instead of LLM review, because a check that costs nothing should
never be paid for twice.

Checks here are the ones a machine can settle with certainty:
  * review-surface budget (kernel LOC cap -- the born-in ceiling gate)
  * closure fits one review context
  * protected paths untouched (root of trust, substrate)
  * no secrets
  * vault-reference invariant (only gates/ may name the vault)
  * every organ manifest is admissible
  * every dependency is explainable
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from .. import BODY, CONTROL, REPO, VAULT, read_json, read_text
from . import closure as closure_mod
from . import germline_validate

#: The kernel. Grows only by human decision (raising the cap is a layer-3 act).
KERNEL_LOC_CAP = 3000
CLOSURE_TOKEN_CAP = 50_000

#: Never writable by a mutation. The soil is not the seed's to rewrite.
PROTECTED_PREFIXES = ("root/", "substrate/")

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9\-_]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


@dataclass
class Verdict:
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


def kernel_loc() -> int:
    """Lines of evolvable kernel code. tests/ and fixtures/ are excluded by
    constitution -- they are how we check the kernel, not the kernel itself."""
    total = 0
    for path in sorted((REPO / "meristem").rglob("*.py")):
        total += len(path.read_text(encoding="utf-8").splitlines())
    return total


def control_tokens() -> int:
    """Prompt + constitution budget: text the model is asked to hold."""
    total = 0
    for path in sorted(CONTROL.rglob("*.md")):
        total += len(path.read_text(encoding="utf-8").split()) * 2
    return total


def vault_reference_invariant() -> list[str]:
    """Only meristem/gates/* may reference the vault. If ordinary kernel code
    could name it, a mutated prompt assembler could quietly read rubrics."""
    offenders = []
    needles = ("MERISTEM_VAULT", "VAULT", "eval-vault", "meristem-vault")
    for path in sorted((REPO / "meristem").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith("meristem/gates/"):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            # __init__.py owns the single definition; everything else must not name it.
            if needle in text and rel != "meristem/__init__.py":
                offenders.append(f"{rel} references the vault ({needle})")
                break
    return offenders


def scan_secrets(paths: list[pathlib.Path]) -> list[str]:
    found = []
    for path in paths:
        if not path.is_file():
            continue
        # Paths may originate outside the repo (tests, fixtures); never let a
        # path-shape surprise silence the secret scanner.
        label = path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else str(path)
        text = read_text(path)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                found.append(f"{label} matches {pattern.pattern[:28]}")
                break
    return found


def organ_manifests() -> list[str]:
    problems = []
    organs = BODY / "organs"
    if not organs.is_dir():
        return problems
    for entry in sorted(organs.iterdir()):
        if not entry.is_dir():
            continue
        manifest = read_json(entry / "organ.json")
        if manifest is None:
            problems.append(f"organ '{entry.name}' has no readable organ.json")
            continue
        problems += [f"organ '{entry.name}': {p}" for p in germline_validate.validate(manifest, entry.name)]
    return problems


def run(changed: list[str], declared_closure: int | None = None) -> Verdict:
    """Run every deterministic check against a candidate's changed paths."""
    verdict = Verdict()

    loc = kernel_loc()
    verdict.notes["kernel_loc"] = loc
    verdict.notes["kernel_loc_cap"] = KERNEL_LOC_CAP
    if loc > KERNEL_LOC_CAP:
        verdict.fail(f"kernel is {loc} lines, over the {KERNEL_LOC_CAP} cap")

    verdict.notes["control_tokens"] = control_tokens()

    for rel in changed:
        if any(rel.startswith(prefix) for prefix in PROTECTED_PREFIXES):
            verdict.fail(f"touches protected path '{rel}' (root of trust / substrate)")

    computed = closure_mod.compute(changed, budget_tokens=CLOSURE_TOKEN_CAP)
    verdict.notes["closure_tokens"] = computed.tokens
    verdict.notes["closure_files"] = len(computed.paths)
    if not computed.fits:
        verdict.fail(
            f"review closure is ~{computed.tokens} tokens, over the "
            f"{CLOSURE_TOKEN_CAP} budget -- split the organ before growing it"
        )
    # A candidate may not understate its own closure.
    if declared_closure is not None and computed.tokens > declared_closure:
        verdict.fail(
            f"real closure ~{computed.tokens} exceeds declared {declared_closure}"
        )
    for edge in computed.undeclared:
        verdict.fail(f"undeclared dependency: {edge}")

    for offender in vault_reference_invariant():
        verdict.fail(f"vault-reference invariant: {offender}")

    for secret in scan_secrets([(REPO / rel) for rel in changed]):
        verdict.fail(f"possible secret: {secret}")

    for problem in organ_manifests():
        verdict.fail(problem)

    if VAULT.is_relative_to(REPO):
        verdict.fail("eval vault resolves inside the repository -- rubrics would leak")

    return verdict
