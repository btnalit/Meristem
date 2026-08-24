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
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate import pipeline  # noqa: E402
from substrate import probe_runner  # noqa: E402
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


def _record(**overrides) -> dict:
    """一条符合 §8.2 `records[]` schema 的 fitness 记录。

    测试夹具必须**带齐三个版本维度** —— 它们是可比性的前提，写入侧现在会拒绝
    缺了它们的记录。上一版这里手写了一条精简记录，恰好被新校验抓住：
    **夹具偷的懒，就是断言覆盖不到的那块地方。**
    """
    record = {"probe_id": PROBE_ID, "before": 40.0, "after": 60.0, "delta": 20.0,
              "status": "improved", "checks_before": 2, "checks_after": 3,
              "checks_total": 5, "measured_by": "soil",
              "tree_before": "sha-before", "tree_after": "sha-after",
              "probe_manifest_sha": "sha-manifest", "runner_version": "1",
              "execution_policy_version": "1"}
    record.update(overrides)
    return record


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


def _preflight_reject(commit, diff, task):
    return pipeline.Verdict(passed=False, authority="manual",
                            reason="H1-preflight: promotion disabled")


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = _make_vault(self.root)

    def _ctx(self, repo, *, calibration=False, soil_cycle=1):
        ctx = soil_state.SoilContext.open(
            repo, generation="gen-0", soil_cycle=soil_cycle,
            calibration=calibration, vault=self.vault)
        self._ensure_frozen(ctx)
        return ctx

    def _ensure_frozen(self, ctx, probe_id=PROBE_ID):
        """让 `probe_id` 在这个 ctx 眼里已经 `active` 且早已过 `eligible_after`
        （C1，§15）——`_make_vault()` 把 manifest 直接写进 vault，绕过
        `pipeline.freeze_proposal()`，从不touch冻结登记。**这个模块里除了
        C1 自己的测试类之外，其余测试类验的都是别的机制**（晋升事务链 /
        非晋升出口 / 校准 / manual-panel 对等 / 崩溃收尾），不该被 C1 的
        时机闸门挡住，所以这里补一条「早已冻结、早已过期」的登记，
        `soil_cycle=0` 保证严格小于任何真实拍号（拍号从 1 起算，
        见 `substrate/supervisor.py::_next_soil_cycle`）。

        C1 自己的测试类（`FreezeProposalTests` / `TaskDeclarationTests` 里
        新增的用例）不经过这个助手——它们要测的正是「还没到 eligible_after」
        与「冻结与登记同一个 ctx」这两种情形，用真实的 `freeze_proposal()`
        或手工摆一条未到期的登记，不能被这里的默认放行悄悄盖过。
        """
        if ctx.frozen_registry.get(probe_id) is not None:
            return
        ctx.frozen_registry.freeze({
            "probe_id": probe_id, "status": "active", "created_by": "seed",
            "proposed_commit": "0" * 40, "frozen_tree_sha": "0" * 40,
            "frozen_probe_manifest_sha": "fixture-manifest-sha-not-checked",
            "eligible_after": {"generation": ctx.generation, "soil_cycle": 0},
        })

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

    def test_preflight_gate_is_not_rejection_quota(self):
        ctx, outcome = self._run(table=KNOWS_THREE, panel=_preflight_reject)
        self._assert_nonpromotion(ctx, outcome, pipeline.Outcome.PREFLIGHT_GATED, quota=False)

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
        self.assertFalse(pipeline.COUNTS_AGAINST_QUOTA[pipeline.Outcome.PREFLIGHT_GATED])
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
        self._ensure_frozen(ctx)
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
        self.assertEqual(set(differences), {"verdict_authority", "attempt_id"})
        self.assertEqual(differences.count("verdict_authority"), 1)
        self.assertEqual(differences.count("attempt_id"), 4)
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
            "records": [_record()],
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

    def test_anchor_present_in_the_measured_set_is_still_refused_as_primary(self):
        """**这一条以前是靠 anchor 缺席才通过的。**

        旧写法是「在清单里找不到就拒绝」，而当时的清单根本不含 anchor ——
        碰巧奏效。§12.2 的「anchor 回归即拒」要求 anchor 必须真的在被测集合里；
        它一进去，靠缺席的判别就**静默失效**，种子从此可以拿外部锚当自己的尺。
        现在按来源目录判别，anchor 在场也照样被拒。
        """
        anchor_dir = self.vault / "anchors" / "probe-anchor-x"
        anchor_dir.mkdir(parents=True)
        _write(anchor_dir / "probe.json", json.dumps({
            "id": "probe-anchor-x", "capability": "c", "organ": "classifier",
            "checks": CHECKS}, ensure_ascii=False))

        # 前提：anchor 确实进了被测集合（否则这条测试又在测缺席）。
        ids = {m["id"] for m in probe_runner.catalogue(self.vault)}
        self.assertIn("probe-anchor-x", ids)
        self.assertIn(PROBE_ID, ids)

        task = pipeline.Task(task_id="t", kind="repair", target="classifier",
                             primary_probe="probe-anchor-x")
        with self.assertRaises(pipeline.TaskDeclarationError) as caught:
            pipeline.validate_task(task, self.ctx)
        self.assertIn("anchor", str(caught.exception))

    def test_anchor_regression_rejects_the_candidate(self):
        """§12.2 的非对称第一半：**anchor 回归 → 候选被拒**。

        这一条不新增机制 —— `run_all` 本就跑全套、`has_regression` 对任一 probe
        触发。它断言的是「anchor 确实在那个『全套』里」，而那正是接线之前不成立的事。
        """
        anchor_dir = self.vault / "anchors" / "probe-anchor-x"
        anchor_dir.mkdir(parents=True)
        # anchor 用 internal 覆盖不到的输入取样。
        _write(anchor_dir / "probe.json", json.dumps({
            "id": "probe-anchor-x", "capability": "c", "organ": "classifier",
            "checks": [{"id": "x1", "input": "anchor only sample",
                        "cmp": "equals", "expect": "anchor-label"}]}, ensure_ascii=False))
        # 本类的 setUp 已经建过一个 repo，这里另起一处，避免撞名。
        elsewhere = self.root / "anchor-regression"
        elsewhere.mkdir()
        repo = _make_repo(elsewhere, table=dict(KNOWS_TWO,
                                                **{"anchor only sample": "anchor-label"}))
        # 候选修好了 internal 的一条，却把 anchor 那条弄坏了。
        candidate = _make_candidate(repo, table=KNOWS_THREE)
        ctx = self._ctx(repo)

        outcome = pipeline.process_candidate(candidate, _task(), repo=repo,
                                             panel=_accept, ctx=ctx)

        self.assertIs(outcome, pipeline.Outcome.REGRESSED)
        self.assertNotIn("accepted_fitness", [r["kind"] for r in ctx.ledger.read()])

    def test_syntax_preflight_rejects_invalid_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            self.assertFalse(pipeline._syntax_preflight(tree))
            (tree / "bad.py").write_text("def okay():\n    return 1\n", encoding="utf-8")
            self.assertTrue(pipeline._syntax_preflight(tree))

    def test_task_path_contract_rejects_forbidden_and_requires_target(self):
        task = pipeline.Task(task_id="t", kind="repair", target="classifier",
                             primary_probe=PROBE_ID,
                             forbidden_paths=("tests/",),
                             required_target_paths=("body/organs/classifier/",))
        self.assertTrue("forbidden_paths" in (pipeline._task_path_violation(
            ["tests/test_x.py", "body/organs/classifier/run.py"], task) or ""))
        self.assertTrue("missing_required_target_paths" in (pipeline._task_path_violation(
            ["body/organs/other/run.py"], task) or ""))
        self.assertIsNone(pipeline._task_path_violation(
            ["body/organs/classifier/run.py"], task))

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


