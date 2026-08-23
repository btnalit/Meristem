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


#: Windows 的 `FILE_ATTRIBUTE_REPARSE_POINT`。junction（`mklink /J`）与 symlink
#: 都是 reparse point，但 **`Path.is_symlink()` 只认 `IO_REPARSE_TAG_SYMLINK`**，
#: 不认 junction 的 `IO_REPARSE_TAG_MOUNT_POINT` —— 而 junction **不需要任何特权**
#: 就能创建（真 symlink 在 Windows 上要管理员或开发者模式）。
#: 2026-08-23 对抗性审查用它绕过了本模块上一版的逐级检查：把 `seed/probe-proposals/`
#: 换成指向 `soil/` 的 junction，写入直接落在土壤私有面上。
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_link(path: pathlib.Path) -> bool:
    """这一级是不是链接。**不能只问 `is_symlink()`**（见上面的常量注释）。

    统一按「任何 reparse point 一律拒绝」判定，而不是按标签逐个列举：
    列举式的边界，每出现一种新标签就漏一次 —— 这份规格反复在说的同一件事。
    """
    if path.is_symlink():
        return True
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        # AttributeError：非 Windows 平台没有 st_file_attributes，symlink 已由上面覆盖。
        # OSError：这一级还不存在 —— 不存在就不是链接。
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


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
        if _is_link(current):
            raise PathViolation(
                f"refusing to write through link: {'/'.join(segments[:index + 1])!r}")
        if index < len(segments) - 1 and current.exists() and not current.is_dir():
            raise PathViolation(f"not a directory: {'/'.join(segments[:index + 1])!r}")
    return current


def _safe_write(target: pathlib.Path, content: str) -> None:
    """Write with no-follow semantics, no hardlink aliasing, and no
    check-then-open race.

    Three distinct attacks, three distinct defences -- none of them covers
    another:

    * **symlink / junction**: `O_NOFOLLOW` refuses at open() time on POSIX.
      Windows has no `O_NOFOLLOW` at all, so the pre-open `_is_link()` check
      is the only static guard there.
    * **hardlink**: a hardlink is not a link *to a path*, it is a second name
      for the same inode, and **no open flag distinguishes it** -- `st_nlink`
      is the only signal. SS10.1 names both ("拒绝 symlink / hardlink").
    * **TOCTOU**: on Windows the two checks above are not atomic with the
      open, and an attacker who swaps the path for a link in between wins.
      A 2026-08-23 adversarial review won that race **on the first attempt**.
      So the file is opened WITHOUT `O_TRUNC`, the opened descriptor's
      identity is compared against what was checked, and only then is it
      truncated. Opening no longer destroys anything before we have proved
      we opened the file we inspected.

    **Residual, stated rather than papered over** (the previous version of
    this docstring quietly dropped the TOCTOU caveat it inherited while the
    race was still live -- that is the failure mode this project calls
    "declared but unasserted", committed in a comment): the identity check
    rests on `st_dev`/`st_ino` being meaningful. Where a filesystem reports
    zeros for both, it degrades to the pre-open checks alone.
    """
    if _is_link(target):
        raise PathViolation(f"refusing to write through link: {target}")

    existed = target.exists()
    before = None
    if existed:
        try:
            before = os.stat(target, follow_symlinks=False)
        except OSError as exc:
            raise PathViolation(f"cannot stat write target: {target}: {exc}") from exc
        if before.st_nlink > 1:
            raise PathViolation(
                f"refusing to write through hardlink (st_nlink>1): {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if not existed:
        # 目标本不存在 -> 必须由我们创建。O_EXCL 让「有人抢先把链接放进来」
        # 变成 open 失败，而不是一次跟随写入。
        flags |= os.O_EXCL

    fd = os.open(str(target), flags, 0o644)
    try:
        if before is not None:
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise PathViolation(
                    f"write target changed identity between check and open: {target}")
            if after.st_nlink > 1:
                raise PathViolation(
                    f"refusing to write through hardlink (st_nlink>1): {target}")
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None  # fdopen 接管，交给 with 关闭
            fh.write(content)
    finally:
        if fd is not None:
            os.close(fd)


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
