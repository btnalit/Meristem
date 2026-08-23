"""meristem.loop -- the spine + CLI entrypoint (SS9.1 / SS10.1).

`meristem/loop.py` must not be renamed, or supervisor.py's 6 call sites
need updating in lockstep.

CLI exit-code contract (SS9.1, verbatim compatibility required):
    selftest -> 0 = passed
    cycle    -> 0 = one candidate commit was produced; non-0 = no
                candidate or an exception. 0 no longer means "every gate
                passed" -- v5 moves measurement and verdict into the soil.
    reflect  -> same convention as cycle
    report   -> exit code not checked
    status   -> non-0 triggers a fault; stdout must match the status
                contract exactly
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import subprocess
import sys

from meristem import REPO, SEED_DIR, SEED_READONLY, SEED_WRITABLE
from meristem import engine
from meristem import narrative
from meristem.task import ReadOnlyFacts, take_task


@dataclasses.dataclass(frozen=True)
class Result:
    ok: bool
    commit: str | None = None
    reason: str | None = None


def _git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def run_cycle(task: str | None, cycle: int | None, *, workdir: pathlib.Path | None = None,
              config: dict | None = None) -> Result:
    """Take a task -> generate a change -> commit it in the worktree ->
    return the candidate. Does not measure, does not judge, does not write
    a score -- those belong to the soil. The seed writes no ledger of any
    kind: the soil derives its own cycle record from this process's exit
    code, the resulting commit, stdout, and filesystem state.
    """
    if not task:
        return Result(ok=False, reason="no_task")
    root = workdir if workdir is not None else REPO

    try:
        mutation = engine.propose(task, config=config or {})
    except engine.PromptOverBudget as exc:
        print(f"PROMPT_OVER_BUDGET {exc}", file=sys.stderr)
        return Result(ok=False, reason="prompt_over_budget")
    except Exception as exc:  # noqa: BLE001 -- any generation failure reads
        # as "no candidate" via the exit code; it is not a spine crash.
        print(f"PROPOSE_FAILED {exc}", file=sys.stderr)
        return Result(ok=False, reason="propose_failed")

    if not mutation.files:
        return Result(ok=False, reason="empty_mutation")

    try:
        changed = engine.apply(mutation, root)
    except engine.PathViolation as exc:
        print(f"PATH_VIOLATION {exc}", file=sys.stderr)
        return Result(ok=False, reason="path_violation")
    if not changed:
        return Result(ok=False, reason="no_files_written")

    _git(["add", *changed], cwd=root)
    commit = _git(
        ["-c", "user.email=seed@meristem.local", "-c", "user.name=meristem-seed",
         "commit", "-m",
         f"seed cycle {cycle if cycle is not None else 'unknown'}: {task[:72]}"],
        cwd=root,
    )
    if commit.returncode != 0:
        print(f"GIT_COMMIT_FAILED {commit.stderr}", file=sys.stderr)
        return Result(ok=False, reason="git_commit_failed")

    sha = _git(["rev-parse", "HEAD"], cwd=root).stdout.strip()
    return Result(ok=True, commit=sha or None)


def _cmd_cycle() -> int:
    facts = ReadOnlyFacts.load(SEED_DIR / "feedback.json")
    task = take_task(SEED_DIR / "agenda.md", facts)
    # 拍号归土壤（种子不持有自己的时间基准 —— 那是 S1 脉搏的一部分）。
    # 土壤尚未注入时**不要伪造一个 0**：每个 commit 都标成 "cycle 0" 是个静默的
    # 错误标签，而错误标签比没有标签更坏——它看起来像数据。
    # 未知就写 unknown，等 supervisor 接上（§13.3 的波次 2 改动）。
    raw = os.environ.get("MERISTEM_SOIL_CYCLE", "").strip()
    cycle = int(raw) if raw.isdigit() else None
    return 0 if run_cycle(task, cycle).ok else 1


def _cmd_reflect(pressure: bool) -> int:
    narrative.write_narrative(SEED_DIR)
    if pressure:
        facts = ReadOnlyFacts.load(SEED_DIR / "feedback.json")
        print(f"core pressure: {facts.core_pressure:.2f}")
    return 0


def _cmd_report() -> int:
    # REPORT.md rendering is soil/report_renderer.py's job (S8); the seed's
    # half is only to keep seed/narrative.md current.
    narrative.write_narrative(SEED_DIR)
    return 0


def _cmd_status() -> int:
    return narrative.print_status(REPO)


def _cmd_selftest() -> int:
    # Immune self-test: import sanity + boundary constants non-empty. No
    # writes, no network, no model call.
    assert SEED_WRITABLE and SEED_READONLY
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meristem.loop")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cycle")
    reflect_parser = sub.add_parser("reflect")
    reflect_parser.add_argument("--pressure", action="store_true")
    sub.add_parser("report")
    sub.add_parser("status")
    sub.add_parser("selftest")

    args = parser.parse_args(argv)
    if args.cmd == "reflect":
        return _cmd_reflect(args.pressure)
    dispatch = {"cycle": _cmd_cycle, "report": _cmd_report,
                "status": _cmd_status, "selftest": _cmd_selftest}
    return dispatch[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
