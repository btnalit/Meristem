"""Tests for substrate/probe_runner.py (S3, v5 spec §9.2 / §10.2 / §15.6 C6)."""
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate import probe_runner  # noqa: E402


ECHO_ORGAN = textwrap.dedent("""\
    import json, sys
    payload = json.loads(sys.stdin.read())
    print(json.dumps({"output": payload["input"]}))
    """)

ENV_READING_ORGAN = textwrap.dedent("""\
    import json, os, sys
    sys.stdin.read()
    secret = os.environ["SENSENOVA_API_KEY"]
    print(json.dumps({"output": secret}))
    """)


def _make_tree(root: Path, organ_name: str = "echoer", organ_src: str = ECHO_ORGAN) -> Path:
    tree = root / "tree"
    organ_dir = tree / "body" / "organs" / organ_name
    organ_dir.mkdir(parents=True)
    (organ_dir / "run.py").write_text(organ_src, encoding="utf-8")
    return tree


def _manifest(organ: str = "echoer", checks=None) -> dict:
    return {
        "id": "probe-test",
        "capability": "test capability",
        "organ": organ,
        "checks": checks if checks is not None else [],
    }


class ProbeRunScoreTests(unittest.TestCase):
    """§11: 'probe_runner' -- 5 检查探针通过 3 个 -> score 恰为 60.0；改 2 个 -> 40.0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = _make_tree(Path(self.tmp.name))

    def test_three_of_five_scores_60(self):
        checks = [
            {"id": "c1", "input": "a", "cmp": "equals", "expect": "a"},
            {"id": "c2", "input": "b", "cmp": "equals", "expect": "b"},
            {"id": "c3", "input": "c", "cmp": "equals", "expect": "c"},
            {"id": "c4", "input": "d", "cmp": "equals", "expect": "WRONG"},
            {"id": "c5", "input": "e", "cmp": "equals", "expect": "WRONG"},
        ]
        run = probe_runner.run_probe(_manifest(checks=checks), self.tree)
        self.assertEqual(run.checks_passed, 3)
        self.assertEqual(run.checks_total, 5)
        self.assertEqual(run.score, 60.0)

    def test_two_of_five_scores_40(self):
        checks = [
            {"id": "c1", "input": "a", "cmp": "equals", "expect": "a"},
            {"id": "c2", "input": "b", "cmp": "equals", "expect": "b"},
            {"id": "c3", "input": "c", "cmp": "equals", "expect": "WRONG"},
            {"id": "c4", "input": "d", "cmp": "equals", "expect": "WRONG"},
            {"id": "c5", "input": "e", "cmp": "equals", "expect": "WRONG"},
        ]
        run = probe_runner.run_probe(_manifest(checks=checks), self.tree)
        self.assertEqual(run.checks_passed, 2)
        self.assertEqual(run.score, 40.0)

    def test_contains_comparator(self):
        checks = [{"id": "c1", "input": "closure ~52704 > 50000 budget",
                   "cmp": "contains", "expect": "budget"}]
        run = probe_runner.run_probe(_manifest(checks=checks), self.tree)
        self.assertEqual(run.checks_passed, 1)

    def test_unregistered_named_regex_yields_no_score_at_all(self):
        # 比较器白名单（§8.1.1）：regex 只接受土壤预置的命名正则，不接受字面量。
        # 测不出来就**没有分数**（返回 None），不是 0 分 —— 见 test_..._never_scores_zero。
        checks = [{"id": "c1", "input": "a", "cmp": "regex", "expect": "not-registered"}]
        self.assertIsNone(probe_runner.run_probe(_manifest(checks=checks), self.tree))

    def test_comparator_outside_whitelist_yields_no_score_at_all(self):
        checks = [{"id": "c1", "input": "a", "cmp": "startswith", "expect": "a"}]
        self.assertIsNone(probe_runner.run_probe(_manifest(checks=checks), self.tree))

    def test_unmeasured_check_never_scores_zero(self):
        """§15.6 的具体形态：crash 的 organ 记 0 分,修好它就读成一次 improved。

        一条 check 测不出来 -> 整把尺没有可信分数 -> None（§10 的 UNMEASURED 出口）。
        绝不能返回一个看起来合法的 0.0。
        """
        checks = [{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"},
                  {"id": "c2", "input": "b", "cmp": "startswith", "expect": "b"}]
        self.assertIsNone(probe_runner.run_probe(_manifest(checks=checks), self.tree))

    def test_relative_tree_path_measures_the_same_as_absolute(self):
        """argv 留相对路径而 cwd 切到 organ 目录,会得到翻倍的不存在路径 ->
        每条 check 非零退出 -> 静默变成一次「测量」。绝对路径与相对路径必须同分。"""
        checks = [{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}]
        m = _manifest(checks=checks)
        absolute = probe_runner.run_probe(m, os.path.abspath(self.tree))
        cwd = os.getcwd()
        os.chdir(os.path.dirname(os.path.abspath(self.tree)))
        self.addCleanup(os.chdir, cwd)
        relative = probe_runner.run_probe(m, os.path.basename(os.path.abspath(self.tree)))
        self.assertIsNotNone(absolute)
        self.assertIsNotNone(relative)
        self.assertEqual(absolute.score, relative.score)

    def test_malformed_manifest_returns_none_not_keyerror(self):
        """一把坏尺不得连累其它尺：原先 KeyError 会从 run_all 的列表推导里炸出去,
        把一轮测量整个打掉。"""
        self.assertIsNone(probe_runner.run_probe({"id": "x", "checks": []}, self.tree))
        self.assertIsNone(probe_runner.run_probe({"organ": "o", "checks": []}, self.tree))

    def test_version_dims_produced_at_measurement_time(self):
        checks = [{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}]
        run = probe_runner.run_probe(_manifest(checks=checks), self.tree)
        self.assertTrue(run.probe_manifest_sha)
        self.assertEqual(run.runner_version, probe_runner.RUNNER_VERSION)
        self.assertEqual(run.execution_policy_version, probe_runner.EXECUTION_POLICY_VERSION)


class SecretIsolationTests(unittest.TestCase):
    """§15.6 C6 验收：尝试读环境变量的 organ -- 读不到，且记 unmeasured。"""

    def test_organ_reading_env_var_is_unmeasured(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp), organ_name="leaker", organ_src=ENV_READING_ORGAN)
            os.environ["SENSENOVA_API_KEY"] = "super-secret"
            self.addCleanup(lambda: os.environ.pop("SENSENOVA_API_KEY", None))
            checks = [{"id": "c1", "input": "x", "cmp": "equals", "expect": "super-secret"}]
            run = probe_runner.run_probe(_manifest(organ="leaker", checks=checks), tree)
            # 读不到 secret -> organ 崩溃 -> unmeasured -> 整把尺无分数。
            # 若这里返回 0.0，下一拍「修好」这个 organ 就会读成一次 improved。
            self.assertIsNone(run)
            # 而且 secret 绝不能出现在任何返回值里。
            self.assertNotIn("super-secret", json.dumps(probe_runner.run_all(tree, tree) or []))


class CatalogueTests(unittest.TestCase):
    """§9.2 / §10.2: catalogue() 是读 vault 的唯一入口；§8.1.1 的 entrypoint 字段即拒绝。

    F1: 一把坏尺（读不了文件 / JSON 解析失败 / 含 entrypoint 字段）必须让整份
    清单编不出来（返回 None），不是悄悄跳过那一把、拿其余的凑一份「少一把尺的
    清单」出去——那正是 run_all 自己的 docstring 点名绝不做的事。
    """

    def test_catalogue_reads_frozen_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            probe_dir = vault / "internal" / "active" / "probe-a"
            probe_dir.mkdir(parents=True)
            manifest = _manifest(checks=[{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}])
            (probe_dir / "probe.json").write_text(json.dumps(manifest), encoding="utf-8")
            found = probe_runner.catalogue(vault)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["id"], "probe-test")

    def test_catalogue_rejects_entrypoint_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            probe_dir = vault / "internal" / "active" / "probe-b"
            probe_dir.mkdir(parents=True)
            manifest = _manifest()
            manifest["entrypoint"] = ["python3", "hack.py"]
            (probe_dir / "probe.json").write_text(json.dumps(manifest), encoding="utf-8")
            # 不是 []（悄悄跳过这把坏尺）——CA-5 的语义是「出现即拒绝」，拒绝必须
            # 让整份清单这一次编不出来。
            self.assertIsNone(probe_runner.catalogue(vault))

    def test_catalogue_returns_none_on_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            probe_dir = vault / "internal" / "active" / "probe-c"
            probe_dir.mkdir(parents=True)
            (probe_dir / "probe.json").write_text("{not valid json", encoding="utf-8")
            self.assertIsNone(probe_runner.catalogue(vault))

    def test_catalogue_returns_none_on_missing_manifest_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            probe_dir = vault / "internal" / "active" / "probe-d"
            probe_dir.mkdir(parents=True)
            # probe.json 从不存在——目录在，尺不在。
            self.assertIsNone(probe_runner.catalogue(vault))

    def test_catalogue_on_missing_vault_returns_empty(self):
        # 尚未冻结过任何 probe 是合法的初始状态，不是一把坏尺；[] 而非 None。
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(probe_runner.catalogue(Path(tmp) / "no-such-vault"), [])


class RunAllTests(unittest.TestCase):
    def test_run_all_combines_catalogue_and_run_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tree = _make_tree(tmp_path)
            vault = tmp_path / "vault"
            probe_dir = vault / "internal" / "active" / "probe-test"
            probe_dir.mkdir(parents=True)
            manifest = _manifest(checks=[{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}])
            (probe_dir / "probe.json").write_text(json.dumps(manifest), encoding="utf-8")
            runs = probe_runner.run_all(tree, vault)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].score, 100.0)

    def test_run_all_returns_full_list_when_all_manifests_are_legal(self):
        """绿的一半：全部 manifest 合法时,run_all 返回完整列表,一把都不少。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tree = _make_tree(tmp_path)
            vault = tmp_path / "vault"
            for name in ("probe-1", "probe-2", "probe-3"):
                probe_dir = vault / "internal" / "active" / name
                probe_dir.mkdir(parents=True)
                manifest = _manifest(
                    checks=[{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}])
                manifest["id"] = name
                (probe_dir / "probe.json").write_text(json.dumps(manifest), encoding="utf-8")
            runs = probe_runner.run_all(tree, vault)
            self.assertIsNotNone(runs)
            self.assertEqual({r.probe_id for r in runs}, {"probe-1", "probe-2", "probe-3"})

    def test_run_all_returns_none_not_a_short_list_when_one_manifest_has_entrypoint(self):
        """F1 红：一把尺被拒（entrypoint 字段）不得只是从清单里消失,必须让整轮
        run_all 测不出来——而不是悄悄返回「另外两把」的列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tree = _make_tree(tmp_path)
            vault = tmp_path / "vault"
            good_dir = vault / "internal" / "active" / "probe-good"
            good_dir.mkdir(parents=True)
            good_manifest = _manifest(
                checks=[{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}])
            (good_dir / "probe.json").write_text(json.dumps(good_manifest), encoding="utf-8")

            bad_dir = vault / "internal" / "active" / "probe-bad"
            bad_dir.mkdir(parents=True)
            bad_manifest = _manifest()
            bad_manifest["id"] = "probe-bad"
            bad_manifest["entrypoint"] = ["python3", "hack.py"]
            (bad_dir / "probe.json").write_text(json.dumps(bad_manifest), encoding="utf-8")

            self.assertIsNone(probe_runner.run_all(tree, vault))

    def test_run_all_returns_none_not_a_short_list_when_one_manifest_json_is_malformed(self):
        """F1 红：JSON 解析失败的那一把同样必须让整轮 run_all 测不出来。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tree = _make_tree(tmp_path)
            vault = tmp_path / "vault"
            good_dir = vault / "internal" / "active" / "probe-good"
            good_dir.mkdir(parents=True)
            good_manifest = _manifest(
                checks=[{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}])
            (good_dir / "probe.json").write_text(json.dumps(good_manifest), encoding="utf-8")

            bad_dir = vault / "internal" / "active" / "probe-bad"
            bad_dir.mkdir(parents=True)
            (bad_dir / "probe.json").write_text("{not valid json", encoding="utf-8")

            self.assertIsNone(probe_runner.run_all(tree, vault))


