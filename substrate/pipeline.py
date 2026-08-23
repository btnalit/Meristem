"""候选处理流水线（S2+S4+S5, §10.2）与出生判据谓词（§1.2）。

v3.1 把 deterministic + probes + review 做在 `meristem.loop cycle` 内部；v5 把这三件
事全部判给土壤，于是需要这条流水线。**种子只产出候选，不测量、不判决、不写分数。**

判决位上坐着谁，是 P0-a 与 P0-b 的唯一差别：`panel` 是一个 adapter，
P0-a 传 `manual_prompt`（人敲 y/n），P0-b 换成真 panel，**其余代码一行不动**。

---

**本模块两处偏离 §10 的字面文本，都是实现时才暴露的规格缺口，走 §18 勘误行（v5.10）。**

**① `Outcome` 多出三个表以外的取值。** §10 的失败路径表列的是 `process_candidate`
**判决路径**的六个出口。但另有三种情形结构上不属于那六个：
`CALIBRATION`（§12.0.1 强制回滚、永不 merge —— 它不是失败，把它记成 `UNFULFILLED`
会污染拒绝额度与 failure_history）、`ABANDONED` 与 `SOIL_RECOVERY`（两者只由
`reconcile_on_start` 产生，§10.2 自己写了「未含则标记 abandoned…无法判定 →
soil_recovery」却没给它们值）。**扩的是 `outcome` 而不是 `kind`**：CA-6a 断言台账里
的 `kind` 是规格集合的子集，新造一个 `kind` 会当场违规，而 `outcome` 字段的取值域
本就由 `Outcome` 枚举定义。

**② `promotion_intent` 带 `verdict_authority`。** CA-11 要断言「manual accept 与
panel accept 产生逐字相同的事件序列，仅 `verdict.authority` 取值不同」——
**而 §8.2 到 §10 的任何一条事件都不携带 authority**。照字面实现，两条序列会
*完全*相同，CA-11 于是恒真：**一条永远绿、也永远不检查任何东西的断言**，
正是 §1.2 点名的那种空真（`.get()` 兜底、CA-7 缺键恒过）的第三个实例。
authority 落在 `promotion_intent` 上——判决之后、merge 之前的第一条事件。
**已知未闭合**：拒绝路径（`promotion_outcome`）目前不带 authority，
因此 CA-11 只覆盖 accept 一侧，与它自己的措辞（「manual accept 与 panel accept」）一致。
"""
from __future__ import annotations

import dataclasses
import enum
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace

from substrate import fitness
from substrate import probe_runner

#: §8.1.4 的 `expected` 枚举。缺一不可，每一条都锚定一次实证。
EXPECTED_VALUES = frozenset({"score_increase", "cost_reduction",
                             "refusal_with_reason", "no_measurement"})

#: §8.1.4：回归面由土壤定，种子不可改。**能收窄回归面就等于能挑尺。**
REQUIRED_REGRESSION_POLICY = "all-active-probes"

#: 在 `process_candidate` 之前分流的特殊任务（§10.2 docstring）。
#: 它们不产生 Change，走 TaskDecision / no_measurement 各自的路径。
DIVERTED_EXPECTED = frozenset({"refusal_with_reason", "no_measurement"})

CANARY_TIMEOUT_SECONDS = 300


class TaskDeclarationError(ValueError):
    """Task 声明违反 §8.1.4 的硬约束，土壤拒绝该声明。"""


class Outcome(enum.Enum):
    PROMOTED = enum.auto()
    # ── §10 失败路径表的六个判决出口 ──
    REGRESSED = enum.auto()
    UNFULFILLED = enum.auto()
    REJECTED = enum.auto()
    CANARY_REJECT = enum.auto()
    UNMEASURED = enum.auto()
    STALE = enum.auto()
    # ── 表以外的三个，理由见模块 docstring 的缺口 ① ──
    CALIBRATION = enum.auto()
    ABANDONED = enum.auto()
    SOIL_RECOVERY = enum.auto()


#: 是否计入该任务的拒绝额度（§10 失败路径表最后一列）。
#: **语义失败计额度，机制/环境故障不计** —— 混淆两者，就会因为一次换 runner 版本
#: 而把任务判成「种子做坏了三次」并 parked。
COUNTS_AGAINST_QUOTA = {
    Outcome.REGRESSED: True,
    Outcome.UNFULFILLED: True,
    Outcome.REJECTED: True,
    Outcome.CANARY_REJECT: True,
    Outcome.UNMEASURED: False,
    Outcome.STALE: False,
    Outcome.CALIBRATION: False,
    Outcome.ABANDONED: False,
    Outcome.SOIL_RECOVERY: False,
}