class C1EnforcementTests(PipelineTestCase):
    """C1（§15 / §8.1.4 硬约束表第二行）：`validate_task()` 对 `primary_probe`
    的 `active` + `eligible_after` 校验。

    **不用 `self._ctx()`。** 那个助手替这个文件里其它所有测试类把 PROBE_ID
    摆成「早已冻结、早已过期」（见 `PipelineTestCase._ensure_frozen` 的
    docstring），而这里要测的正是「尚未过期」「非 active」这些情形本身，
    必须自己控制登记内容，不能被默认放行盖过。
    """

    def _ctx_with_registry(self, repo, entry_overrides, *, soil_cycle=1):
        ctx = soil_state.SoilContext.open(
            repo, generation="gen-0", soil_cycle=soil_cycle, vault=self.vault)
        entry = {"probe_id": PROBE_ID, "status": "active", "created_by": "seed",
                 "proposed_commit": "0" * 40, "frozen_tree_sha": "0" * 40,
                 "frozen_probe_manifest_sha": "sha-fixture",
                 "eligible_after": {"generation": "gen-0", "soil_cycle": 1}}
        entry.update(entry_overrides)
        ctx.frozen_registry.freeze(entry)
        return ctx

    def test_probe_frozen_in_the_same_cycle_as_its_capability_is_rejected(self):
        """核心场景（Constraints 第 1 条）：登记的 `eligible_after.soil_cycle`
        与本次 ctx 的 `soil_cycle` 相等——「同一个 Change 里先写尺、再写刚好
        通过这把尺的能力」，C1 明令禁止，必须拒绝。"""
        repo = _make_repo(self.root)
        ctx = self._ctx_with_registry(
            repo, {"eligible_after": {"generation": "gen-0", "soil_cycle": 1}},
            soil_cycle=1)
        with self.assertRaises(pipeline.TaskDeclarationError) as cm:
            pipeline.validate_task(_task(), ctx)
        self.assertIn("eligible_after", str(cm.exception))

    def test_probe_becomes_eligible_once_an_independent_cycle_has_passed(self):
        repo = _make_repo(self.root)
        ctx = self._ctx_with_registry(
            repo, {"eligible_after": {"generation": "gen-0", "soil_cycle": 1}},
            soil_cycle=2)
        pipeline.validate_task(_task(), ctx)  # 不抛

    def test_probe_not_yet_active_is_rejected(self):
        """`freeze()` 只接受 `status="active"`（首次冻结转移），要构造「非
        active」得在写入之后直接改磁盘内容——模拟 I3 退役路径已经把它标成
        `degenerate_suspected` 之后的状态（状态迁移本身不在本任务范围内，
        见 `FrozenProbeRegistry` 类 docstring）。"""
        repo = _make_repo(self.root)
        ctx = self._ctx_with_registry(
            repo, {"eligible_after": {"generation": "gen-0", "soil_cycle": 0}},
            soil_cycle=1)
        data = json.loads(ctx.frozen_registry.path.read_text(encoding="utf-8"))
        data[PROBE_ID]["status"] = "degenerate_suspected"
        ctx.frozen_registry.path.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(pipeline.TaskDeclarationError) as cm:
            pipeline.validate_task(_task(), ctx)
        self.assertIn("active", str(cm.exception))

    def test_generation_mismatch_is_treated_as_not_eligible(self):
        """规格未言明世代变化（§7.1 soil_recovery：「冻结自主运行，等待重新
        点火」）之后旧 `soil_cycle` 是否仍可比；本实现按 §4.1 Fitness 三维度
        配对同一条纪律 fail closed。这条测试把这个判断钉死，避免以后有人
        「顺手」改成只比 `soil_cycle`、不比 `generation`。"""
        repo = _make_repo(self.root)
        ctx = self._ctx_with_registry(
            repo, {"eligible_after": {"generation": "gen-earlier", "soil_cycle": 0}},
            soil_cycle=5)
        with self.assertRaises(pipeline.TaskDeclarationError):
            pipeline.validate_task(_task(), ctx)

    def test_missing_registry_entry_is_rejected_even_if_vault_has_the_manifest(self):
        """vault 与登记不一致（例如登记条目绕开 `freeze_into_vault()` 手工
        放进 vault）时 fail closed，不是「找不到登记就当成没有这层约束」。"""
        repo = _make_repo(self.root)
        ctx = soil_state.SoilContext.open(
            repo, generation="gen-0", soil_cycle=1, vault=self.vault)
        # 刻意不写登记——self.vault（PipelineTestCase.setUp）已经直接把
        # PROBE_ID 的 manifest 放进了 vault。
        with self.assertRaises(pipeline.TaskDeclarationError) as cm:
            pipeline.validate_task(_task(), ctx)
        self.assertIn("冻结登记", str(cm.exception))


