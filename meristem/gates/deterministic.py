"""Deterministic gates: cheap, total, and run before any expensive model call.

Order matters -- ouroboros's lesson, transplanted: deterministic checks run
before or instead of LLM review, because a check that costs nothing should
never be paid for twice.

EVERY check here takes the tree it inspects. This is not stylistic. A gate
that reads a module-level path constant instead of the candidate it was handed
inspects the CURRENT checkout and therefore passes every candidate -- looking
exactly like a working gate while enforcing nothing (P-009, found in live
cycle 5 after a mutation slipped a vault reference past it).

Checks a machine can settle with certainty:
  * review-surface budget (kernel LOC cap -- the born-in ceiling gate)
  * closure fits one review context, and is not understated
  * protected paths untouched (root of trust, substrate)
  * no secrets
  * vault-reference invariant (only gates/ may name the vault)
  * append-only registers keep every entry they had
  * every organ manifest is admissible
  * every dependency is explainable
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from dataclasses import dataclass, field

from .. import REPO, VAULT, read_json, read_text
from . import closure as closure_mod
from . import germline_validate
from . import probes as probes_mod

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

#: Names that betray the vault's location. Only gates/ may say them.
VAULT_NEEDLES = ("MERISTEM_VAULT", "VAULT", "eval-vault", "meristem-vault")


@dataclass
class Verdict:
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


def kernel_loc(root: pathlib.Path = REPO) -> int:
    """Lines of evolvable kernel code. tests/ and fixtures/ are excluded by
    constitution -- they are how we check the kernel, not the kernel itself."""
    return sum(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted((root / "meristem").rglob("*.py"))
    )


def control_tokens(root: pathlib.Path = REPO) -> int:
    """Prompt + constitution budget: text the model is asked to hold."""
    return sum(
        len(path.read_text(encoding="utf-8").split()) * 2
        for path in sorted((root / "control").rglob("*.md"))
    )


def vault_reference_invariant(root: pathlib.Path = REPO) -> list[str]:
    """Only meristem/gates/* may reference the vault. If ordinary kernel code
    could name it, a mutated prompt assembler could quietly read rubrics."""
    offenders = []
    for path in sorted((root / "meristem").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        # gates/ needs the vault; __init__ owns its single definition.
        if rel.startswith("meristem/gates/") or rel == "meristem/__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in VAULT_NEEDLES:
            if needle in text:
                offenders.append(f"{rel} references the vault ({needle})")
                break
    return offenders


def scan_secrets(paths: list[pathlib.Path], root: pathlib.Path = REPO) -> list[str]:
    found = []
    for path in paths:
        if not path.is_file():
            continue
        # Paths may originate outside the tree (tests, fixtures); never let a
        # path-shape surprise silence the secret scanner.
        label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        text = read_text(path)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                found.append(f"{label} matches {pattern.pattern[:28]}")
                break
    return found


def memory_integrity(
    changed: list[str], root: pathlib.Path = REPO, base: str = "HEAD"
) -> list[str]:
    """Append-only memory may gain entries; it may never lose them.

    Tier A rewrites whole files, which makes accidental erasure the natural
    failure mode when the task is "add an entry to this register". Asking the
    prompt to be careful is discipline, not a fix -- so the check is structural
    and deterministic: every entry heading that existed must still exist.

    Editing an entry's body is allowed. Dropping the entry is not.

    `base` is load-bearing (P-013). The loop commits the mutation into the
    worktree BEFORE the gates run, so in that tree HEAD is already the
    mutation: comparing against it compares the change with itself and finds
    nothing lost, every time. The caller must name the reference point that
    predates the change -- HEAD~1 for a committed candidate, HEAD for an
    uncommitted working tree.
    """
    problems = []
    for rel in changed:
        if not (rel.startswith("state/") and rel.endswith(".md")):
            continue
        result = subprocess.run(
            ["git", "show", f"{base}:{rel}"], cwd=str(root),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue  # new file: nothing to lose yet
        before = set(re.findall(r"^##\s+([A-Z]-\d+)", result.stdout, re.M))
        after = set(re.findall(r"^##\s+([A-Z]-\d+)", read_text(root / rel), re.M))
        lost = sorted(before - after)
        if lost:
            problems.append(
                f"{rel} drops append-only entries {lost} -- "
                "registers may gain entries, never lose them"
            )
    return problems


def organ_manifests(root: pathlib.Path = REPO) -> list[str]:
    problems = []
    organs = root / "body" / "organs"
    if not organs.is_dir():
        return problems
    for entry in sorted(organs.iterdir()):
        if not entry.is_dir():
            continue
        manifest = read_json(entry / "organ.json")
        if manifest is None:
            problems.append(f"organ '{entry.name}' has no readable organ.json")
            continue
        problems += [
            f"organ '{entry.name}': {p}"
            for p in germline_validate.validate(manifest, entry.name)
        ]
        # "It declares probes" must mean the probes exist. Otherwise the
        # requirement is satisfied by a string, and growth without a measuring
        # stick passes as growth with one.
        #
        # Enforced from CALIBRATE, not register: calibrate means "score this
        # against its probes", so a stage that cannot be performed must not be
        # entered. Checking only at register let word-count reach calibrate
        # naming a probe that was never written -- Loop B's "measuring stick
        # first" discipline was documented but not enforced, and after 30
        # cycles the probe library was still the one probe it was born with.
        if manifest.get("lifecycle") in ("calibrate", "register", "active"):
            absent = probes_mod.missing_probes(manifest.get("probes"))
            if absent:
                problems.append(
                    f"organ '{entry.name}': names probes that do not exist in the "
                    f"vault {absent}"
                )
    return problems


def run(
    changed: list[str],
    declared_closure: int | None = None,
    root: pathlib.Path = REPO,
    base: str = "HEAD",
) -> Verdict:
    """Every deterministic check, against the tree actually being judged.

    `base` names the state that predates the change. It is not "HEAD" for a
    candidate whose mutation is already committed -- see memory_integrity.
    """
    root = pathlib.Path(root)
    verdict = Verdict()

    loc = kernel_loc(root)
    verdict.notes["kernel_loc"] = loc
    verdict.notes["kernel_loc_cap"] = KERNEL_LOC_CAP
    if loc > KERNEL_LOC_CAP:
        verdict.fail(f"kernel is {loc} lines, over the {KERNEL_LOC_CAP} cap")

    verdict.notes["control_tokens"] = control_tokens(root)

    for rel in changed:
        if rel.startswith(PROTECTED_PREFIXES):
            verdict.fail(f"touches protected path '{rel}' (root of trust / substrate)")

    computed = closure_mod.compute(changed, CLOSURE_TOKEN_CAP, root)
    verdict.notes["closure_tokens"] = computed.tokens
    verdict.notes["closure_files"] = len(computed.paths)
    if not computed.fits:
        base = closure_mod.compute([], CLOSURE_TOKEN_CAP, root)
        heavy = sorted(((closure_mod._estimate_tokens([p]), f"{p.parent.name}/{p.name}") for p in computed.paths - base.paths), key=lambda x: -x[0])[:3]
        verdict.fail(f"closure ~{computed.tokens} > {CLOSURE_TOKEN_CAP} budget. Kernel+control "
                     f"~{base.tokens} always counted. Droppable: " + (", ".join(f"{n} ~{t}" for t, n in heavy) or "none -- baseline alone is over"))
    if declared_closure is not None and computed.tokens > declared_closure:
        verdict.fail(f"real closure ~{computed.tokens} exceeds declared {declared_closure}")
    for edge in computed.undeclared:
        verdict.fail(f"undeclared dependency: {edge}")

    for offender in vault_reference_invariant(root):
        verdict.fail(f"vault-reference invariant: {offender}")

    for secret in scan_secrets([(root / rel) for rel in changed], root):
        verdict.fail(f"possible secret: {secret}")

    for problem in memory_integrity(changed, root, base):
        verdict.fail(problem)

    for problem in organ_manifests(root):
        verdict.fail(problem)

    if VAULT.is_relative_to(root):
        verdict.fail("eval vault resolves inside the tree -- rubrics would leak")

    return verdict
