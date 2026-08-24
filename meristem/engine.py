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

from meristem import BODY_DIR, REPO, SEED_DIR, SEED_READONLY, SEED_WRITABLE
from meristem import llm

#: I10 prompt-face budget: build_context's token count must stay <=
#: PROMPT_BUDGET. SS10.1 mandates the mechanism but gives no number --
#: conservative P0-a placeholder pending real calibration.
PROMPT_BUDGET = 8000


class PromptOverBudget(Exception):
    """build_context exceeded PROMPT_BUDGET; refused before any model call."""


class PathViolation(Exception):
    """_validate_paths rejected a path outside the seed's writable whitelist."""


class ClosureViolation(Exception):
    """The mutation closure contains an unsafe filesystem entry."""


@dataclasses.dataclass(frozen=True)
class Mutation:
    task: str
    files: dict[str, str]  # relative path -> full new file content


def _estimate_tokens(text: str) -> int:
    """No third-party tokenizer (stdlib only): word count * 1.3, rounded up.
    Overestimating is the safe direction for a budget gate.
    """
    return int(len(text.split()) * 1.3) + 1


def _mutation_closure() -> tuple[list[tuple[str, str]], int]:
    """Read the current organ sources that a mutation may replace.

    The model is asked for whole-file replacements.  Showing only the writable
    path names gives it permission without the information needed to preserve
    or improve the current implementation.  The closure is deliberately
    discovered from the ``body/organs/`` writable prefix rather than from a
    classifier-specific name; a later organ must receive the same treatment.

    ``tests/`` is intentionally not part of the closure.  Tests are writable
    for the seed's repository mechanics, but they are not the capability body
    being measured and including them would let unrelated test text consume
    the model's comprehension surface.

    Symlinks are refused rather than followed.  A seed-controlled link under
    the writable body must never turn prompt construction into a read of a
    soil-private path.
    """
    organs_root = BODY_DIR / "organs"
    if not organs_root.exists():
        return [], 0
    if organs_root.is_symlink():
        raise ClosureViolation(f"mutation closure root is a symlink: {organs_root}")

    entries: list[tuple[str, str]] = []
    for path in sorted(organs_root.rglob("*")):
        if path.is_symlink():
            raise ClosureViolation(f"mutation closure contains a symlink: {path}")
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ClosureViolation(f"cannot read mutation closure file {path}: {exc}") from exc
        rel = path.relative_to(REPO).as_posix()
        entries.append((rel, content))

    closure_text = "\n\n".join(
        f"--- {rel} ---\n{content}" for rel, content in entries)
    return entries, _estimate_tokens(closure_text) if closure_text else 0


def build_context(task: str, *, config, extra: str = "") -> str:
    """Assemble the model prompt. Never touches a soil-private path.

    **可写面必须进 prompt。** 上一版只拼 task/extra/config —— 于是种子被要求
    产出整文件替换，**却不知道自己能写哪些文件**。它会去改白名单外的路径，
    `_validate_paths` 当场拒绝，整拍作废。那样测出来的是「模型能不能猜中白名单」，
    **不是 H1 要问的「能不能沿梯度爬」** —— 一个每拍都栽在路径上的实验，
    对假设本身没有产出任何证据。

    宪法（`seed/constitution.md`）在种子的可写面上，因此它是**种子自己的文档**：
    土壤只负责把它交到模型面前，不负责它写了什么。种子改了它，下一拍读到的就是改后的。

    **仍然不碰任何土壤私有路径**：这里读的两样东西（白名单常量、种子自己的宪法）
    都在种子的可见面内。隐藏用例库、台账、配额数字一律不在此列（§8.1.2 / §8.1.3）——
    CA-3 断言种子代码里连这些名字都不该出现，本 docstring 因此也避开它们。
    """
    closure, closure_tokens = _mutation_closure()
    parts = [
        "You are the seed's mutation engine. Reply with ONLY a JSON object "
        "mapping relative file path to the FULL new file content "
        "(whole-file replacement, not a diff).",
        "Writable paths (anything else is refused before any write, and the "
        "whole mutation is discarded): " + ", ".join(SEED_WRITABLE),
        "Read-only (never write these): " + ", ".join(SEED_READONLY),
        f"Task: {task}",
        # Keep the machine-readable budget visible to the model and to
        # operators reviewing a prompt.  ``fits`` is against the complete
        # prompt budget below; no second truncation or hidden closure cap is
        # introduced here.
        f'closure_budget: {{"files": {len(closure)}, '
        f'"tokens": {closure_tokens}, "fits": '
        f'{str(closure_tokens <= PROMPT_BUDGET).lower()}}}',
    ]
    if closure:
        parts.append("Current mutation closure (whole-file sources):\n" +
                     "\n\n".join(
                         f"--- {rel} ---\n{content}" for rel, content in closure))
    constitution = _read_constitution()
    if constitution:
        parts.append(constitution)
    if extra:
        parts.append(extra)
    if config:
        parts.append(f"Config: {config}")
    return "\n\n".join(parts)


def _read_constitution() -> str:
    """种子宪法。读不到就不读 —— **它缺席不该让一拍失败**。

    宪法是种子可写的：种子可以删掉它。那不是故障，是它的权限。
    真正不许缺席的是机制（路径校验、判决、台账），而那些一条也不依赖本文件。
    """
    try:
        text = (SEED_DIR / "constitution.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return ("Your constitution (you may rewrite it; the mechanisms it describes "
            f"are enforced regardless of what this file says):\n{text}")


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