class FrozenProbeRegistryTests(unittest.TestCase):
    """`soil_state.FrozenProbeRegistry` —— C1 冻结登记的持久层（§15 C1）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.soil = Path(self.tmp.name) / "soil"

    def _entry(self, **overrides):
        entry = {"probe_id": "probe-r", "status": "active", "created_by": "seed",
                 "proposed_commit": "c" * 40, "frozen_tree_sha": "t" * 40,
                 "frozen_probe_manifest_sha": "m" * 64,
                 "eligible_after": {"generation": "gen-0", "soil_cycle": 3}}
        entry.update(overrides)
        return entry

    def _registry(self):
        return soil_state.FrozenProbeRegistry(self.soil / "frozen-probe-registry.json")

    def test_read_on_missing_file_returns_empty_dict(self):
        registry = self._registry()
        self.assertEqual(registry.read(), {})
        self.assertIsNone(registry.get("probe-r"))

    def test_freeze_then_get_round_trips(self):
        registry = self._registry()
        written = registry.freeze(self._entry())
        self.assertEqual(written["probe_id"], "probe-r")
        self.assertIn("frozen_at", written)
        self.assertEqual(registry.get("probe-r"), written)

    def test_freeze_persists_across_new_instances(self):
        """真实场景是跨进程 / 跨 ctx 复用同一份文件——不能只在同一个对象里
        「看起来」持久。"""
        self._registry().freeze(self._entry())
        reopened = self._registry()
        self.assertEqual(reopened.get("probe-r")["probe_id"], "probe-r")

    def test_freeze_refuses_duplicate_probe_id(self):
        registry = self._registry()
        registry.freeze(self._entry())
        with self.assertRaises(soil_state.SoilStateError):
            registry.freeze(self._entry())
        self.assertEqual(len(registry.read()), 1)  # 拒绝不得改动已有内容

    def test_freeze_refuses_missing_field(self):
        registry = self._registry()
        for field in soil_state.FROZEN_REGISTRY_REQUIRED_ON_INPUT:
            entry = self._entry()
            del entry[field]
            with self.subTest(field=field):
                with self.assertRaises(soil_state.SoilStateError):
                    registry.freeze(entry)
        self.assertEqual(registry.read(), {})

    def test_freeze_refuses_malformed_eligible_after(self):
        registry = self._registry()
        for bad in ({"generation": "gen-0"},                        # 缺 soil_cycle
                    {"soil_cycle": 3},                               # 缺 generation
                    {"generation": "", "soil_cycle": 3},              # generation 空串
                    {"generation": "gen-0", "soil_cycle": "3"},       # 类型不对
                    {"generation": "gen-0", "soil_cycle": True},      # bool 不算 int
                    "not-a-dict"):
            with self.subTest(bad=bad):
                with self.assertRaises(soil_state.SoilStateError):
                    registry.freeze(self._entry(eligible_after=bad))

    def test_freeze_only_accepts_active_status(self):
        """`freeze()` 只做首次冻结（§6.2 的 draft -> active），不支持直接
        写入其它状态——状态迁移不在 C1 本次交付范围内（类 docstring 已注明
        这是刻意留空，不是遗漏）。"""
        registry = self._registry()
        with self.assertRaises(soil_state.SoilStateError):
            registry.freeze(self._entry(status="degenerate_suspected"))


class FreezeProposalTests(unittest.TestCase):
    """`pipeline.freeze_proposal()` —— C1（§15）：把 `seed/probe-proposals/`
    里的提案冻结进 vault + 登记，一次搭一把锁完成。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # 刻意不用模块顶部的 _make_vault()：那份 fixture 直接把 PROBE_ID 的
        # manifest 写进 vault，绕过 freeze_into_vault()，是给「已有测量环境」
        # 的测试类（PipelineTestCase 及其子类）准备的起点。这里要测的是
        # **冻结本身**，起点必须是一个还没冻结过任何 probe 的空 vault——
        # 但 resolve_vault()（C-65）要求路径已经是目录，所以仍要 mkdir。
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def _ctx(self, repo, soil_cycle=1, generation="gen-0"):
        return soil_state.SoilContext.open(
            repo, generation=generation, soil_cycle=soil_cycle, vault=self.vault)

    def _proposal_path(self, repo, probe_id=PROBE_ID, checks=None, extra=None):
        path = repo / "seed" / "probe-proposals" / f"{probe_id}.json"
        manifest = {"id": probe_id, "capability": "把失败原因分类到命名类别",
                    "organ": "classifier",
                    "checks": checks if checks is not None else CHECKS}
        manifest.update(extra or {})
        _write(path, json.dumps(manifest, ensure_ascii=False))
        return path

    def test_freeze_writes_vault_and_registry_with_matching_manifest_sha(self):
        """核心断言（Constraints 第 3 条）：`frozen_probe_manifest_sha` 真的
        等于 runner 测量时算出的 `probe_manifest_sha`——不是假设相等，是冻结
        之后再真的从 vault 读回、跑一次测量核对出来的。"""
        repo = _make_repo(self.root)
        proposal_path = self._proposal_path(repo)
        ctx = self._ctx(repo, soil_cycle=1)
        commit = _git(repo, "rev-parse", "HEAD")

        entry = pipeline.freeze_proposal(proposal_path, ctx=ctx, proposed_commit=commit)

        self.assertEqual(entry["probe_id"], PROBE_ID)
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["created_by"], "seed")
        self.assertEqual(entry["proposed_commit"], commit)
        self.assertEqual(entry["frozen_tree_sha"],
                         _git(repo, "rev-parse", f"{commit}^{{tree}}"))
        self.assertEqual(entry["eligible_after"],
                         {"generation": "gen-0", "soil_cycle": 1})
        self.assertIn("frozen_at", entry)
        self.assertEqual(ctx.frozen_registry.get(PROBE_ID), entry)

        manifests = probe_runner.catalogue(ctx.vault)
        [manifest] = [m for m in manifests if m["id"] == PROBE_ID]
        run = probe_runner.run_probe(manifest, repo)
        self.assertEqual(run.probe_manifest_sha, entry["frozen_probe_manifest_sha"])

    def test_freeze_refuses_fewer_than_five_checks(self):
        """I2（Constraints 第 2 条）：冻结点拒绝 <5 个 check 的提案，且不留下
        任何部分写入的痕迹。"""
        repo = _make_repo(self.root)
        proposal_path = self._proposal_path(repo, checks=CHECKS[:4])
        ctx = self._ctx(repo, soil_cycle=1)
        commit = _git(repo, "rev-parse", "HEAD")

        with self.assertRaises(probe_runner.ProbeProposalError):
            pipeline.freeze_proposal(proposal_path, ctx=ctx, proposed_commit=commit)

        self.assertEqual(probe_runner.catalogue(self.vault), [])
        self.assertIsNone(ctx.frozen_registry.get(PROBE_ID))

    def test_freeze_refuses_entrypoint_field(self):
        repo = _make_repo(self.root)
        proposal_path = self._proposal_path(
            repo, extra={"entrypoint": ["python3", "hack.py"]})
        ctx = self._ctx(repo, soil_cycle=1)
        commit = _git(repo, "rev-parse", "HEAD")

        with self.assertRaises(probe_runner.ProbeProposalError):
            pipeline.freeze_proposal(proposal_path, ctx=ctx, proposed_commit=commit)
        self.assertEqual(probe_runner.catalogue(self.vault), [])

    def test_freeze_refuses_duplicate_probe_id(self):
        repo = _make_repo(self.root)
        proposal_path = self._proposal_path(repo)
        ctx = self._ctx(repo, soil_cycle=1)
        commit = _git(repo, "rev-parse", "HEAD")
        pipeline.freeze_proposal(proposal_path, ctx=ctx, proposed_commit=commit)

        with self.assertRaises(probe_runner.ProbeProposalError):
            pipeline.freeze_proposal(proposal_path, ctx=ctx, proposed_commit=commit)

    def test_freeze_then_validate_task_in_the_same_cycle_is_rejected(self):
        """端到端（Constraints 第 1 条）：同一个 Change（同一个 soil_cycle）
        里先冻结、再声明 `primary_probe`——C1 要防的攻击本身，必须拒绝。"""
        repo = _make_repo(self.root)
        proposal_path = self._proposal_path(repo)
        ctx = self._ctx(repo, soil_cycle=1)
        commit = _git(repo, "rev-parse", "HEAD")
        pipeline.freeze_proposal(proposal_path, ctx=ctx, proposed_commit=commit)

        task = pipeline.Task(task_id="t", kind="repair", target="classifier",
                             primary_probe=PROBE_ID)
        with self.assertRaises(pipeline.TaskDeclarationError):
            pipeline.validate_task(task, ctx)

    def test_freeze_then_validate_task_one_cycle_later_is_accepted(self):
        """同一场景，隔了一个独立 cycle（新的 ctx，`soil_cycle` 前进一格）
        ——通过。"""
        repo = _make_repo(self.root)
        proposal_path = self._proposal_path(repo)
        ctx1 = self._ctx(repo, soil_cycle=1)
        commit = _git(repo, "rev-parse", "HEAD")
        pipeline.freeze_proposal(proposal_path, ctx=ctx1, proposed_commit=commit)

        ctx2 = self._ctx(repo, soil_cycle=2)
        task = pipeline.Task(task_id="t", kind="repair", target="classifier",
                             primary_probe=PROBE_ID)
        pipeline.validate_task(task, ctx2)  # 不抛


