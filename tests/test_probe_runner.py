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

    def test_unregistered_named_regex_is_unmeasured_not_silently_true(self):
        # 比较器白名单（§8.1.1）：regex 只接受土壤预置的命名正则，不接受字面量。
        checks = [{"id": "c1", "input": "a", "cmp": "regex", "expect": "not-registered"}]
        run = probe_runner.run_probe(_manifest(checks=checks), self.tree)
        self.assertEqual(run.checks_passed, 0)
        self.assertEqual(run.detail[0]["result"], "unmeasured")

    def test_comparator_outside_whitelist_is_unmeasured(self):
        checks = [{"id": "c1", "input": "a", "cmp": "startswith", "expect": "a"}]
        run = probe_runner.run_probe(_manifest(checks=checks), self.tree)
        self.assertEqual(run.checks_passed, 0)
        self.assertEqual(run.detail[0]["result"], "unmeasured")

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
            self.assertEqual(run.checks_passed, 0)
            self.assertEqual(run.detail[0]["result"], "unmeasured")
            self.assertNotIn("super-secret", json.dumps(run.detail))


class CatalogueTests(unittest.TestCase):
    """§9.2 / §10.2: catalogue() 是读 vault 的唯一入口；§8.1.1 的 entrypoint 字段即拒绝。"""

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
            self.assertEqual(probe_runner.catalogue(vault), [])

    def test_catalogue_on_missing_vault_returns_empty(self):
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


if __name__ == "__main__":
    unittest.main()
