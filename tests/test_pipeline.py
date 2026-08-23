"""Tests for substrate/pipeline.py + substrate/soil_state.py（S2+S4+S5, §10.2 / §8.2）.

§10.2 的验收判据逐条落地：**六种非晋升情形各构造一例**，断言 `kind`（恒为
`promotion_outcome`）、`outcome`、`counts_as_progress`（恒为 `False`）与额度计入正确；
**外加一条负断言**：六种情形均不得产生 `accepted_fitness` —— 否则 §1.2 的判据会把
失败数成点火。

vault 一律建在 `tmp_path` 里，**绝不建在仓库附近**（C-65：`.claude/worktrees/` 下
那份 anchor vault 副本就是这么来的）。
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

from substrate import pipeline  # noqa: E402
from substrate import soil_state  # noqa: E402

#: 探针的 5 个 check：input -> 期望类别。
CHECKS = [
    {"id": "c1", "input": "closure over budget", "cmp": "equals", "expect": "closure-budget"},
    {"id": "c2", "input": "touches protected path", "cmp": "equals", "expect": "protected-path"},
    {"id": "c3", "input": "anchor regressed", "cmp": "equals", "expect": "probe-regressed"},
    {"id": "c4", "input": "prompt surface too large", "cmp": "equals", "expect": "prompt-budget"},
    {"id": "c5", "input": "contract surface too large", "cmp": "equals", "expect": "contract-budget"},
]

#: 点火 organ 的初始状态：5 个 check 认得 2 个 -> 40 分（§12.2 刻意做成 40%）。
KNOWS_TWO = {"closure over budget": "closure-budget",
             "touches protected path": "protected-path"}
KNOWS_THREE = dict(KNOWS_TWO, **{"anchor regressed": "probe-regressed"})
KNOWS_ONE = {"closure over budget": "closure-budget"}
#: 认得的仍是 2 个，但换了一个 —— 内容变了、分数没变（仍是 40）。
#: 用它构造「有 diff 但 delta 为 0」的候选：写一份与父树逐字相同的树，
#: git 会拒绝提交，那样测到的是测试脚手架而不是流水线。
KNOWS_TWO_ALT = {"closure over budget": "closure-budget",
                 "anchor regressed": "probe-regressed"}

PROBE_ID = "probe-classify-basic"

#: 固定时间与身份 -> commit sha 可复现。CA-11 要比对两次运行的事件序列，
#: 而两个仓库若产出不同的 commit sha，"逐字相同" 就无从谈起。
FIXED_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
}

ORGAN_TEMPLATE = textwrap.dedent("""\
    import json, sys
    TABLE = {table!r}
    payload = json.loads(sys.stdin.read())
    print(json.dumps({{"category": TABLE.get(payload["input"], "no-match")}}))
    """)

CRASHING_ORGAN = "import sys\nsys.exit(3)\n"

#: canary 跑的是 `python -m meristem.loop selftest`（§9.1：0 = 通过）。
#: 测试仓库里放一个最小桩，而不是依赖真种子 —— 被测的是流水线，不是种子。
SELFTEST_OK = "import sys\nsys.exit(0)\n"
SELFTEST_FAIL = "import sys\nsys.exit(1)\n"


def _git(repo, *args, env=None):
    full_env = {**os.environ, **FIXED_GIT_ENV, **(env or {})}
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                            text=True, env=full_env)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_vault(root: Path) -> Path:
    """internal probe 的冻结 vault。**建在 tmp 里，不在仓库附近**（C-65）。"""
    vault = root / "vault"
    probe_dir = vault / "internal" / "active" / PROBE_ID
    probe_dir.mkdir(parents=True)
    _write(probe_dir / "probe.json", json.dumps({
        "id": PROBE_ID, "capability": "把失败原因分类到命名类别",
        "organ": "classifier", "checks": CHECKS}, ensure_ascii=False))
    return vault


def _make_repo(root: Path, *, table=KNOWS_TWO, selftest=SELFTEST_OK) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _write(repo / "body" / "organs" / "classifier" / "run.py",
           ORGAN_TEMPLATE.format(table=table))
    _write(repo / "meristem" / "__init__.py", "")
    _write(repo / "meristem" / "loop.py", selftest)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _make_candidate(repo: Path, *, table=None, organ_src=None, selftest=None) -> str:
    """产出一个候选 commit，**并把主线 HEAD 退回它的父提交**。

    这正是 §10.1「提交到 worktree」在测试里的等价形态：候选存在，
    而 `candidate.parent == HEAD` 成立 —— C3 的祖先检查因此有意义。
    """
    base = _git(repo, "rev-parse", "HEAD")
    if organ_src is not None:
        _write(repo / "body" / "organs" / "classifier" / "run.py", organ_src)
    elif table is not None:
        _write(repo / "body" / "organs" / "classifier" / "run.py",
               ORGAN_TEMPLATE.format(table=table))
    if selftest is not None:
        _write(repo / "meristem" / "loop.py", selftest)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "--hard", "-q", base)
    return candidate


def _task(minimum_delta=20.0, expected="score_increase") -> pipeline.Task:
    return pipeline.Task(task_id="task-test", kind="repair", target="classifier",
                         primary_probe=PROBE_ID, expected=expected,
                         minimum_delta=minimum_delta)


def _accept(commit, diff, task):
    return pipeline.Verdict(passed=True, authority="panel", reason="ok")


def _accept_manual(commit, diff, task):
    return pipeline.Verdict(passed=True, authority="manual", reason="ok")


def _reject(commit, diff, task):
    return pipeline.Verdict(passed=False, authority="panel", reason="not convincing")


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = _make_vault(self.root)

    def _ctx(self, repo, *, calibration=False, soil_cycle=1):
        return soil_state.SoilContext.open(
            repo, generation="gen-0", soil_cycle=soil_cycle,
            calibration=calibration, vault=self.vault)

    def _ledger(self, ctx):
        return ctx.ledger.read()

    def _kinds(self, ctx):
        return [r["kind"] for r in self._ledger(ctx)]


class PromotionChainTests(PipelineTestCase):
    """晋升路径：四条事件 + 记分板全套，且字段逐一对应（CA-10）。"""

    def test_promoted_writes_full_transaction_chain(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)

        outcome = pipeline.process_candidate(candidate, _task(), repo=repo,
                                             panel=_accept, ctx=ctx)

        self.assertIs(outcome, pipeline.Outcome.PROMOTED)
        self.assertEqual(self._kinds(ctx),
                         ["observed_fitness", "promotion_intent",
                          "accepted_fitness", "promotion_committed"])
        rows = self._ledger(ctx)
        observed, intent, accepted = rows[0], rows[1], rows[2]

        # CA-10：事务链逐字段一一对应，不只是数量相等。
        self.assertEqual(intent["source"], observed["event_id"])
        self.assertEqual(accepted["source"], observed["event_id"])
        self.assertEqual(accepted["commit"], candidate)
        self.assertTrue(accepted["counts_as_progress"])
        self.assertFalse(accepted["calibration"])

        board = ctx.scoreboard.read()
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["commit"], candidate)
        self.assertEqual(board[0]["parent_sha"], intent["parent"])

        # 主线真的前进了 —— 判决位上是谁不改变晋升事务链（§12.0.2）。
        self.assertEqual(_git(repo, "rev-parse", "HEAD"), candidate)

    def test_promotion_is_an_ignition_event(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)
        pipeline.process_candidate(candidate, _task(), repo=repo, panel=_accept, ctx=ctx)

        hits = [ev for ev in self._ledger(ctx) if pipeline.is_ignition_event(ev)]
        self.assertEqual(len(hits), 1)
        record = next(r for r in hits[0]["records"] if r["probe_id"] == PROBE_ID)
        self.assertEqual((record["before"], record["after"]), (40.0, 60.0))


class NonPromotionOutcomeTests(PipelineTestCase):
    """§10.2 验收：六种情形各一例，外加「都不得产生 accepted_fitness」的负断言。"""

    def _run(self, *, table=None, organ_src=None, selftest=None, panel=_accept,
             task=None, calibration=False, stale=False):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=table, organ_src=organ_src,
                                    selftest=selftest)
        if stale:
            # 主线后来又前进了一步 -> 候选不再是 HEAD 的孩子。
            _write(repo / "unrelated.txt", "moved on")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "head moves")
        ctx = self._ctx(repo, calibration=calibration)
        outcome = pipeline.process_candidate(candidate, task or _task(), repo=repo,
                                             panel=panel, ctx=ctx)
        return ctx, outcome

    def _assert_nonpromotion(self, ctx, outcome, expected, *, quota):
        self.assertIs(outcome, expected)
        rows = self._ledger(ctx)
        final = rows[-1]
        # `kind` 恒为 promotion_outcome，枚举名住在 `outcome` 字段里
        # （§10 的「本轮勘误」：照旧表头实现会写出规格里不存在的 kind）。
        self.assertEqual(final["kind"], "promotion_outcome")
        self.assertEqual(final["outcome"], expected.name)
        self.assertIs(final["counts_as_progress"], False)
        self.assertIs(final["counts_against_task_quota"], quota)
        # 负断言：任何非晋升出口都不得产生 accepted_fitness。
        self.assertNotIn("accepted_fitness", [r["kind"] for r in rows])

    def test_regressed(self):
        ctx, outcome = self._run(table=KNOWS_ONE)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.REGRESSED, quota=True)

    def test_unfulfilled_when_delta_below_minimum(self):
        # 分数没动 -> 不是回归，但也没兑现声明的 +20。
        ctx, outcome = self._run(table=KNOWS_TWO_ALT)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.UNFULFILLED, quota=True)

    def test_rejected_by_panel(self):
        ctx, outcome = self._run(table=KNOWS_THREE, panel=_reject)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.REJECTED, quota=True)

    def test_canary_reject(self):
        ctx, outcome = self._run(table=KNOWS_THREE, selftest=SELFTEST_FAIL)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.CANARY_REJECT, quota=True)

    def test_unmeasured_when_organ_cannot_run(self):
        ctx, outcome = self._run(organ_src=CRASHING_ORGAN)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.UNMEASURED, quota=False)

    def test_stale_when_head_moved(self):
        ctx, outcome = self._run(table=KNOWS_THREE, stale=True)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.STALE, quota=False)

    def test_unmeasured_and_stale_do_not_count_against_quota(self):
        """机制/环境故障不计额度 —— 混淆两者会因为一次换 runner 版本
        就把任务判成「种子做坏了三次」并 parked。"""
        self.assertFalse(pipeline.COUNTS_AGAINST_QUOTA[pipeline.Outcome.UNMEASURED])
        self.assertFalse(pipeline.COUNTS_AGAINST_QUOTA[pipeline.Outcome.STALE])
        for outcome in (pipeline.Outcome.REGRESSED, pipeline.Outcome.UNFULFILLED,
                        pipeline.Outcome.REJECTED, pipeline.Outcome.CANARY_REJECT):
            self.assertTrue(pipeline.COUNTS_AGAINST_QUOTA[outcome])


class CalibrationTests(PipelineTestCase):
    """§12.0.1：校准强制回滚、永不 merge，**结构上产不出 accepted_fitness**。"""

    def test_calibration_measures_but_never_promotes(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        head_before = _git(repo, "rev-parse", "HEAD")
        ctx = self._ctx(repo, calibration=True)

        outcome = pipeline.process_candidate(candidate, _task(), repo=repo,
                                             panel=_accept, ctx=ctx)

        self.assertIs(outcome, pipeline.Outcome.CALIBRATION)
        rows = self._ledger(ctx)
        # 测到了 —— 这正是校准要回答的问题（土壤坏了还是种子弱）。
        self.assertIs(rows[0]["calibration"], True)
        record = next(r for r in rows[0]["records"] if r["probe_id"] == PROBE_ID)
        self.assertEqual(record["status"], "improved")
        # 但结构上到不了 accepted_fitness，主线一步没动。
        self.assertNotIn("accepted_fitness", [r["kind"] for r in rows])
        self.assertEqual(_git(repo, "rev-parse", "HEAD"), head_before)
        # 纵深的第二层：即便日后有人给校准开出一条 merge 的路，判据仍挡得住。
        self.assertEqual([ev for ev in rows if pipeline.is_ignition_event(ev)], [])


class IgnitionPredicateTests(unittest.TestCase):
    """§1.2 谓词与 §12.0.2 的 `excluded` 归因顺序。"""

    def _accepted(self, **overrides):
        event = {"kind": "accepted_fitness", "calibration": False,
                 "counts_as_progress": True, "primary_probe": PROBE_ID,
                 "task_id": "t", "generation": "gen-0", "soil_cycle": 1,
                 "records": [{"probe_id": PROBE_ID, "status": "improved"}]}
        event.update(overrides)
        return event

    def test_accepts_a_well_formed_ignition_event(self):
        self.assertTrue(pipeline.is_ignition_event(self._accepted()))
        self.assertIsNone(pipeline.ignition_exclusion_reason(self._accepted()))

    def test_anchor_rise_is_not_ignition(self):
        """anchor 上升不加分、不计入 improved（§12.2）——
        缺了这个合取项，anchor 就从外部锚变成第二把可挑的尺。"""
        event = self._accepted(records=[{"probe_id": "probe-anchor", "status": "improved"}])
        self.assertFalse(pipeline.is_ignition_event(event))
        self.assertEqual(pipeline.ignition_exclusion_reason(event), "primary_probe")

    def test_observed_but_unpromoted_is_not_ignition(self):
        event = self._accepted(kind="observed_fitness")
        self.assertFalse(pipeline.is_ignition_event(event))
        self.assertEqual(pipeline.ignition_exclusion_reason(event), "kind≠accepted_fitness")

    def test_calibration_is_never_counted(self):
        event = self._accepted(calibration=True)
        self.assertFalse(pipeline.is_ignition_event(event))

    def test_missing_mandatory_field_raises_rather_than_guessing(self):
        """§1.2：字段既已强制，缺键就是台账损坏，**应当当场抛错**，
        而不是由读者猜一个方向 —— `.get()` 在 calibration 与 counts_as_progress
        上的兜底方向是相反的。"""
        event = self._accepted()
        del event["calibration"]
        with self.assertRaises(KeyError):
            pipeline.is_ignition_event(event)

    def test_exclusion_attribution_reports_only_the_first_failing_conjunct(self):
        """归因顺序定死为 kind → calibration → counts_as_progress → primary_probe，
        **报第一个不满足的那一项**。顺序不定死，两次运行的 excluded 行就可能不一样，
        而这一行是拿来做处置判断的（H1 否证 vs 修土壤）。"""
        # 三项同时不满足，只应报最靠前的 calibration。
        event = self._accepted(calibration=True, counts_as_progress=False,
                               records=[{"probe_id": "other", "status": "improved"}])
        self.assertEqual(pipeline.ignition_exclusion_reason(event), "calibration")
        # 去掉 calibration 这一项，下一个才轮到 counts_as_progress。
        event = self._accepted(counts_as_progress=False,
                               records=[{"probe_id": "other", "status": "improved"}])
        self.assertEqual(pipeline.ignition_exclusion_reason(event), "counts_as_progress")

    def test_conjunct_order_constant_matches_spec_order(self):
        self.assertEqual(pipeline.IGNITION_CONJUNCTS,
                         ("kind", "calibration", "counts_as_progress", "primary_probe"))


class ManualPanelParityTests(PipelineTestCase):
    """CA-11：同一候选下，manual accept 与 panel accept 必须产生
    **逐字相同的事件序列**，仅 `verdict.authority` 取值不同（§12.0.2）。"""

    VOLATILE = ("ts", "event_id", "source")

    def _sequence(self, panel, subdir):
        root = self.root / subdir
        root.mkdir()
        repo = _make_repo(root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = soil_state.SoilContext.open(repo, generation="gen-0", soil_cycle=1,
                                          vault=self.vault)
        outcome = pipeline.process_candidate(candidate, _task(), repo=repo,
                                             panel=panel, ctx=ctx)
        self.assertIs(outcome, pipeline.Outcome.PROMOTED)
        return [{k: v for k, v in row.items() if k not in self.VOLATILE}
                for row in ctx.ledger.read()]

    def test_manual_and_panel_sequences_differ_only_in_authority(self):
        panel_rows = self._sequence(_accept, "panel-run")
        manual_rows = self._sequence(_accept_manual, "manual-run")

        self.assertEqual(len(panel_rows), len(manual_rows))
        differences = []
        for left, right in zip(panel_rows, manual_rows):
            self.assertEqual(left.keys(), right.keys())
            for key in left:
                if left[key] != right[key]:
                    differences.append(key)
        self.assertEqual(differences, ["verdict_authority"])
        self.assertEqual(
            [row.get("verdict_authority") for row in panel_rows if "verdict_authority" in row],
            ["panel"])
        self.assertEqual(
            [row.get("verdict_authority") for row in manual_rows if "verdict_authority" in row],
            ["manual"])


class ReconcileTests(PipelineTestCase):
    """§10.2：有 `promotion_intent` 而无 `promotion_committed` 的收尾。

    崩溃恢复要到 P0-c 无人值守时才第一次被真正需要 —— 那时它从未被跑过一次。
    """

    def test_reconcile_completes_a_promotion_main_already_contains(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)

        # 手工重演「merge 完成、accepted_fitness 尚未写入」的崩溃现场。
        oid = ctx.ledger.append({
            "kind": "observed_fitness", "commit": candidate, "source": None,
            "records": [{"probe_id": PROBE_ID, "before": 40.0, "after": 60.0,
                         "delta": 20.0, "status": "improved", "checks_before": 2,
                         "checks_after": 3, "checks_total": 5, "measured_by": "soil",
                         "tree_before": "a", "tree_after": "b",
                         "probe_manifest_sha": "m", "runner_version": "1",
                         "execution_policy_version": "1"}],
            "task_id": "task-test", "primary_probe": PROBE_ID, "generation": "gen-0",
            "soil_cycle": 1, "calibration": False, "counts_as_progress": False})
        parent = _git(repo, "rev-parse", "HEAD")
        ctx.ledger.append({"kind": "promotion_intent", "commit": candidate,
                           "parent": parent, "source": oid, "state": "pending",
                           "verdict_authority": "manual"})
        _git(repo, "merge", "--ff-only", "-q", candidate)

        resolved = pipeline.reconcile_on_start(repo, ctx)

        self.assertEqual(resolved, [(candidate, pipeline.Outcome.PROMOTED)])
        kinds = self._kinds(ctx)
        self.assertEqual(kinds[-2:], ["accepted_fitness", "promotion_committed"])
        accepted = self._ledger(ctx)[-2]
        # 身份字段从当时那条 observed 抄回，不从当前 ctx 取。
        self.assertEqual(accepted["soil_cycle"], 1)
        self.assertEqual(accepted["source"], oid)
        self.assertTrue(pipeline.is_ignition_event(accepted))

    def test_reconcile_abandons_when_main_does_not_contain_the_commit(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)
        parent = _git(repo, "rev-parse", "HEAD")
        ctx.ledger.append({"kind": "promotion_intent", "commit": candidate,
                           "parent": parent, "source": "ev-missing", "state": "pending",
                           "verdict_authority": "manual"})

        resolved = pipeline.reconcile_on_start(repo, ctx)

        self.assertEqual(resolved, [(candidate, pipeline.Outcome.ABANDONED)])
        final = self._ledger(ctx)[-1]
        self.assertEqual(final["kind"], "promotion_outcome")
        self.assertEqual(final["outcome"], "ABANDONED")
        self.assertNotIn("accepted_fitness", self._kinds(ctx))

    def test_reconcile_does_not_duplicate_accepted_fitness_after_a_late_crash(self):
        """崩溃落在 `accepted_fitness` 与 `promotion_committed` **之间**。

        这是一行的窗口，也**正是 reconcile 存在的理由**；而上一版只看
        `promotion_committed` 在不在，于是把「差最后一条」当成「什么都没写」，
        补写出第二条 `accepted_fitness` —— 同一次晋升被 §1.2 数成**两次点火**。
        CA-10 当时也抓不到：它只验每条 accepted 都配得上 intent/committed/scoreboard，
        **从不验基数**，而重复条目每一项都配得上。

        §1.2 的计数是整个 P0-a 的唯一出口。**多数一次，就是宣布一次没发生的点火。**
        """
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)
        pipeline.process_candidate(candidate, _task(), repo=repo, panel=_accept, ctx=ctx)

        # 砍掉收尾那一条，重演崩溃现场。
        lines = ctx.ledger.path.read_text(encoding="utf-8").splitlines()
        self.assertIn("promotion_committed", lines[-1])
        ctx.ledger.path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        scoreboard_before = len(ctx.scoreboard.read())

        resolved = pipeline.reconcile_on_start(repo, ctx)

        self.assertEqual(resolved, [(candidate, pipeline.Outcome.PROMOTED)])
        rows = self._ledger(ctx)
        self.assertEqual(sum(1 for r in rows if r["kind"] == "accepted_fitness"), 1)
        self.assertEqual(sum(1 for r in rows if pipeline.is_ignition_event(r)), 1)
        # 记分板同理：补写不得再写一遍全套。
        self.assertEqual(len(ctx.scoreboard.read()), scoreboard_before)
        self.assertEqual(rows[-1]["kind"], "promotion_committed")

    def test_reconcile_reports_soil_recovery_when_the_commit_cannot_be_resolved(self):
        """`git merge-base --is-ancestor` 对解析不了的名字退 **128**，不是 1。

        按 `!= 0` 一律当「不是祖先」，会把**判定不了**报成 `ABANDONED` ——
        一个确信的否定。规格反复强调这两者对应完全不同的处置。
        """
        repo = _make_repo(self.root)
        ctx = self._ctx(repo)
        ctx.ledger.append({"kind": "promotion_intent", "commit": "0" * 40,
                           "parent": _git(repo, "rev-parse", "HEAD"),
                           "source": "ev-missing", "state": "pending",
                           "verdict_authority": "manual"})

        resolved = pipeline.reconcile_on_start(repo, ctx)

        self.assertEqual([o for _, o in resolved], [pipeline.Outcome.SOIL_RECOVERY])
        self.assertEqual(self._ledger(ctx)[-1]["outcome"], "SOIL_RECOVERY")

    def test_reconcile_refuses_to_launder_a_calibration_run(self):
        """校准结构上到不了 `promotion_intent`。真到了，说明有人开了一条 merge 的路 ——
        **不得把它洗成一条 `calibration:false` 的 `accepted_fitness`**。"""
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo, calibration=True)
        oid = ctx.ledger.append({
            "kind": "observed_fitness", "commit": candidate, "source": None,
            "records": [{"probe_id": PROBE_ID, "before": 40.0, "after": 60.0,
                         "delta": 20.0, "status": "improved"}],
            "task_id": "task-test", "primary_probe": PROBE_ID, "generation": "gen-0",
            "soil_cycle": 1, "calibration": True, "counts_as_progress": False})
        parent = _git(repo, "rev-parse", "HEAD")
        ctx.ledger.append({"kind": "promotion_intent", "commit": candidate,
                           "parent": parent, "source": oid, "state": "pending",
                           "verdict_authority": "manual"})
        _git(repo, "merge", "--ff-only", "-q", candidate)

        resolved = pipeline.reconcile_on_start(repo, ctx)

        self.assertEqual([o for _, o in resolved], [pipeline.Outcome.SOIL_RECOVERY])
        self.assertNotIn("accepted_fitness", self._kinds(ctx))

    def test_reconcile_is_a_noop_when_the_chain_is_complete(self):
        repo = _make_repo(self.root)
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)
        pipeline.process_candidate(candidate, _task(), repo=repo, panel=_accept, ctx=ctx)
        before = self._ledger(ctx)

        self.assertEqual(pipeline.reconcile_on_start(repo, ctx), [])
        self.assertEqual(self._ledger(ctx), before)


class TaskDeclarationTests(PipelineTestCase):
    """§8.1.4 的硬约束：土壤在校验声明时执行，违反即拒绝。"""

    def setUp(self):
        super().setUp()
        self.repo = _make_repo(self.root)
        self.ctx = self._ctx(self.repo)

    def test_anchor_cannot_be_declared_primary_probe(self):
        task = pipeline.Task(task_id="t", kind="repair", target="classifier",
                             primary_probe="probe-anchor-hidden")
        with self.assertRaises(pipeline.TaskDeclarationError):
            pipeline.validate_task(task, self.ctx)

    def test_regression_policy_is_not_the_seeds_to_change(self):
        task = pipeline.Task(task_id="t", kind="repair", target="classifier",
                             primary_probe=PROBE_ID, regression_policy="only-my-probe")
        with self.assertRaises(pipeline.TaskDeclarationError):
            pipeline.validate_task(task, self.ctx)

    def test_diverted_task_kinds_are_refused_at_the_pipeline_entrance(self):
        """特殊任务在进入 process_candidate 之前就分流（§10.2）。"""
        for expected in ("refusal_with_reason", "no_measurement"):
            with self.assertRaises(ValueError):
                pipeline.process_candidate("HEAD", _task(expected=expected),
                                           repo=self.repo, panel=_accept, ctx=self.ctx)

    def test_cost_reduction_fails_closed_without_a_metric_registry(self):
        """§17.2 的 Metric Registry 本轮未实现 —— **不假装兑现**。"""
        ok, why = pipeline.evaluate_task(_task(expected="cost_reduction"), [])
        self.assertFalse(ok)
        self.assertIn("Metric Registry", why)


class SoilStateTests(unittest.TestCase):
    """§8.1.5 前缀族 + §8.2 六个强制字段，在**写入侧**拒绝。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()

    def test_filename_must_match_the_prefix_family(self):
        for bad in ("ledger.jsonl", "soil-ledger.jsonl.bak", "soil-ledger.jsonl.tmp",
                    "soil-Ledger.jsonl", "soil-ledger.json"):
            with self.subTest(bad=bad):
                with self.assertRaises(soil_state.SoilStateError):
                    soil_state.Ledger(self.state / bad)

    def test_fitness_event_missing_a_mandatory_field_is_refused(self):
        ledger = soil_state.Ledger(self.state / "soil-ledger.jsonl")
        for field in soil_state.MANDATORY_FITNESS_FIELDS:
            event = {"kind": "accepted_fitness", "commit": "c", "records": [],
                     "task_id": "t", "primary_probe": PROBE_ID, "generation": "gen-0",
                     "soil_cycle": 1, "calibration": False, "counts_as_progress": True}
            del event[field]
            with self.subTest(field=field):
                with self.assertRaises(soil_state.SoilStateError):
                    ledger.append(event)
        self.assertEqual(ledger.read(), [])

    def test_non_fitness_events_do_not_need_the_six_fields(self):
        ledger = soil_state.Ledger(self.state / "soil-ledger.jsonl")
        ledger.append({"kind": "promotion_committed", "commit": "c"})
        self.assertEqual(len(ledger.read()), 1)

    def test_vault_has_no_relative_path_default(self):
        """C-65：读不到 MERISTEM_VAULT 就拒绝运行，**不猜路径**。"""
        saved = os.environ.pop("MERISTEM_VAULT", None)
        try:
            with self.assertRaises(soil_state.SoilStateError):
                soil_state.resolve_vault()
        finally:
            if saved is not None:
                os.environ["MERISTEM_VAULT"] = saved


if __name__ == "__main__":
    unittest.main()