@dataclasses.dataclass(frozen=True)
class Task:
    """§8.1.4 的 Task 声明。**目标由种子提出，结果由土壤计算。**"""

    task_id: str
    kind: str
    target: str
    primary_probe: str
    expected: str = "score_increase"
    minimum_delta: float = 0.0
    regression_policy: str = REQUIRED_REGRESSION_POLICY

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        missing = [k for k in ("task_id", "kind", "target", "primary_probe") if k not in data]
        if missing:
            raise TaskDeclarationError(f"Task 声明缺字段 {missing}（§8.1.4）")
        return cls(
            task_id=data["task_id"], kind=data["kind"], target=data["target"],
            primary_probe=data["primary_probe"],
            expected=data.get("expected", "score_increase"),
            minimum_delta=float(data.get("minimum_delta", 0.0)),
            regression_policy=data.get("regression_policy", REQUIRED_REGRESSION_POLICY),
        )


@dataclasses.dataclass(frozen=True)
class Verdict:
    """判决。`authority ∈ {manual, panel}` —— **这是 manual 与 panel 之间唯一的差别**
    （§12.0.2）。y/n 落在这里，不落在 merge 位置。"""

    passed: bool
    authority: str
    reason: str = ""

    def __post_init__(self):
        if self.authority not in ("manual", "panel"):
            raise ValueError(f"verdict.authority 只能是 manual 或 panel，收到 {self.authority!r}")


# ---------------------------------------------------------------------------
# git 与树物化
# ---------------------------------------------------------------------------
def git(repo, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, ("git",) + args,
                                            result.stdout, result.stderr)
    return result.stdout.strip()


