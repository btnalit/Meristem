"""ROOT OF TRUST -- panic. Never evolvable. Human is the only writer.

Deliberately placed outside the principle hierarchy: no agent code, tool,
prompt, or constitutional argument may prevent, delay, or circumvent panic.

Panic is not a restart. Panic is a full stop. Only a human clears the latch.

The latch file lives outside the repository so that a mutation of the
repository cannot remove it.

PLATFORM NOTE (P0, honest): on POSIX this kills the process group. On
Windows it falls back to `taskkill /F /T`. The Unix file-ownership isolation
of the latch (operator-owned, seed read-only) is NOT available on Windows --
that is a documented P1 hardening gap, not a solved problem.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

class ControlPathMissing(RuntimeError):
    """`MERISTEM_CONTROL` 未设置。**急停开关拒绝在猜出来的路径上工作。**"""


def _control_dir() -> pathlib.Path:
    """闩所在目录。**只从 `MERISTEM_CONTROL` 读；读不到就拒绝，不猜。**

    这里原本是 `os.environ.get("MERISTEM_CONTROL", ~/.meristem-control)`。
    2026-08-24 那个缺省造成了一次**谎报成功**：操作员在没有 source 环境文件的 shell 里
    跑 `panic.py clear`，闩的真实路径是 `/RSI/meristem-control/PANIC`，
    而缺省把它指到了根本不存在的 `~/.meristem-control/PANIC`，
    `unlink(missing_ok=True)` 什么也没做，**然后照样打印了「panic latch cleared」**。
    闩还在，人以为清掉了。

    **反方向更致命**：`engage()` 会 `mkdir` 并把闩写进那个没人检查的目录 ——
    **你以为按下了急停，系统照跑**。一个会谎报成功的急停开关，比没有急停开关更坏，
    因为它会让人停止寻找别的停法。

    这与 vault 的 C-65 是同一个病（缺省失败时不报错，只是指到错的地方），
    只是长在了信任根里。**信任根尤其不允许「猜一个路径」这种动作。**
    """
    raw = os.environ.get("MERISTEM_CONTROL", "").strip()
    if not raw:
        raise ControlPathMissing(
            "MERISTEM_CONTROL 未设置：panic 闩必须显式给出路径，本模块不提供缺省。\n"
            "  服务器上它在 /RSI/meristem-env 里：\n"
            "    set -a && . /RSI/meristem-env && set +a")
    return pathlib.Path(raw)


def latch_path() -> pathlib.Path:
    return _control_dir() / "PANIC"


def engaged() -> bool:
    return latch_path().exists()


def engage(reason: str = "manual") -> None:
    """上闩，**并核实它真的落地了**。"""
    latch = latch_path()
    latch.parent.mkdir(parents=True, exist_ok=True)
    latch.write_text(f"{reason}\n", encoding="utf-8")
    if not latch.exists():
        raise RuntimeError(f"闩没有落地：{latch}")


def clear() -> None:
    """Only a human runs this. Nothing in the seed may call it.

    **清完必须核实。** 旧版直接 `unlink(missing_ok=True)` 然后由调用方打印
    「cleared」—— 于是「路径指错了、什么也没清」与「确实清掉了」打印出来一模一样。
    急停开关的每一个动作都必须能验证，否则它只是一句话。
    """
    latch = latch_path()
    existed = latch.exists()
    latch.unlink(missing_ok=True)
    if latch.exists():
        raise RuntimeError(f"闩清不掉：{latch}")
    return existed


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    action = argv[0] if argv else "engage"
    try:
        latch = latch_path()
    except ControlPathMissing as exc:
        print(exc, file=sys.stderr)
        return 2
    if action == "engage":
        engage(" ".join(argv[1:]) or "manual")
        for pid in argv[1:]:
            if pid.isdigit():
                kill_tree(int(pid))
        print(f"PANIC engaged: {latch}")
    elif action == "clear":
        # **说出到底清掉了什么**，不要一句放之四海皆准的「cleared」。
        if clear():
            print(f"panic latch cleared: {latch}")
        else:
            print(f"没有闩可清（该路径上本来就没有）：{latch}")
    elif action == "status":
        print("ENGAGED" if engaged() else "clear")
    else:
        print("usage: panic.py [engage|clear|status]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
