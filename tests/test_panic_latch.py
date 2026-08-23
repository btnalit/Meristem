"""panic 闩（`root/panic.py`）—— 信任根里的急停开关。

2026-08-24：操作员在没 source 环境文件的 shell 里跑 `panic.py clear`，
命令打印了「panic latch cleared」，**而闩纹丝不动** —— 因为
`MERISTEM_CONTROL` 缺省把路径指到了一个不存在的 `~/.meristem-control/`，
`unlink(missing_ok=True)` 什么也没做，调用方照样打印成功。

**反方向更致命**：`engage()` 会 mkdir 并把闩写进那个没人检查的目录 ——
**你以为按下了急停，系统照跑**。一个会谎报成功的急停开关比没有更坏，
因为它让人停止寻找别的停法。

与 vault 的 C-65 是同一个病（缺省失败时不报错，只是指到错的地方），
只是长在信任根里 —— **信任根尤其不允许「猜一个路径」。**
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from root import panic  # noqa: E402


class LatchPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = os.environ.get("MERISTEM_CONTROL")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("MERISTEM_CONTROL", None)
        else:
            os.environ["MERISTEM_CONTROL"] = self._saved

    def test_refuses_to_guess_a_path_when_control_is_unset(self):
        """**不猜。** 缺省曾经指向 `~/.meristem-control` —— 一个没人检查的地方。"""
        os.environ.pop("MERISTEM_CONTROL", None)
        with self.assertRaises(panic.ControlPathMissing):
            panic.latch_path()
        with self.assertRaises(panic.ControlPathMissing):
            panic.engaged()

    def test_engage_then_clear_round_trip_on_the_real_path(self):
        os.environ["MERISTEM_CONTROL"] = self.tmp.name
        self.assertFalse(panic.engaged())
        panic.engage("test stop")
        self.assertTrue(panic.engaged())
        self.assertTrue((Path(self.tmp.name) / "PANIC").exists())
        self.assertTrue(panic.clear())
        self.assertFalse(panic.engaged())

    def test_clear_reports_whether_anything_was_actually_cleared(self):
        """`clear()` 必须能区分「清掉了一个闩」与「这里本来就没有闩」。

        旧版两种情形打印同一句话 —— 于是「路径指错了」看起来和「成功」一样。
        """
        os.environ["MERISTEM_CONTROL"] = self.tmp.name
        self.assertFalse(panic.clear(), "空目录上 clear 应报告「本来就没有」")
        panic.engage("x")
        self.assertTrue(panic.clear(), "确实清掉时应报告 True")

    def test_cli_exits_nonzero_instead_of_lying_when_control_is_unset(self):
        """**这条是那次事故的直接回归。**

        命令行不得在无法定位闩时打印成功。
        """
        env = {k: v for k, v in os.environ.items() if k != "MERISTEM_CONTROL"}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "root" / "panic.py"),
             "clear"],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("cleared", result.stdout)
        self.assertIn("MERISTEM_CONTROL", result.stderr)


class SupervisorRefusesWhenLatchIsUnlocatable(unittest.TestCase):
    """「不知道闩在哪」与「闩没上」必须是两件事。

    把前者当成后者，等于在一个**无法急停**的系统上开工。
    """

    def test_manual_cycle_refuses_when_control_is_unset(self):
        saved = os.environ.get("MERISTEM_CONTROL")
        os.environ.pop("MERISTEM_CONTROL", None)
        try:
            from substrate import supervisor
            self.assertTrue(supervisor._refuse_if_latched())
        finally:
            if saved is not None:
                os.environ["MERISTEM_CONTROL"] = saved


if __name__ == "__main__":
    unittest.main()
