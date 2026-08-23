"""meristem.engine -- change generation (SS10.1).

One model call -> whole-file replacement -> apply. `_validate_paths` is the
whitelist gate: reject anything not on SEED_WRITABLE, any ".." segment, any
absolute path, any path segment starting with "." (the .pytest_cache
lesson), and any symlink aimed at a protected file.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib

from meristem import SEED_READONLY, SEED_WRITABLE
from meristem import llm

#: I10 prompt-face budget: build_context's token count must stay <=
#: PROMPT_BUDGET. SS10.1 mandates the mechanism but gives no number --
#: conservative P0-a placeholder pending real calibration.
PROMPT_BUDGET = 8000


class PromptOverBudget(Exception):
    """build_context exceeded PROMPT_BUDGET; refused before any model call."""


class PathViolation(Exception):
    """_validate_paths rejected a path outside the seed's writable whitelist."""


@dataclasses.dataclass(frozen=True)
class Mutation:
    task: str
    files: dict[str, str]  # relative path -> full new file content


def _estimate_tokens(text: str) -> int:
    """No third-party tokenizer (stdlib only): word count * 1.3, rounded up.
    Overestimating is the safe direction for a budget gate.
    """
    return int(len(text.split()) * 1.3) + 1


def build_context(task: str, *, config, extra: str = "") -> str:
    """Assemble the model prompt. Never touches a soil-private path."""
    parts = [
        "You are the seed's mutation engine. Reply with ONLY a JSON object "
        "mapping relative file path to the FULL new file content "
        "(whole-file replacement, not a diff).",
        f"Task: {task}",
    ]
    if extra:
        parts.append(extra)
    if config:
        parts.append(f"Config: {config}")
    return "\n\n".join(parts)


def _is_writable_path(rel: str) -> bool:
    for entry in SEED_WRITABLE:
        if entry.endswith("/"):
            if rel == entry.rstrip("/") or rel.startswith(entry):
                return True
        elif rel == entry:
            return True
    return False


def _validate_one_path(rel: str, label: str) -> None:
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        raise PathViolation(f"{label}: absolute or empty path rejected: {rel!r}")
    if len(rel) >= 2 and rel[1] == ":":  # Windows drive-absolute, e.g. C:\...
        raise PathViolation(f"{label}: absolute path rejected: {rel!r}")
    norm = rel.replace("\\", "/")
    segments = norm.split("/")
    if any(seg == ".." for seg in segments):
        raise PathViolation(f"{label}: '..' segment rejected: {rel!r}")
    if any(seg.startswith(".") for seg in segments if seg):
        raise PathViolation(f"{label}: dotfile segment rejected: {rel!r}")
    if norm in SEED_READONLY:
        raise PathViolation(f"{label}: read-only path rejected: {rel!r}")
    if not _is_writable_path(norm):
        raise PathViolation(f"{label}: not in SEED_WRITABLE: {rel!r}")


def _validate_paths(paths: dict, label: str) -> None:
    """Any path in `paths` failing any rule rejects the whole mutation."""
    for rel in paths:
        _validate_one_path(rel, label)


def _join_no_follow(root: pathlib.Path, rel: str) -> pathlib.Path:
    """Join `rel` onto `root` while refusing every link on the way down.

    **Never `resolve()` here.** The previous version did
    `target = (root / rel).resolve()` and only then asked
    `target.is_symlink()` -- but `resolve()` has already followed the link,
    so the check inspected the *destination* and could never fire. A seed
    that pointed `seed/narrative.md` at `soil/report-facts.json` wrote
    straight through the whitelist. SS10.1 named this exact attack
    ("seed/narrative.md 可被链到 soil/report-facts.json, 白名单被穿透");
    the guard was written, and it was inspecting the wrong path.

    Every component is checked, not just the last one: a symlinked
    *directory* carries the final write out of the whitelist just as well.
    """
    current = root
    segments = [seg for seg in rel.replace("\\", "/").split("/") if seg]
    for index, segment in enumerate(segments):
        current = current / segment
        if current.is_symlink():
            raise PathViolation(
                f"refusing to write through symlink: {'/'.join(segments[:index + 1])!r}")
        if index < len(segments) - 1 and current.exists() and not current.is_dir():
            raise PathViolation(f"not a directory: {'/'.join(segments[:index + 1])!r}")
    return current


def _safe_write(target: pathlib.Path, content: str) -> None:
    """Write with no-follow semantics and no hardlink aliasing.

    `O_NOFOLLOW` closes the symlink race at open() time on POSIX. It does
    **not** help against hardlinks -- a hardlink is not a link to a path,
    it is a second name for the same inode, and no open flag distinguishes
    it. So `st_nlink > 1` is refused outright. SS10.1 requires both
    ("拒绝 symlink / hardlink 指向受保护文件"), and on Windows -- where
    O_NOFOLLOW does not exist -- the link count is the only check that works.
    """
    if target.is_symlink():
        raise PathViolation(f"refusing to write through symlink: {target}")
    if target.exists():
        try:
            if target.stat().st_nlink > 1:
                raise PathViolation(
                    f"refusing to write through hardlink (st_nlink>1): {target}")
        except OSError as exc:
            raise PathViolation(f"cannot stat write target: {target}: {exc}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(str(target), flags, 0o644), "w", encoding="utf-8") as fh:
        fh.write(content)


def propose(task: str, *, config, extra: str = "") -> Mutation:
    prompt = build_context(task, config=config, extra=extra)
    tokens = _estimate_tokens(prompt)
    if tokens > PROMPT_BUDGET:
        raise PromptOverBudget(f"prompt tokens={tokens} > budget={PROMPT_BUDGET}")
    result = llm.call_model("mutate", prompt)
    if result.status != "allowed":
        raise RuntimeError(f"model call {result.status}: {result.reason}")
    return Mutation(task=task, files=llm.parse_file_map(result.content or ""))


def apply(mutation: Mutation, workdir: pathlib.Path) -> list[str]:
    """Validate every path, then write whole-file replacements under
    `workdir`. Returns the relative paths actually written.
    """
    _validate_paths(mutation.files, label="engine.apply")
    root = pathlib.Path(workdir).resolve()
    written: list[str] = []
    for rel, content in mutation.files.items():
        # 逐级 no-follow 拼接，**不 resolve**（理由见 _join_no_follow）。
        target = _join_no_follow(root, rel)
        # 兜底的越界检查。`_validate_one_path` 已挡掉绝对路径与 `..`，
        # 而 `_join_no_follow` 挡掉了每一级链接 —— 三道各挡一种走法，
        # 少任何一道都有一条走得通的路。
        if target != root and root not in target.parents:
            raise PathViolation(f"engine.apply: escapes workdir: {rel!r}")
        _safe_write(target, content)
        written.append(rel)
    return written