def check_ancestry(repo, commit: str) -> bool:
    """`candidate.parent == HEAD`（C3）。**锁内调用两次** —— 测量与面板判决之间
    HEAD 可能移动，只在入口检查一次等于没检查。"""
    try:
        return git(repo, "rev-parse", commit + "^") == git(repo, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return False


def git_diff(repo, parent: str, commit: str) -> str:
    return git(repo, "diff", parent, commit)


def merge_ff(repo, commit: str) -> None:
    git(repo, "merge", "--ff-only", commit)


def materialize_readonly_tree(repo, sha: str, dest) -> Path:
    """把一个 commit 的树物化到 `dest`。

    用 `git archive` + stdlib `tarfile`，不用 `git worktree add`：物化出来的是一份
    **与仓库无关的普通目录**，organ 在里面跑不到 `.git`，也就无从改动仓库本身
    （§15.6 C6 的最小完整性隔离，同一条理由）。
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "--format=tar", sha],
                             cwd=str(repo), capture_output=True)
    if archive.returncode != 0:
        raise subprocess.CalledProcessError(archive.returncode,
                                            ["git", "archive", sha],
                                            archive.stdout, archive.stderr)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 没有 filter 参数
            tf.extractall(dest)
    return dest


def canary(repo, commit: str, tree) -> tuple:
    """候选还能不能启动（§9.1 的 `selftest` 契约：0 = 通过）。

    **不再重跑探针。** v3.1 的 canary 里那份「全套冻结集回归」在 v5 是 pipeline
    自己的 before/after 测量（S2）；重跑一遍会让同一件事有两个判定处，
    **而两个判定处迟早不一致** —— 这正是 §17.5 点名的那种漂移。
    """
    env = {**os.environ, "PYTHONPATH": str(tree)}
    try:
        result = subprocess.run([sys.executable, "-m", "meristem.loop", "selftest"],
                                cwd=str(tree), capture_output=True, text=True,
                                timeout=CANARY_TIMEOUT_SECONDS, env=env)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"canary boot failed: {exc}"
    if result.returncode != 0:
        return False, f"selftest failed: {(result.stdout + result.stderr)[-400:]}"
    return True, "canary alive: immune selftest passed"


# ---------------------------------------------------------------------------
# Task 校验与兑现
# ---------------------------------------------------------------------------
def validate_task(task: Task, ctx) -> None:
    """§8.1.4 的硬约束，违反即拒绝该声明。

    **`primary_probe` 必须是 internal probe，anchor 不得被声明为 primary** ——
    否则 evaluator 面前摆着两把尺，哪把有利就用哪把。这里的判别方式是
    「在 internal 清单里找得到」：`probe_runner.catalogue()` 只扫
    `vault/internal/active/`，anchor 不在其中。

    **已知未闭合**：anchor 目前根本不在被测集合里（见 `ignition-status` 的
    open item），所以这条检查现在等价于「probe 必须是已冻结的 internal probe」。
    anchor 进入被测集合之后，判别必须改为按来源目录区分，**而不是靠「找不到」**——
    靠缺席做判别，正是 anchor 一旦加入就会静默失效的那种写法。
    """
    if task.expected not in EXPECTED_VALUES:
        raise TaskDeclarationError(f"expected={task.expected!r} 不在 §8.1.4 枚举内")
    if task.regression_policy != REQUIRED_REGRESSION_POLICY:
        raise TaskDeclarationError(
            f"regression_policy 固定为 {REQUIRED_REGRESSION_POLICY!r}，种子不可改（§8.1.4）")
    manifests = probe_runner.catalogue(ctx.vault)
    if manifests is None:
        raise TaskDeclarationError("probe 清单这一次编不出来，无法校验 primary_probe")
    internal_ids = {m.get("id") for m in manifests}
    if task.primary_probe not in internal_ids:
        raise TaskDeclarationError(
            f"primary_probe={task.primary_probe!r} 不是已冻结的 internal probe"
            f"（vault/internal/active 里有 {sorted(i for i in internal_ids if i)}）")


def evaluate_task(task: Task, observed: list) -> tuple:
    """C6：土壤验证种子兑现了自己声明的方向。返回 `(ok, why)`。"""
    if task.expected == "score_increase":
        record = next((r for r in observed if r.get("probe_id") == task.primary_probe), None)
        if record is None:
            return False, f"primary_probe {task.primary_probe} 没有产出记录"
        delta = record.get("delta")
        if delta is None:
            # 不可比不是没兑现，但它也确实没证明兑现。两者都不该被算成成功。
            return False, f"primary_probe delta 不可比（status={record.get('status')}）"
        if delta < task.minimum_delta:
            return False, f"delta {delta} < minimum_delta {task.minimum_delta}"
        return True, ""
    if task.expected == "cost_reduction":
        # **fail closed，不假装兑现。** §17.2 的 Metric Registry
        # （soil/metric-registry.json）本轮未实现，没有任何东西可以据以判定下降。
        return False, ("expected=cost_reduction 需要 Metric Registry（§17.2），本轮未实现")
    return False, f"expected={task.expected!r} 不该进入 process_candidate（§10.2 分流规则）"


# ---------------------------------------------------------------------------
# 流水线
# ---------------------------------------------------------------------------
def finalize_nonpromotion(ctx, outcome: Outcome, source, why: str, *, quota: bool) -> Outcome:
    """**唯一的非晋升出口。** 任何没有 `accepted_fitness` 的候选，都不得被统计为 improved。"""
    ctx.ledger.append({"kind": "promotion_outcome", "outcome": outcome.name,
                       "source": source, "why": why,
                       "counts_as_progress": False,
                       "counts_against_task_quota": quota})
    return outcome


def process_candidate(commit: str, task: Task, *, repo, panel, ctx) -> Outcome:
    """候选处理流水线。无隐式全局；每个非晋升出口都写 `promotion_outcome`。

    **只处理产生 Change 的任务。** 特殊任务在进入本函数之前就分流（§10.2）。
    """
    if task.expected in DIVERTED_EXPECTED:
        raise ValueError(
            f"expected={task.expected!r} 必须在进入 process_candidate 之前分流（§10.2）："
            "refusal_with_reason 走 TaskDecision，no_measurement 走仅测回归 + 面板批准豁免。")
    validate_task(task, ctx)
    repo = Path(repo)

    with ctx.promotion_lock:                                   # 单写者
        # ── C3 第一次祖先检查（测量之前）
        if not check_ancestry(repo, commit):
            return finalize_nonpromotion(ctx, Outcome.STALE, None,
                                         "candidate.parent != HEAD", quota=False)

        parent = git(repo, "rev-parse", commit + "^")
        with tempfile.TemporaryDirectory(prefix="meristem-measure-") as tmp:
            parent_tree = materialize_readonly_tree(repo, parent, Path(tmp) / "parent")
            candidate_tree = materialize_readonly_tree(repo, commit, Path(tmp) / "candidate")

            before = probe_runner.run_all(parent_tree, ctx.vault)      # S2
            after = probe_runner.run_all(candidate_tree, ctx.vault)
            if before is None or after is None:
                return finalize_nonpromotion(ctx, Outcome.UNMEASURED, None,
                                             "measurement failed", quota=False)

            observed = fitness.pair(before, after, commit)             # S5
            oid = ctx.ledger.append({
                "kind": "observed_fitness", "records": observed,
                "commit": commit, "source": None,
                # ── §8.2 的六个强制字段，全部由土壤在此处写入 ──
                "task_id": task.task_id,
                "primary_probe": task.primary_probe,   # 已校验必为 internal
                "generation": ctx.generation,
                "soil_cycle": ctx.soil_cycle,
                "calibration": ctx.calibration,
                "counts_as_progress": False,           # 此刻尚未晋升
                "candidate_state": "measured",
                "promotion_state": "pending"})

            # ── §12.0.1：校准强制回滚、永不 merge，**结构上产不出 accepted_fitness**。
            # 封套上的 calibration:true 是纵深的第二层（§1.2 谓词的第二个合取项）；
            # 结构性保证在这里，优先于标记。
            if ctx.calibration:
                return finalize_nonpromotion(
                    ctx, Outcome.CALIBRATION, oid,
                    "calibration run: measured, forced rollback, never merged", quota=False)

            if fitness.has_regression(observed):
                return finalize_nonpromotion(ctx, Outcome.REGRESSED, oid,
                                             "regressed vs parent", quota=True)

            fulfilled, why = evaluate_task(task, observed)             # C6
            if not fulfilled:
                return finalize_nonpromotion(ctx, Outcome.UNFULFILLED, oid,
                                             f"declared outcome not met: {why}", quota=True)

            # 面板只收 diff + 任务声明。**不传 observed** —— 评审员看见 +20 分会
            # 锚定向批准，判决就被测量污染了。
            verdict = panel(commit, git_diff(repo, parent, commit), task)    # S4
            if not verdict.passed:
                return finalize_nonpromotion(ctx, Outcome.REJECTED, oid,
                                             verdict.reason, quota=True)

            canary_ok, canary_why = canary(repo, commit, candidate_tree)
            if not canary_ok:
                return finalize_nonpromotion(ctx, Outcome.CANARY_REJECT, oid,
                                             canary_why, quota=True)

            # ── C3 第二次祖先检查（merge 之前）
            if not check_ancestry(repo, commit):
                return finalize_nonpromotion(ctx, Outcome.STALE, oid,
                                             "HEAD moved during judgement", quota=False)

            # 晋升是三步非原子操作：merge / scoreboard / accepted_fitness。
            # 任一步之间崩溃 → 主线已含候选而事实不完整，靠 reconcile_on_start 收尾。
            ctx.ledger.append({"kind": "promotion_intent", "commit": commit,
                               "parent": parent, "source": oid, "state": "pending",
                               "verdict_authority": verdict.authority})
            merge_ff(repo, commit)
            ctx.scoreboard.write(after, commit, parent)                # S2
            ctx.ledger.append({
                "kind": "accepted_fitness", "source": oid,
                "commit": commit, "records": observed,
                "task_id": task.task_id,
                "primary_probe": task.primary_probe,
                "generation": ctx.generation,
                "soil_cycle": ctx.soil_cycle,
                "calibration": False,      # 校准强制回滚，永不到达此处（§12.0.1）
                "counts_as_progress": True})
            ctx.ledger.append({"kind": "promotion_committed", "commit": commit})
            return Outcome.PROMOTED


def _runs_from_records(records: list) -> list:
    """把 `observed_fitness` 的 `records[]` 还原成记分板需要的形状。

    只在 `reconcile_on_start` 补写记分板时用：崩溃之后无法重新拿到当时的
    `ProbeRun` 对象，而**重新测一遍会得到另一次测量**（树可能已变），
    那就不再是「补写当时那条事实」而是伪造一条新的。
    """
    return [SimpleNamespace(
        probe_id=r.get("probe_id"), score=r.get("after"),
        checks_passed=r.get("checks_after"), checks_total=r.get("checks_total"),
        tree_sha=r.get("tree_after"), probe_manifest_sha=r.get("probe_manifest_sha"),
        runner_version=r.get("runner_version"),
        execution_policy_version=r.get("execution_policy_version"),
    ) for r in records]


def reconcile_on_start(repo, ctx) -> list:
    """supervisor 启动时必跑：有 `promotion_intent` 而无 `promotion_committed` 的，
    核对 main 是否已含该 commit —— 已含则补写 scoreboard/accepted_fitness，
    未含则标记 abandoned。无法判定 → soil_recovery。

    **崩溃恢复要到 P0-c 无人值守时才第一次被真正需要**，那时它从未被跑过一次 ——
    所以 P0-a 的 manual-cycle 也走这条路径（§12.0.2）：那不只是在测种子，
    也是在测土壤自己的事务链。
    """
    repo = Path(repo)
    rows = ctx.ledger.read()
    committed = {r.get("commit") for r in rows if r.get("kind") == "promotion_committed"}
    by_id = {r.get("event_id"): r for r in rows}
    resolved = []

    for intent in [r for r in rows if r.get("kind") == "promotion_intent"]:
        commit = intent.get("commit")
        if commit in committed:
            continue
        with ctx.promotion_lock:
            try:
                merged = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                    cwd=str(repo), capture_output=True).returncode == 0
            except OSError as exc:
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.SOIL_RECOVERY, intent.get("source"),
                    f"cannot decide whether main contains {commit}: {exc}", quota=False)))
                continue

            if not merged:
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.ABANDONED, intent.get("source"),
                    "promotion_intent written but main does not contain the commit",
                    quota=False)))
                continue

            observed_event = by_id.get(intent.get("source"))
            if observed_event is None:
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.SOIL_RECOVERY, intent.get("source"),
                    "main contains the commit but its observed_fitness event is missing",
                    quota=False)))
                continue

            records = observed_event.get("records", [])
            ctx.scoreboard.write(_runs_from_records(records), commit, intent.get("parent"))
            ctx.ledger.append({
                "kind": "accepted_fitness", "source": intent.get("source"),
                "commit": commit, "records": records,
                # 身份字段从当时那条 observed 事件抄回，**不从当前 ctx 取** ——
                # 崩溃后重启的 ctx 拿的是新的拍号，用它会把旧事实记成新拍的事。
                "task_id": observed_event["task_id"],
                "primary_probe": observed_event["primary_probe"],
                "generation": observed_event["generation"],
                "soil_cycle": observed_event["soil_cycle"],
                "calibration": False,
                "counts_as_progress": True})
            ctx.ledger.append({"kind": "promotion_committed", "commit": commit})
            resolved.append((commit, Outcome.PROMOTED))
    return resolved


# ---------------------------------------------------------------------------
# 出生判据（§1.2 的全文唯一定义点，实现者不得自撰等价物）
# ---------------------------------------------------------------------------
def is_ignition_event(ev) -> bool:
    """**单条台账行的纯函数。** 不接 task 参数、不查任何注册表、不做多跳关联。"""
    return (ev["kind"] == "accepted_fitness"           # C2：仅已 merge 进 main 的
        and ev["calibration"] is not True              # §12.0.1：校准永不计数
        and ev["counts_as_progress"] is True           # §10：唯一非晋升出口写 False
        and any(r["probe_id"] == ev["primary_probe"]   # 事件自带；不查 task registry
                and r["status"] == "improved"          # I5 枚举
                for r in ev["records"]))               # §8.2：records 是封套内元素

# ↑ **与 §1.2 的代码块逐字相同，由 SA-5 断言。** 改这里必须同步改规格，反之亦然；
# 这正是「判据只有一个定义点」在 CI 上的落地方式。

#: `excluded` 的归因顺序（§12.0.2）。**按 §1.2 四个合取项的书写顺序求值，
#: 报第一个不满足的那一项** —— 不是「所有不满足的项」，也不是任意一项。
IGNITION_CONJUNCTS = ("kind", "calibration", "counts_as_progress", "primary_probe")


def ignition_exclusion_reason(ev):
    """返回第一个不满足的合取项名；四项全满足返回 `None`。

    **归因顺序不定死，两次运行的 `excluded` 行就可能不一样**；而这一行是拿来做
    处置判断的（H1 否证 vs 修土壤）——**读数不稳定的仪表比没有仪表更坏**。
    """
    if ev.get("kind") != "accepted_fitness":
        return "kind≠accepted_fitness"
    # 到这里事件已是 accepted_fitness，六个强制字段按 §8.2 必然在场；
    # 严格下标、缺键抛错 —— 缺键是台账损坏，不该由读者猜一个方向（§1.2）。
    if ev["calibration"] is True:
        return "calibration"
    if ev["counts_as_progress"] is not True:
        return "counts_as_progress"
    if not any(r["probe_id"] == ev["primary_probe"] and r["status"] == "improved"
               for r in ev["records"]):
        return "primary_probe"
    return None