class RealClassifierOrganIntegrationTests(unittest.TestCase):
    """只读验证（不修改 body/，body/organs/classifier/run.py 不在本次交付范围内）：
    probe_runner 假设的 organ 入口路径（body/organs/<organ>/run.py）与输出 ABI
    （恰好一个字符串值的 JSON 对象）确实与仓库里已经存在的分类器 organ 一致，
    不是两套互不兼容的约定各说各话。"""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.classifier = self.repo_root / "body" / "organs" / "classifier" / "run.py"
        if not self.classifier.is_file():
            self.skipTest("body/organs/classifier/run.py not present in this worktree")

    def test_real_classifier_organ_is_reachable_and_measurable(self):
        checks = [{"id": "c1", "input": "hello world", "cmp": "equals", "expect": "unclassified"}]
        run = probe_runner.run_probe(
            _manifest(organ="classifier", checks=checks), self.repo_root)
        # 关键断言：不是 "entrypoint missing" / "output ABI violation" ->
        # run_probe 真的找到了这个 organ 并且解析出了它的输出。
        self.assertNotEqual(run.detail[0]["result"], "unmeasured")


class MalformedManifestShapeTests(unittest.TestCase):
    """独立审查发现的第四类坏 manifest：合法 JSON，但形状不对。

    这些**曾经抛未捕获的 TypeError 炸穿 run_all**，而不是走 None 出口。
    崩溃比「少一把尺的清单」更糟 —— 它连优雅退化成 UNMEASURED 都做不到。
    每一种都必须与「缺字段」走同一条出口。
    """

    def _vault_with(self, tmp: Path, content: str) -> Path:
        vault = tmp / "vault"
        probe_dir = vault / "internal" / "active" / "probe-bad"
        probe_dir.mkdir(parents=True)
        (probe_dir / "probe.json").write_text(content, encoding="utf-8")
        return vault

    def test_valid_json_that_is_not_an_object_yields_none(self):
        # 42 / null / true 会让 `"entrypoint" in manifest` 抛 TypeError；
        # 数组和裸字符串更坏 —— 它们支持 `in`，会悄悄混进清单直到 run_probe 才崩。
        for content in ("42", "null", "true", "[1, 2, 3]", '"a string"'):
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as tmp:
                    vault = self._vault_with(Path(tmp), content)
                    self.assertIsNone(probe_runner.catalogue(vault))
                    self.assertIsNone(probe_runner.run_all(vault, vault))

    def test_run_probe_on_non_dict_manifest_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp))
            for manifest in (42, None, True, [1, 2, 3], "a string"):
                with self.subTest(manifest=manifest):
                    self.assertIsNone(probe_runner.run_probe(manifest, tree))

    def test_checks_wrong_type_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp))
            for checks in (None, "not a list", 7, [1, 2], ["str"], [None]):
                with self.subTest(checks=checks):
                    m = {"id": "p1", "organ": "echoer", "checks": checks}
                    self.assertIsNone(probe_runner.run_probe(m, tree))

    def test_legal_manifest_still_measures(self):
        """绿的一半：形状校验不能把合法 manifest 一起挡掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp))
            m = _manifest(checks=[{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}])
            run = probe_runner.run_probe(m, tree)
            self.assertIsNotNone(run)
            self.assertEqual(run.score, 100.0)


def _five_checks():
    return [{"id": f"c{i}", "input": f"in{i}", "cmp": "equals", "expect": f"out{i}"}
            for i in range(5)]


class ProposalValidationTests(unittest.TestCase):
    """`probe_runner.validate_proposal()` —— §8.1.1 schema + I2 校验，
    **冻结路径执行**（C1；`_checks_well_formed` 的 docstring 明写运行时
    故意不查数量）。"""

    def _proposal(self, **overrides):
        proposal = {"id": "probe-x", "capability": "test capability",
                    "organ": "classifier", "checks": _five_checks()}
        proposal.update(overrides)
        return proposal

    def test_well_formed_proposal_passes(self):
        probe_runner.validate_proposal(self._proposal())  # 不抛即通过

    def test_fewer_than_five_checks_refused(self):
        """I2：<5 个 check 在冻结点拒绝——这是 I2 唯一的执行点。"""
        checks = [{"id": f"c{i}", "input": "a", "cmp": "equals", "expect": "a"}
                  for i in range(4)]
        with self.assertRaises(probe_runner.ProbeProposalError):
            probe_runner.validate_proposal(self._proposal(checks=checks))

    def test_exactly_five_checks_is_the_boundary(self):
        checks = [{"id": f"c{i}", "input": "a", "cmp": "equals", "expect": "a"}
                  for i in range(5)]
        probe_runner.validate_proposal(self._proposal(checks=checks))  # 不抛

    def test_entrypoint_field_refused(self):
        """§8.1.1 / CA-5：种子可写 schema 里没有 entrypoint，出现即拒绝。"""
        with self.assertRaises(probe_runner.ProbeProposalError):
            probe_runner.validate_proposal(
                self._proposal(entrypoint=["python3", "hack.py"]))

    def test_missing_capability_refused(self):
        proposal = self._proposal()
        del proposal["capability"]
        with self.assertRaises(probe_runner.ProbeProposalError):
            probe_runner.validate_proposal(proposal)

    def test_missing_organ_refused(self):
        proposal = self._proposal()
        del proposal["organ"]
        with self.assertRaises(probe_runner.ProbeProposalError):
            probe_runner.validate_proposal(proposal)

    def test_missing_id_refused(self):
        proposal = self._proposal()
        del proposal["id"]
        with self.assertRaises(probe_runner.ProbeProposalError):
            probe_runner.validate_proposal(proposal)

    def test_malformed_check_refused(self):
        checks = [{"id": f"c{i}", "input": "a", "cmp": "equals", "expect": "a"}
                  for i in range(4)]
        checks.append({"id": "c5", "input": "a", "cmp": "not-in-whitelist", "expect": "a"})
        with self.assertRaises(probe_runner.ProbeProposalError):
            probe_runner.validate_proposal(self._proposal(checks=checks))

    def test_non_dict_manifest_refused(self):
        for bad in (42, None, True, [1, 2, 3], "a string"):
            with self.subTest(bad=bad):
                with self.assertRaises(probe_runner.ProbeProposalError):
                    probe_runner.validate_proposal(bad)

    def test_runtime_still_does_not_enforce_cardinality(self):
        """反向断言：I2 的执行点只在冻结路径（`validate_proposal`），不在
        `run_probe()`——加了 I2 不能把运行时也顺手锁死，那会把「这把尺不
        合法」与「这把尺这次测不出来」混成同一个出口（`_checks_well_formed`
        的 docstring 讲的正是这件事）。"""
        checks = [{"id": "c1", "input": "a", "cmp": "equals", "expect": "a"}]
        manifest = self._proposal(checks=checks, organ="echoer")
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp))  # 默认 organ_name="echoer"，与上面对齐
            run = probe_runner.run_probe(manifest, tree)
        self.assertIsNotNone(run)  # 仍然测得出来，只是只有 1 个 check


class FreezeIntoVaultTests(unittest.TestCase):
    """`probe_runner.freeze_into_vault()` —— C1 的 vault 写入半。"""

    def _manifest(self, probe_id="probe-y"):
        return {"id": probe_id, "capability": "test", "organ": "classifier",
                "checks": _five_checks()}

    def test_freeze_writes_manifest_and_returns_matching_sha(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            sha = probe_runner.freeze_into_vault(vault, manifest)
            written = json.loads(
                (vault / "internal" / "active" / "probe-y" / "probe.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(written, manifest)
            # 构造性保证：与 run_probe() 测量时用的是同一个 _manifest_sha()。
            self.assertEqual(sha, probe_runner._manifest_sha(manifest))

    def test_frozen_manifest_is_reachable_by_catalogue(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            probe_runner.freeze_into_vault(vault, manifest)
            found = probe_runner.catalogue(vault)
            self.assertEqual([m["id"] for m in found], ["probe-y"])

    def test_freeze_refuses_duplicate_probe_id(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            probe_runner.freeze_into_vault(vault, manifest)
            with self.assertRaises(probe_runner.ProbeProposalError):
                probe_runner.freeze_into_vault(vault, manifest)
            # 拒绝时不得改动已经冻结的那份内容。
            found = probe_runner.catalogue(vault)
            self.assertEqual(len(found), 1)

    def test_freeze_refuses_unvalidated_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            with self.assertRaises(probe_runner.ProbeProposalError):
                probe_runner.freeze_into_vault(vault, {"capability": "no id here"})


if __name__ == "__main__":
    unittest.main()