class LedgerReaderContractTests(unittest.TestCase):
    """写入侧接受的每一行，判据侧都必须读得出来。

    2026-08-23 第三份独立审查复现：`records: [{}]` 能写进台账，而
    `is_ignition_event` 读到它抛 `KeyError` —— **判据的唯一求值点当场崩溃**。
    一个写得进去、却读不出来的台账行，等于把崩溃留给最需要读数的那一刻
    （崩溃恢复、事后审计）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        self.ledger = soil_state.Ledger(state / "soil-ledger.jsonl")

    def _event(self, records):
        return {"kind": "accepted_fitness", "commit": "c", "source": "ev-x",
                "task_id": "t", "primary_probe": PROBE_ID, "generation": "gen-0",
                "soil_cycle": 1, "calibration": False, "counts_as_progress": True,
                "records": records}

    def test_empty_record_is_refused_at_write_time(self):
        with self.assertRaises(soil_state.SoilStateError):
            self.ledger.append(self._event([{}]))
        self.assertEqual(self.ledger.read(), [])

    def test_record_without_version_dimensions_is_refused(self):
        """三个版本维度缺失会让不可比**静默发生** —— 规格叫它「又一个声明了没断言」。"""
        thin = {k: v for k, v in _record().items()
                if k not in ("probe_manifest_sha", "runner_version",
                             "execution_policy_version")}
        with self.assertRaises(soil_state.SoilStateError):
            self.ledger.append(self._event([thin]))

    def test_record_with_status_outside_the_i5_enum_is_refused(self):
        with self.assertRaises(soil_state.SoilStateError):
            self.ledger.append(self._event([_record(status="looks-good")]))

    def test_everything_the_writer_accepts_the_predicate_can_read(self):
        """正向断言：写得进去的，判据一定读得出来（不抛异常）。"""
        self.ledger.append(self._event([_record()]))
        for row in self.ledger.read():
            self.assertIsInstance(pipeline.is_ignition_event(row), bool)
            pipeline.ignition_exclusion_reason(row)

    def test_baseline_and_unmeasured_records_remain_writable(self):
        """`pair()` 在 baseline / unmeasured 下合法地产出 `None` ——
        校验不能严到让 runner 写不出自己的记录。"""
        self.ledger.append(self._event([_record(before=None, delta=None,
                                                checks_before=None, status="baseline")]))
        self.ledger.append(self._event([_record(delta=None, status="unmeasured")]))
        self.assertEqual(len(self.ledger.read()), 2)


class LegacyEntryPointTests(unittest.TestCase):
    """v3.1 的运行入口**已删除**，不是加了闸。

    上一版给它们加默认拒绝、并留了 `MERISTEM_ALLOW_LEGACY=1` 后门。
    **一个加了锁的第二入口仍然是第二入口** —— 而这个项目的规矩是
    「同一件事只能有一个权威判定入口」；那个后门存在的唯一理由是 v3.1，
    而 v3.1 已在 §13 清盘里判了死刑。§13.3 写的处置本就是
    「整段删除」/「重写或删除」，不是「关起来」。
    """

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from substrate import supervisor
        self.supervisor = supervisor

    def test_only_two_v5_commands_exist(self):
        parser_commands = self.supervisor.main.__doc__  # 占位，真正断言在下面
        for command in ("run", "promote", "rollback", "canary", "heartbeat"):
            with self.subTest(command=command):
                # argparse 对不在 choices 里的值退 2 并打印 usage —— 命令**不存在**，
                # 而不是「存在但被拒绝」。两者对读者是完全不同的信息。
                with self.assertRaises(SystemExit):
                    self.supervisor.main([command])

    def test_no_legacy_escape_hatch_remains(self):
        """后门本身必须不存在 —— 留着它就是为一个已删除的系统保留能力。"""
        self.assertFalse(hasattr(self.supervisor, "LEGACY_COMMANDS"))
        self.assertFalse(hasattr(self.supervisor, "_refuse_legacy"))
        source = (Path(__file__).resolve().parents[1]
                  / "substrate" / "supervisor.py").read_text(encoding="utf-8")
        # 只允许在讲「为什么删掉它」的文档串里出现。
        code = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
        self.assertNotIn("MERISTEM_ALLOW_LEGACY", code)

    def test_no_v31_state_paths_remain(self):
        source = (Path(__file__).resolve().parents[1]
                  / "substrate" / "supervisor.py").read_text(encoding="utf-8")
        code = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
        for marker in ("journal.jsonl", "proposals.md", "control/agenda.md",
                       "scoreboard.jsonl"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, code)

    def test_panic_latch_has_a_reader_in_v5(self):
        """闩必须有人看。**一个零调用点的安全开关不是安全开关** ——
        这份规格自己写过，`advance()` 零调用点就是这么活过 400 拍的。"""
        self.assertTrue(hasattr(self.supervisor, "_refuse_if_latched"))


class SoilCycleCounterTests(unittest.TestCase):
    """拍号必须能在**闸门拒绝的情况下**继续前进。

    2026-08-24 实测于服务器的死锁：`_next_soil_cycle` 原本数
    `observed_fitness` 的条数，而那种事件只在 `validate_task()` **通过之后**才写；
    C1 的 `eligible_after` 又要求「冻结那一拍不可用」，即拍号必须先前进。
    **于是拍号只能靠穿过闸门来前进，而闸门正用它做判断** —— 永远停在原地。

    与 v3.1 的 `campaign_calls` 同一形状（§13.2）。教训比 I1 更窄也更基本：
    **推进拍号的动作，不得挂在拍号所守的那道闸后面。**
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from substrate import supervisor
        self.supervisor = supervisor

    def _ledger(self):
        return soil_state.Ledger(self.state / "soil-ledger.jsonl")

    def test_counter_starts_at_one_on_an_empty_ledger(self):
        self.assertEqual(self.supervisor._next_soil_cycle(Path(self.tmp.name)), 1)

    def test_a_rejected_attempt_still_advances_the_counter(self):
        """**这一条就是那个死锁的回归。**

        只写一条 `cycle`（一次被拒的尝试也会写它），不写任何 `observed_fitness`。
        旧实现在这里恒返回 1；正确行为是前进。
        """
        self._ledger().append({"kind": "cycle", "commit": None, "task_id": None,
                               "generation": "gen-0", "soil_cycle": 1,
                               "exit_code": None})
        self.assertEqual(self.supervisor._next_soil_cycle(Path(self.tmp.name)), 2)

    def test_multiple_events_in_one_beat_do_not_over_advance(self):
        """一拍里写了多条带拍号的事件，拍号只前进一格 ——
        取 `max` 而不是计数，正是为了这个。"""
        ledger = self._ledger()
        for _ in range(3):
            ledger.append({"kind": "cycle", "commit": None, "task_id": None,
                           "generation": "gen-0", "soil_cycle": 4, "exit_code": None})
        self.assertEqual(self.supervisor._next_soil_cycle(Path(self.tmp.name)), 5)

    def test_counter_is_recomputable_from_any_copy_of_the_ledger(self):
        """不另设计数文件：台账就是权威，这个数可在任意副本上离线重算。"""
        ledger = self._ledger()
        ledger.append({"kind": "cycle", "commit": None, "task_id": None,
                       "generation": "gen-0", "soil_cycle": 7, "exit_code": None})
        copy_dir = Path(self.tmp.name) / "copy"
        (copy_dir / "state").mkdir(parents=True)
        shutil.copy(ledger.path, copy_dir / "state" / "soil-ledger.jsonl")
        self.assertEqual(self.supervisor._next_soil_cycle(copy_dir), 8)


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
