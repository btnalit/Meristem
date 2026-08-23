"""Tests for substrate/fitness.py (S5, v5 spec §4.1 / §8.2 / §10.2)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate import fitness  # noqa: E402
from substrate.probe_runner import ProbeRun  # noqa: E402


def _run(probe_id="probe-x", score=40.0, passed=2, total=5,
         manifest_sha="sha-1", runner_version="1", policy_version="1",
         tree_sha="tree-0"):
    return ProbeRun(
        probe_id=probe_id, score=score, checks_passed=passed, checks_total=total,
        detail=[], probe_manifest_sha=manifest_sha, tree_sha=tree_sha,
        runner_version=runner_version, execution_policy_version=policy_version,
    )


class StatusTests(unittest.TestCase):
    """§11 'fitness': I5 五种状态各一例。"""

    def test_improved(self):
        [record] = fitness.pair([_run(score=40.0, passed=2)], [_run(score=60.0, passed=3)], "c1")
        self.assertEqual(record["status"], "improved")
        self.assertEqual(record["delta"], 20.0)

    def test_no_regression(self):
        [record] = fitness.pair([_run(score=40.0)], [_run(score=40.0)], "c1")
        self.assertEqual(record["status"], "no_regression")
        self.assertEqual(record["delta"], 0.0)

    def test_regressed(self):
        [record] = fitness.pair([_run(score=60.0, passed=3)], [_run(score=40.0, passed=2)], "c1")
        self.assertEqual(record["status"], "regressed")
        self.assertEqual(record["delta"], -20.0)

    def test_baseline_when_no_before(self):
        [record] = fitness.pair([], [_run(score=60.0, passed=3)], "c1")
        self.assertEqual(record["status"], "baseline")
        self.assertIsNone(record["before"])
        self.assertIsNone(record["delta"])

    def test_unmeasured_from_version_mismatch(self):
        before = [_run(score=40.0, runner_version="1")]
        after = [_run(score=60.0, runner_version="2")]
        [record] = fitness.pair(before, after, "c1")
        self.assertEqual(record["status"], "unmeasured")
        self.assertIsNone(record["delta"])

    def test_status_always_in_I5_enum(self):
        for record in [
            fitness.pair([_run(score=40.0)], [_run(score=60.0)], "c")[0],
            fitness.pair([_run(score=40.0)], [_run(score=40.0)], "c")[0],
            fitness.pair([_run(score=60.0)], [_run(score=40.0)], "c")[0],
            fitness.pair([], [_run(score=60.0)], "c")[0],
            fitness.pair([_run(runner_version="1")], [_run(runner_version="2")], "c")[0],
        ]:
            self.assertIn(record["status"], fitness.STATUSES)


class VersionDimensionRedGreenTests(unittest.TestCase):
    """§11 'fitness 版本维度' 红绿：三个维度各构造一例失配；三维全同同数据 -> improved。"""

    def test_runner_version_mismatch_forces_unmeasured(self):
        before = [_run(score=40.0, manifest_sha="m1", runner_version="1", policy_version="1")]
        after = [_run(score=60.0, manifest_sha="m1", runner_version="2", policy_version="1")]
        [record] = fitness.pair(before, after, "c")
        self.assertEqual(record["status"], "unmeasured")
        self.assertIsNone(record["delta"])

    def test_probe_manifest_sha_mismatch_forces_unmeasured(self):
        before = [_run(score=40.0, manifest_sha="m1", runner_version="1", policy_version="1")]
        after = [_run(score=60.0, manifest_sha="m2", runner_version="1", policy_version="1")]
        [record] = fitness.pair(before, after, "c")
        self.assertEqual(record["status"], "unmeasured")
        self.assertIsNone(record["delta"])

    def test_execution_policy_version_mismatch_forces_unmeasured(self):
        before = [_run(score=40.0, manifest_sha="m1", runner_version="1", policy_version="1")]
        after = [_run(score=60.0, manifest_sha="m1", runner_version="1", policy_version="2")]
        [record] = fitness.pair(before, after, "c")
        self.assertEqual(record["status"], "unmeasured")
        self.assertIsNone(record["delta"])

    def test_all_three_dims_match_same_data_is_improved(self):
        before = [_run(score=40.0, manifest_sha="m1", runner_version="1", policy_version="1")]
        after = [_run(score=60.0, manifest_sha="m1", runner_version="1", policy_version="1")]
        [record] = fitness.pair(before, after, "c")
        self.assertEqual(record["status"], "improved")
        self.assertEqual(record["delta"], 20.0)


class DegenerateProbeTests(unittest.TestCase):
    """§11 'fitness I3': 连续 N 次只出 0/100 的探针进 degenerate_probes()；出现 3 档则不进。"""

    @staticmethod
    def _write_ledger(ledger: Path, probe_id: str, scores):
        with ledger.open("w", encoding="utf-8") as fh:
            for s in scores:
                fh.write(json.dumps({
                    "kind": "observed_fitness",
                    "records": [{"probe_id": probe_id, "after": s}],
                }) + "\n")

    def test_two_tiers_over_window_is_suspected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-ledger.jsonl"
            self._write_ledger(ledger, "probe-degenerate", [0.0, 100.0, 0.0, 100.0, 0.0])
            self.assertIn("probe-degenerate", fitness.degenerate_probes(ledger, window=5))

    def test_three_tiers_over_window_is_not_suspected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-ledger.jsonl"
            self._write_ledger(ledger, "probe-healthy", [0.0, 50.0, 100.0, 50.0, 0.0])
            self.assertNotIn("probe-healthy", fitness.degenerate_probes(ledger, window=5))

    def test_insufficient_samples_are_not_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-ledger.jsonl"
            self._write_ledger(ledger, "probe-new", [0.0, 100.0])
            self.assertNotIn("probe-new", fitness.degenerate_probes(ledger, window=5))

    def test_accepted_fitness_events_do_not_double_count(self):
        # accepted_fitness 复述同一批 records（§10 pipeline 伪码）；只应计一次。
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-ledger.jsonl"
            with ledger.open("w", encoding="utf-8") as fh:
                for s in [0.0, 100.0]:
                    fh.write(json.dumps({"kind": "observed_fitness",
                                         "records": [{"probe_id": "p", "after": s}]}) + "\n")
                    fh.write(json.dumps({"kind": "accepted_fitness",
                                         "records": [{"probe_id": "p", "after": s}]}) + "\n")
            # 只有 2 次真实测量，窗口 5 时证据不足，不应判定。
            self.assertNotIn("p", fitness.degenerate_probes(ledger, window=5))


class NoWriteTests(unittest.TestCase):
    """write() 必须不存在：台账的唯一写入路径是 pipeline（C4）。

    一个只收 records 的 write() 填不出 §8.2 的六个强制字段，谁调它谁就产出一条
    schema 违规事件 —— 而 CA-7 恰恰断言每条 fitness 事件都带齐那六个字段。
    """

    def test_fitness_exposes_no_ledger_writer(self):
        self.assertFalse(hasattr(fitness, "write"),
                         "fitness.write() 已删除；台账写入归 pipeline.ctx.ledger.append")


class HasRegressionTests(unittest.TestCase):
    def test_regressed_record_is_a_regression(self):
        self.assertTrue(fitness.has_regression([{"status": "improved"},
                                                {"status": "regressed"}]))

    def test_no_regressed_record_is_not(self):
        self.assertFalse(fitness.has_regression([{"status": "improved"},
                                                 {"status": "no_regression"}]))

    def test_unmeasured_is_not_a_regression(self):
        """不可比 != 变坏。混淆会把一次换 runner 版本记成种子把事情做坏了。"""
        self.assertFalse(fitness.has_regression([{"status": "unmeasured"}]))


class TreeShaTests(unittest.TestCase):
    def test_pair_maps_both_tree_shas(self):
        b = _run(score=40.0, tree_sha="tree-parent")
        a = _run(score=60.0, tree_sha="tree-candidate")
        rec = fitness.pair([b], [a], "c0ffee")[0]
        self.assertEqual(rec["tree_before"], "tree-parent")
        self.assertEqual(rec["tree_after"], "tree-candidate")
        self.assertEqual(rec["status"], "improved")

    def test_baseline_has_no_tree_before(self):
        rec = fitness.pair([], [_run(tree_sha="tree-x")], "c0ffee")[0]
        self.assertIsNone(rec["tree_before"])
        self.assertEqual(rec["tree_after"], "tree-x")
        self.assertEqual(rec["status"], "baseline")


if __name__ == "__main__":
    unittest.main()
