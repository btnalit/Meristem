"""种子写入边界（§10.1）与不可信执行契约（§15.6 C6）的断言。

两个来源：2026-08-23 的外部独立审查复现了两处 P0，本文件把**两处都变成断言** ——
一处已修（写入边界），一处未修（执行隔离）且刻意写成 `expectedFailure`。

**为什么未修的那两条不写成 skip。** skip 会让「还没做」和「不适用」长得一样；
`expectedFailure` 在它被修好的那一刻报 **unexpected success**，
那正是「该来摘掉这个标记了」的提醒。SA-5 用的就是这个办法，
而它确实在 `pipeline.py` 落地当天把人叫醒了。skip 不会叫醒任何人 ——
这份规格自己写过：`advance()` 零调用点就是这么活过 400 拍的。
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meristem import engine  # noqa: E402
from substrate import probe_runner  # noqa: E402


def _can_symlink(tmp: Path) -> bool:
    try:
        target = tmp / "_probe-target"
        target.write_text("x", encoding="utf-8")
        link = tmp / "_probe-link"
        os.symlink(target, link)
        link.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


class SeedWriteBoundaryTests(unittest.TestCase):
    """§10.1：`seed/` 是**文件级白名单**，而白名单必须挡住链接穿透。

    审查复现的攻击：把白名单内的 `seed/narrative.md` 链到土壤私有的
    `soil/report-facts.json`，种子写前者、实际覆盖后者。
    §10.1 逐字写过这个例子（「否则 seed/narrative.md 可被链到
    soil/report-facts.json，白名单被穿透」）—— 守卫写了，
    但它检查的是 `resolve()` 之后的路径，**而 resolve 已经把链接跟掉了**。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "seed").mkdir()
        (self.root / "soil").mkdir()
        self.protected = self.root / "soil" / "report-facts.json"
        self.protected.write_text("SOIL-OWNED", encoding="utf-8")

    def _apply(self, rel="seed/narrative.md", content="SEED-WROTE-THIS"):
        return engine.apply(engine.Mutation(task="t", files={rel: content}), self.root)

    def test_symlinked_file_cannot_be_written_through(self):
        if not _can_symlink(self.root):
            self.skipTest("平台不允许建 symlink（非缺陷，是环境限制）")
        os.symlink(self.protected, self.root / "seed" / "narrative.md")
        with self.assertRaises(engine.PathViolation):
            self._apply()
        self.assertEqual(self.protected.read_text(encoding="utf-8"), "SOIL-OWNED")

    def test_symlinked_parent_directory_cannot_be_written_through(self):
        """**每一级都要查，不只是最后一级。** 一个被链走的中间目录
        同样能把最终写入带出白名单。"""
        if not _can_symlink(self.root):
            self.skipTest("平台不允许建 symlink（非缺陷，是环境限制）")
        (self.root / "seed").rmdir()
        os.symlink(self.root / "soil", self.root / "seed", target_is_directory=True)
        with self.assertRaises(engine.PathViolation):
            self._apply(rel="seed/probe-proposals/x.json")

    def test_hardlinked_file_cannot_be_written_through(self):
        """hardlink **不是指向路径的链接，而是同一 inode 的第二个名字** ——
        没有任何 open flag 能区分它，`O_NOFOLLOW` 对它无效。
        所以必须靠 `st_nlink > 1` 直接拒绝（§10.1 同时点名了 hardlink）。"""
        try:
            os.link(self.protected, self.root / "seed" / "narrative.md")
        except (OSError, NotImplementedError):
            self.skipTest("平台不允许建 hardlink（非缺陷，是环境限制）")
        with self.assertRaises(engine.PathViolation):
            self._apply()
        self.assertEqual(self.protected.read_text(encoding="utf-8"), "SOIL-OWNED")

    def test_ntfs_junction_directory_cannot_be_written_through(self):
        """**junction 不是 symlink，`is_symlink()` 看不见它。**

        2026-08-23 对抗性审查用它绕过了本守卫的上一版：把 `seed/probe-proposals/`
        （一个合法的 SEED_WRITABLE 目录前缀）做成指向 `soil/` 的 junction，
        写入直接落在土壤私有面上。junction 与真 symlink 的关键差别是
        **创建它不需要任何特权** —— Windows 上真 symlink 要管理员或开发者模式，
        junction 不要。所以它是这条路上更容易走的那一扇门，而不是更难的。
        """
        if sys.platform != "win32":
            self.skipTest("junction 是 NTFS 概念（非缺陷，是平台差异）")
        junction = self.root / "seed" / "probe-proposals"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction),
                               str(self.root / "soil")], capture_output=True)
        if made.returncode != 0:
            self.skipTest("此环境不允许创建 junction")
        self.assertFalse(junction.is_symlink(),
                         "前提变了：junction 现在被 is_symlink() 报告了")
        self.assertTrue(engine._is_link(junction), "_is_link 必须认出 reparse point")
        with self.assertRaises(engine.PathViolation):
            self._apply(rel="seed/probe-proposals/report-facts.json")
        self.assertEqual(self.protected.read_text(encoding="utf-8"), "SOIL-OWNED")

    def test_write_refuses_when_target_identity_changes_after_the_check(self):
        """TOCTOU：检查与 open 之间目标被换掉，必须在**截断之前**发现。

        Windows 没有 `O_NOFOLLOW`，静态检查与 open 不是原子的。修法是
        「不带 O_TRUNC 打开 → 比对 fd 身份 → 再 ftruncate」，
        所以即便竞态赢了，被指向的文件也不会先被清空。
        这里直接对 `_safe_write` 注入一次身份变化来断言那条分支。
        """
        victim = self.root / "seed" / "narrative.md"
        victim.write_text("ORIGINAL", encoding="utf-8")

        class _OtherFile:
            """open 之后拿到的 fd 指向了另一个文件 —— 竞态赢了的那一刻。"""
            st_dev, st_ino, st_nlink = 1, 999999, 1

        real_fstat = os.fstat
        os.fstat = lambda fd: _OtherFile()
        try:
            with self.assertRaises(engine.PathViolation):
                engine._safe_write(victim, "SHOULD-NOT-LAND")
        finally:
            os.fstat = real_fstat

        # **关键断言：文件没有被清空。** 打开时不带 O_TRUNC 就是为了这一条 ——
        # 先截断再发现身份不对，等于攻击已经得手了一半。
        self.assertEqual(victim.read_text(encoding="utf-8"), "ORIGINAL")

    def test_ordinary_whitelisted_write_still_works(self):
        """守卫收紧之后，正常写入不能被误伤 —— 否则这道修复只是把门焊死。"""
        written = self._apply(content="hello")
        self.assertEqual(written, ["seed/narrative.md"])
        self.assertEqual((self.root / "seed" / "narrative.md").read_text(encoding="utf-8"),
                         "hello")


