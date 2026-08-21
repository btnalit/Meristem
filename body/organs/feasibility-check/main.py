#!/usr/bin/env python3
"""Feasibility check organ.

Estimates core and closure pressure impact of a proposed task before it
enters the agenda, rejecting tasks that would exceed the kernel line cap
or closure token budget. Reduces wasted cycles by catching infeasible
tasks before any model call is made.

Conservative by construction: over-estimating impact is acceptable (a false
rejection can be retried), but under-estimating is not (a false acceptance
wastes a cycle). This mirrors the closure calculator's philosophy.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

KERNEL_LOC_CAP = 3000
CLOSURE_TOKEN_CAP = 50_000
TOKENS_PER_LINE = 12


def _count_lines(path: pathlib.Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return 0


def _count_words(path: pathlib.Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").split())
    except (OSError, UnicodeDecodeError):
        return 0


def kernel_loc(repo: pathlib.Path) -> int:
    """Lines of code in meristem/**/*.py."""
    return sum(_count_lines(p) for p in sorted((repo / "meristem").rglob("*.py")))


def control_tokens(repo: pathlib.Path) -> int:
    """Estimated tokens in control/**/*.md (always in closure)."""
    return sum(_count_words(p) * 2 for p in sorted((repo / "control").rglob("*.md")))


def _files_referenced(task: str) -> set[str]:
    """Extract file and directory references from task text."""
    refs: set[str] = set()
    for m in re.finditer(r'(?:meristem|body|control|tests)/[\w/..-]+\.\w+', task):
        refs.add(m.group())
    for m in re.finditer(r'body/organs/([\w-]+)', task):
        refs.add(f"body/organs/{m.group(1)}")
    return refs


def _estimate_core_delta(task: str) -> int:
    """Estimate net change to kernel LOC from the task.

    Conservative: over-estimate additions, under-estimate relief.
    """
    lowered = task.lower()
    if re.search(r'\bexternaliz', lowered):
        return -50
    if re.search(r'\b(grow|growt|new organ|implement.*organ)\b', lowered):
        return 0
    if re.search(r'\b(repair|fix)\b', lowered):
        return 20
    if re.search(r'cap', lowered) and re.search(r'\b(raise|change|increase)\b', lowered):
        return 0
    return 20


def _estimate_closure_delta(repo: pathlib.Path, task: str) -> int:
    """Estimate additional closure tokens from files the task brings in
    that are NOT already in the base (kernel + control)."""
    extra = 0
    for ref in _files_referenced(task):
        path = repo / ref
        if path.is_file():
            if not ref.startswith("meristem/"):
                extra += _count_lines(path) * TOKENS_PER_LINE
        elif path.is_dir():
            for p in sorted(path.rglob("*")):
                if p.is_file() and p.suffix in ('.py', '.md', '.json', '.toml'):
                    extra += _count_lines(p) * TOKENS_PER_LINE

    organ_match = re.search(r'body/organs/([\w-]+)', task)
    if organ_match and re.search(r'\b(grow|growt|new organ|implement)\b', task, re.I):
        organ_dir = repo / "body" / "organs" / organ_match.group(1)
        if not organ_dir.exists():
            extra += 200 * TOKENS_PER_LINE

    return extra


def check(task: str, repo_path: str) -> dict:
    """Estimate feasibility of a task before it enters the agenda."""
    repo = pathlib.Path(repo_path).resolve()
    if not repo.is_dir():
        return {"feasible": False, "reason": f"repo_path not found: {repo}"}

    current_loc = kernel_loc(repo)
    core_delta = _estimate_core_delta(task)
    estimated_loc = current_loc + core_delta
    core_pressure = estimated_loc / KERNEL_LOC_CAP

    base_closure = current_loc * TOKENS_PER_LINE + control_tokens(repo)
    closure_delta = _estimate_closure_delta(repo, task)
    estimated_closure = base_closure + closure_delta
    closure_pressure = estimated_closure / CLOSURE_TOKEN_CAP

    feasible = True
    reasons: list[str] = []

    if estimated_loc > KERNEL_LOC_CAP:
        feasible = False
        reasons.append(f"estimated kernel LOC {estimated_loc} > cap {KERNEL_LOC_CAP}")

    if estimated_closure > CLOSURE_TOKEN_CAP:
        feasible = False
        reasons.append(f"estimated closure ~{estimated_closure} > cap {CLOSURE_TOKEN_CAP}")

    return {
        "feasible": feasible,
        "core_pressure": round(core_pressure, 4),
        "closure_pressure": round(closure_pressure, 4),
        "estimated_core_loc": estimated_loc,
        "estimated_closure_tokens": estimated_closure,
        "current_core_loc": current_loc,
        "core_delta": core_delta,
        "closure_delta": closure_delta,
        "reason": "; ".join(reasons) if reasons else "within caps",
    }


def selfcheck() -> dict:
    """Exercise the organ's core logic with tiny fixtures."""
    repo = pathlib.Path(__file__).resolve().parents[3]
    results: list = []
    failures: list[str] = []

    r = check("Fix a typo in meristem/loop.py", str(repo))
    results.append({"test": "simple_fix", "feasible": r["feasible"]})
    if "feasible" not in r:
        failures.append("check did not return a feasibility verdict")

    r = check("Externalize the report logic from meristem/loop.py", str(repo))
    results.append({"test": "externalize", "core_delta": r["core_delta"]})
    if r["core_delta"] >= 0:
        failures.append("externalize task did not show negative core delta")

    r = check("GROWTH: Implement a new organ at body/organs/test-organ/", str(repo))
    results.append({"test": "growth", "core_delta": r["core_delta"]})
    if r["core_delta"] != 0:
        failures.append("growth task showed non-zero core delta")

    r = check("Fix a bug", str(repo))
    required = ["feasible", "core_pressure", "closure_pressure",
                "estimated_core_loc", "estimated_closure_tokens", "reason"]
    missing = [f for f in required if f not in r]
    results.append({"test": "output_schema", "missing": missing})
    if missing:
        failures.append(f"output missing fields: {missing}")

    return {"ok": len(failures) == 0, "results": results, "failures": failures}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON: {exc}"}))
        sys.exit(1)

    op = payload.get("op", "check")

    if op == "selfcheck":
        print(json.dumps(selfcheck()))
        return

    if op == "check":
        task = payload.get("task", "")
        repo_path = payload.get("repo_path",
                                str(pathlib.Path(__file__).resolve().parents[3]))
        print(json.dumps(check(task, repo_path)))
        return

    print(json.dumps({"error": f"unknown op: {op}"}))
    sys.exit(1)


if __name__ == "__main__":
    main()