class ProbeManifestValidationTests(unittest.TestCase):
    """§8.1.1：坏 manifest 必须走 `unmeasured` 出口，**不得抛异常炸穿调用者**。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name) / "tree"
        organ = self.tree / "body" / "organs" / "classifier"
        organ.mkdir(parents=True)
        (organ / "run.py").write_text(
            'import json,sys\np=json.loads(sys.stdin.read())\n'
            'print(json.dumps({"category": p["input"]}))\n', encoding="utf-8")

    def _manifest(self, checks):
        return {"id": "probe-x", "capability": "c", "organ": "classifier", "checks": checks}

    def test_check_missing_cmp_is_unmeasured_not_keyerror(self):
        result = probe_runner.run_probe(self._manifest([{"id": "c1", "input": "x"}]), self.tree)
        self.assertIsNone(result)

    def test_check_with_unknown_comparator_is_refused(self):
        result = probe_runner.run_probe(self._manifest(
            [{"id": "c1", "input": "x", "cmp": "eval", "expect": "x"}]), self.tree)
        self.assertIsNone(result)

    def test_duplicate_check_ids_are_refused(self):
        dup = [{"id": "c1", "input": "x", "cmp": "equals", "expect": "x"},
               {"id": "c1", "input": "y", "cmp": "equals", "expect": "y"}]
        self.assertIsNone(probe_runner.run_probe(self._manifest(dup), self.tree))

    def test_well_formed_checks_still_run(self):
        good = [{"id": "c1", "input": "x", "cmp": "equals", "expect": "x"}]
        run = probe_runner.run_probe(self._manifest(good), self.tree)
        self.assertIsNotNone(run)
        self.assertEqual(run.checks_passed, 1)


class UntrustedExecutionContractTests(unittest.TestCase):
    """§15.6 C6 **最小完整性隔离（P0-a 档）**：vault 不可读 · ledger 不可写 · 禁网。

    规格把这一档明确切在 P0-a，理由写在同一节：
    「威胁模型不只是恶意的自主种子 —— **更是一个有 bug 的 organ 污染土壤状态、
    制造一次假的 `accepted_fitness`**。那是实验完整性问题，从第一拍就存在。」
    并且逐字点名了当前实现的不足：
    「**「不继承环境变量」≠「读不到 secrets」，「subprocess」≠「隔离」**：
    同 UID 下可读 `/proc/<pid>/environ`、**可用绝对路径读 vault**、可直接联网。」

    当前 `probe_runner` 只做了裁剪环境变量 + subprocess + 墙钟超时。
    下面两条是 2026-08-23 外部审查复现过的攻击，**至今未修**，
    因此标成 `expectedFailure`：修好的那天它们会报 unexpected success。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.secret = self.root / "vault-anchor-secret.txt"
        self.secret.write_text("ANCHOR_HIDDEN_CASE_ANSWER", encoding="utf-8")
        self.ledger = self.root / "soil-ledger.jsonl"
        self.ledger.write_text("", encoding="utf-8")
        self.tree = self.root / "tree"

    def _organ(self, body: str):
        organ = self.tree / "body" / "organs" / "classifier"
        organ.mkdir(parents=True, exist_ok=True)
        (organ / "run.py").write_text(body, encoding="utf-8")

    def _run_one(self):
        manifest = {"id": "probe-x", "capability": "c", "organ": "classifier",
                    "checks": [{"id": "c1", "input": "x", "cmp": "equals", "expect": "x"}]}
        return probe_runner.run_probe(manifest, self.tree)

    @unittest.expectedFailure
    def test_organ_cannot_read_the_vault_by_absolute_path(self):
        """**vault 不可读**。organ 读到 anchor 的隐藏 case，
        anchor 作为外部锚的全部价值即刻归零 —— 它就是靠种子看不见才成立的。"""
        self._organ(textwrap.dedent(f"""\
            import json, sys, pathlib
            sys.stdin.read()
            try:
                got = pathlib.Path({str(self.secret)!r}).read_text()
            except Exception as exc:
                got = "BLOCKED"
            print(json.dumps({{"category": got}}))
            """))
        run = self._run_one()
        leaked = run is not None and any(
            "ANCHOR_HIDDEN_CASE_ANSWER" in str(d.get("got", "")) for d in run.detail)
        self.assertFalse(leaked, "organ 用绝对路径读到了 vault 内容（§15.6 C6 P0-a 档）")

    @unittest.expectedFailure
    def test_organ_cannot_write_the_soil_ledger(self):
        """**ledger 不可写**。这条比读 vault 更直接：organ 能往台账追加，
        就能伪造一条 `accepted_fitness` —— **凭空制造出 §1.2 判据要的那个事件**，
        而整个 P0-a 就是拿那个事件来判断 H1 是否成立的。"""
        self._organ(textwrap.dedent(f"""\
            import json, sys
            sys.stdin.read()
            try:
                with open({str(self.ledger)!r}, "a") as fh:
                    fh.write("FORGED\\n")
                got = "WROTE"
            except Exception:
                got = "BLOCKED"
            print(json.dumps({{"category": got}}))
            """))
        self._run_one()
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), "",
                         "organ 往土壤台账里写进去了（§15.6 C6 P0-a 档）")

    def test_organ_cannot_read_secrets_from_the_environment(self):
        """这一条是**已经生效**的那半 —— §15.6 的验收判据之一：
        「构造一个尝试读环境变量的 organ，断言它读不到且记 `unmeasured`」。"""
        os.environ["SENSENOVA_API_KEY"] = "should-not-be-visible"
        self.addCleanup(os.environ.pop, "SENSENOVA_API_KEY", None)
        self._organ(textwrap.dedent("""\
            import json, os, sys
            sys.stdin.read()
            secret = os.environ["SENSENOVA_API_KEY"]
            print(json.dumps({"output": secret}))
            """))
        run = self._run_one()
        # organ 因读不到而崩溃 -> 整把尺记 unmeasured（不是 0 分）。
        self.assertIsNone(run)


if __name__ == "__main__":
    unittest.main()
