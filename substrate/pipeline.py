"""候选处理流水线（S2+S4+S5, §10.2）与出生判据谓词（§1.2）；也是 C1 冻结
契约（§15 C1）的编排层（`validate_task()` 的 active/eligible_after 校验、
`freeze_proposal()`）。

v3.1 把 deterministic + probes + review 做在 `meristem.loop cycle` 内部；v5 把这三件
事全部判给土壤，于是需要这条流水线。**种子只产出候选，不测量、不判决、不写分数。**

判决位上坐着谁，是 P0-a 与 P0-b 的唯一差别：`panel` 是一个 adapter，
P0-a 传 `manual_prompt`（人敲 y/n），P0-b 换成真 panel，**其余代码一行不动**。

---

**本模块三处偏离 §10 的字面文本，都是实现时才暴露的规格缺口，走 §18 勘误行（v5.10）。**

（原文写「两处」而实际有三处 —— 独立审查 2026-08-23 指出。一句自称完整的
清单少一项，与本文档反复点名的「声明了没断言」是同一种东西，只是发生在注释里。）

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

**③ `evaluate_task(task, observed)` 不接 `ctx`。** §10.2 的伪代码写的是
`task_evaluator.evaluate(task, observed, ctx)`，而 §10 的土壤模块清单里没有
`task_evaluator.py` 这个模块 —— 它只在那一行调用里出现过。判定不需要 `ctx`
（`expected` / `minimum_delta` / `primary_probe` 全在 Task 声明上），
故内联于本模块并去掉那个未被使用的参数。
"""
from __future__ import annotations

import dataclasses
import enum
import io
import json
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


class SoilIsolationError(RuntimeError):
    """土壤无法在本环境下兑现 §15.6 要求的隔离，**拒绝继续**而不是降级运行。"""


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
        # **没有「悄悄降级成不设防」这个选项。** 候选树里可以合法地存在一个
        # git symlink 条目（mode 120000，target 任意），`extractall` 在没有
        # `filter="data"` 时会照着它把链接建出来 —— 于是「物化一棵只读树」变成了
        # 一次任意路径写入/读出。旧写法用 `except TypeError` 静默退回到裸
        # `extractall()`：在 Python < 3.12 上**保护为零，且没有任何提示**，
        # 而仓库从未声明过最低 Python 版本。宁可拒绝运行，也不要一条
        # 「看起来在防护、实际什么都没防」的路径。
        if sys.version_info < (3, 12):
            raise SoilIsolationError(
                "materialize_readonly_tree 需要 Python >= 3.12 的 tarfile "
                f"filter='data'；当前 {sys.version_info.major}.{sys.version_info.minor} "
                "无法阻止候选树里的 symlink 条目逃逸出目标目录。")
        tf.extractall(dest, filter="data")
    return dest


def canary(repo, commit: str, tree) -> tuple:
    """候选还能不能启动（§9.1 的 `selftest` 契约：0 = 通过）。

    **不再重跑探针。** v3.1 的 canary 里那份「全套冻结集回归」在 v5 是 pipeline
    自己的 before/after 测量（S2）；重跑一遍会让同一件事有两个判定处，
    **而两个判定处迟早不一致** —— 这正是 §17.5 点名的那种漂移。
    """
    # **绝不 `{**os.environ, ...}`。** §15.6 点名了 `canary(commit)`：它执行候选代码，
    # 与 organ 同受不可信执行契约约束。而 C-65 保证 supervisor 进程上一定有
    # `MERISTEM_VAULT` —— 整份环境传下去，等于把 anchor vault 的路径亲手交给种子控制的
    # 代码，**把上午刚立起来的那道门从旁边绕开**。正确写法本就在同一个仓库里
    # （`probe_runner._sandboxed_env()`），这里复用它，不另写一份。
    env = {**probe_runner._sandboxed_env(), "PYTHONPATH": str(tree)}
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

    **C1（§8.1.4 硬约束表第二行）**：「该 probe 须处于 `active`，且已过 C1 的
    `eligible_after`」。上面那条 catalogue 检查只问「vault 里有没有这把尺」，
    从不问「这把尺什么时候起才能用」——这正是 C1 要挡的攻击：「同一个 Change
    里先写尺、再写刚好通过这把尺的能力」。这条检查读 `ctx.frozen_registry`
    （C1 的登记，见 `substrate/soil_state.FrozenProbeRegistry`），不是 vault：
    「什么时候起可用」是登记记的事实，不是 manifest 内容的一部分。
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

    # ── C1：active + eligible_after（§8.1.4 硬约束表第二行；§15 C1）──────
    entry = ctx.frozen_registry.get(task.primary_probe)
    if entry is None:
        # 正常不会发生：能通过上面 catalogue 检查的 probe，冻结时
        # `pipeline.freeze_proposal()` 在同一把锁内同时写了 vault 与登记。
        # 到这里说明 vault 与登记不一致（例如：vault 里的条目是绕开
        # `freeze_into_vault()` 手工放进去的，或崩在两次写入之间，见
        # `freeze_proposal()` 的「已知未闭合项」）——**同样是拒绝**，不猜。
        raise TaskDeclarationError(
            f"primary_probe={task.primary_probe!r} 在 vault 里找得到，却没有冻结登记 "
            "—— vault 与冻结登记不一致，拒绝（C1）")
    if entry.get("status") != "active":
        raise TaskDeclarationError(
            f"primary_probe={task.primary_probe!r} 的冻结登记状态为 "
            f"{entry.get('status')!r}，须为 'active' 才可被声明为 primary_probe"
            "（§8.1.4；C1）")
    eligible_after = entry.get("eligible_after") or {}
    eligible_generation = eligible_after.get("generation")
    eligible_cycle = eligible_after.get("soil_cycle")
    # **generation 必须相等，不只是 soil_cycle 更大**——世代变化（§7.1 的
    # soil_recovery：「冻结自主运行，等待重新点火」）之后旧的 soil_cycle 计数
    # 是否仍可比是规格未言明的一点；本实现采取与 §4.1 Fitness 三维度配对
    # 同一条纪律：任一维度不等就不可信，fail closed，不假设跨世代可比。
    is_eligible = (
        ctx.generation == eligible_generation
        and isinstance(eligible_cycle, int) and not isinstance(eligible_cycle, bool)
        and ctx.soil_cycle > eligible_cycle
    )
    if not is_eligible:
        raise TaskDeclarationError(
            f"primary_probe={task.primary_probe!r} 尚未过 eligible_after —— "
            f"冻结于 generation={eligible_generation!r} soil_cycle={eligible_cycle!r}，"
            f"当前 generation={ctx.generation!r} soil_cycle={ctx.soil_cycle!r}；"
            "必须隔至少一个独立 cycle 才可针对它提能力变更（C1：防「同一 Change 里"
            "先写尺、再写刚好通过这把尺的能力」）")


# ---------------------------------------------------------------------------
# Probe 冻结（C1，`author_probe` 能力的土壤半，§15 C1 / §7）
# ---------------------------------------------------------------------------
def freeze_proposal(proposal_path, *, ctx, proposed_commit, created_by="seed") -> dict:
    """把 `seed/probe-proposals/<id>.json` 校验通过后冻结进 vault + 写冻结登记
    （C1；§7 `author_probe`：「种子写提案文件；土壤校验后写冻结登记与 vault
    manifest」——本函数就是「土壤校验后」那半）。

    v3.1 的 `promote_probes()`（已删除，见 `probe_runner.catalogue()` 的
    docstring）读的是 `state/probe-proposals`（错的路径，与 CA-5 扫的
    `seed/probe-proposals/` 对不上，§13.3 列的 supervisor 波次 2 改造项）；
    本函数是这条能力在 v5 下的重写，不是 v3.1 代码的修补。

    **落在 `pipeline.py` 而不是 `probe_runner.py`**：`probe_runner` 模块
    docstring 自称「唯一读写 vault 的地方」——本函数确实要写 vault，但写入
    动作全权委派给 `probe_runner.freeze_into_vault()`，本函数自己不直接碰
    vault 路径，只做编排（读提案文件、拿 git tree sha、组装登记项、串起
    `promotion_lock`），那条「唯一入口」的边界因此仍然成立。

    **`proposed_commit` 由调用方给出，不在此处从 HEAD 现读**——理由与
    `ctx.generation` / `ctx.soil_cycle` 住在 ctx 里而不是现读同一条：调用方
    知道「这次冻结对应哪个候选 commit」，这里现猜就是又一次「猜一个方向」
    （§1.2 的纪律）。P0-a 尚未给这个函数接一条 CLI（不在本次交付范围——
    交付的是机制本身与它的测试，接线是另一件事），调用方目前只有测试。

    **在 `ctx.promotion_lock` 内完成 vault 写入与登记写入**：两者是两次独立
    的文件系统操作，不是一个事务；用同一把跨进程锁把它们串行化，至少排除
    「两个并发 freeze 各自认为自己是第一个」的竞态。与晋升共用同一把锁没有
    语义冲突——两者都是「改变共享 soil 状态」，谁也不需要在另一个的临界区
    里跑。

    **已知的未闭合项（不是本函数该顺手补的）**：vault 写入与登记写入仍是
    两次独立操作，中间可能崩溃——若崩在两者之间，vault 有 manifest 但登记
    没有条目（或反过来，若未来实现改成先写登记）。`validate_task()` 的两条
    独立检查（catalogue 成员资格 + 登记条目存在）在这种情形下都会 fail
    closed（缺一半就拒绝，不会误判为 eligible），所以不是一个安全漏洞，
    但也不是一次干净的收尾——对称于 `reconcile_on_start()` 对晋升三步的
    处理，freeze 目前没有等价的 reconcile。P0-a 单写者、人工触发的量级下，
    这个窗口没有实际发生过；P0-c 无人值守时需要重新审视。
    """
    proposal_path = Path(proposal_path)
    try:
        manifest = json.loads(proposal_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise probe_runner.ProbeProposalError(f"{proposal_path}: 读不到（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise probe_runner.ProbeProposalError(f"{proposal_path}: 不是合法 JSON（{exc}）") from exc

    probe_runner.validate_proposal(manifest)               # §8.1.1 schema + I2
    probe_id = manifest["id"]

    with ctx.promotion_lock:
        if ctx.frozen_registry.get(probe_id) is not None:
            raise probe_runner.ProbeProposalError(
                f"{probe_id}: 已冻结过，拒绝再次冻结（C1：同一 probe_id 的 manifest "
                "不得变化；改 active probe 必须新建 probe_id）")
        probe_manifest_sha = probe_runner.freeze_into_vault(ctx.vault, manifest)
        frozen_tree_sha = git(ctx.repo, "rev-parse", f"{proposed_commit}^{{tree}}")
        entry = ctx.frozen_registry.freeze({
            "probe_id": probe_id,
            "status": "active",
            "created_by": created_by,
            "proposed_commit": proposed_commit,
            "frozen_tree_sha": frozen_tree_sha,
            "frozen_probe_manifest_sha": probe_manifest_sha,
            # eligible_after：见 §15 C1 与本模块 validate_task() 的注释——
            # 度量单位是 soil_cycle（拍号，因果排序的原语），不是墙钟时间：
            # C1 要挡的是「同一个 Change 内」，Change 的边界由 soil_cycle 划，
            # 两次 cycle 可能背靠背在同一秒内跑完，也可能横跨数小时，
            # 墙钟时间对「隔没隔开一个独立 cycle」这个问题不提供任何保证。
            "eligible_after": {"generation": ctx.generation, "soil_cycle": ctx.soil_cycle},
        })
    return entry


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
def finalize_nonpromotion(ctx, outcome: Outcome, source, why: str, *, quota=None) -> Outcome:
    """**唯一的非晋升出口。** 任何没有 `accepted_fitness` 的候选，都不得被统计为 improved。

    额度归属**以 `COUNTS_AGAINST_QUOTA` 为准**，调用点传的 `quota` 只当交叉校验。
    原先那张表是装饰性的：每个调用点各写一个字面量，表本身没有任何调用者，
    唯一「验证」它的测试还是拿表跟自己比。**一张没人读的表就是下一次漂移的起点** ——
    改了调用点没改表（或反过来）不会有任何东西出声。现在两边不一致会当场抛错。
    """
    expected = COUNTS_AGAINST_QUOTA[outcome]
    if quota is not None and quota != expected:
        raise ValueError(
            f"{outcome.name} 的额度归属与 COUNTS_AGAINST_QUOTA 不一致："
            f"调用点写 {quota}，表写 {expected}（§10 失败路径表）")
    ctx.ledger.append({"kind": "promotion_outcome", "outcome": outcome.name,
                       "source": source, "why": why,
                       "counts_as_progress": False,
                       "counts_against_task_quota": expected})
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
            try:
                parent_tree = materialize_readonly_tree(repo, parent, Path(tmp) / "parent")
                candidate_tree = materialize_readonly_tree(repo, commit, Path(tmp) / "candidate")
            except SoilIsolationError:
                # 环境兑现不了隔离契约 —— 这不是「这个候选测不出来」，
                # 而是土壤自己不该继续跑。原样上抛，让操作员看见。
                raise
            except Exception as exc:  # noqa: BLE001 -- 恶意/损坏的候选树不得炸穿流水线
                # 例如候选树里带一个逃逸目标的 symlink 条目：extractall 会拒绝并抛错。
                # 那是「这一次量不出来」（机制故障，不计额度），不是一个 traceback。
                return finalize_nonpromotion(ctx, Outcome.UNMEASURED, None,
                                             f"cannot materialize trees: {exc}", quota=False)

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


def _main_contains(repo, commit: str):
    """main 是否已含该 commit。`True` / `False` / **`None` = 判定不了**。

    `git merge-base --is-ancestor` 用退出码区分三件事，而不是两件：
    **0 = 是祖先，1 = 不是祖先，其它（实测 128）= 根本没能判定**
    （commit 名字解析不了：被 gc 掉、仓库损坏、拿错了仓库）。
    把「非 0 一律当作不是祖先」会把「判定不了」报成 `ABANDONED` —— 一个**确信的否定**，
    而真实状态是未知。规格反复强调这两者对应完全不同的处置
    （H1 否证 vs 修土壤），读数不稳定的仪表比没有仪表更坏。
    """
    try:
        result = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                                cwd=str(repo), capture_output=True)
    except OSError:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


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
    # **已存在的 accepted_fitness 必须单独记账。** 晋升是三步非原子操作，
    # 崩溃可能正好落在 accepted_fitness 与 promotion_committed 之间 —— 那是一行的
    # 窗口，而且**正是本函数存在的理由**。只看 promotion_committed 在不在，
    # 会把「差最后一条」误判成「什么都没写」，于是补写出第二条 accepted_fitness：
    # 同一次晋升被 §1.2 的判据数成两次点火。CA-10 抓不到它（它只验每条 accepted
    # 都有配对的 intent/committed/scoreboard，**从不验基数**），
    # 而那个数字是整个 P0-a 的唯一出口。
    accepted_sources = {r.get("source") for r in rows if r.get("kind") == "accepted_fitness"}
    by_id = {r.get("event_id"): r for r in rows}
    resolved = []

    for intent in [r for r in rows if r.get("kind") == "promotion_intent"]:
        commit = intent.get("commit")
        source = intent.get("source")
        if commit in committed:
            continue
        with ctx.promotion_lock:
            merged = _main_contains(repo, commit)
            if merged is None:
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.SOIL_RECOVERY, source,
                    f"cannot decide whether main contains {commit}", quota=False)))
                continue
            if not merged:
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.ABANDONED, source,
                    "promotion_intent written but main does not contain the commit",
                    quota=False)))
                continue

            if source in accepted_sources:
                # accepted_fitness 已经写过了，缺的只是收尾那一条。**只补收尾。**
                ctx.ledger.append({"kind": "promotion_committed", "commit": commit})
                committed.add(commit)
                resolved.append((commit, Outcome.PROMOTED))
                continue

            observed_event = by_id.get(source)
            if observed_event is None:
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.SOIL_RECOVERY, source,
                    "main contains the commit but its observed_fitness event is missing",
                    quota=False)))
                continue
            if observed_event.get("calibration") is True:
                # §12.0.1 的纵深第二层：校准结构上到不了 promotion_intent。
                # 真到了，说明有人给校准开了一条 merge 的路 —— **不得把它洗成
                # 一条 calibration:false 的 accepted_fitness**，那正是判据要挡的东西。
                resolved.append((commit, finalize_nonpromotion(
                    ctx, Outcome.SOIL_RECOVERY, source,
                    "calibration run reached promotion_intent -- refusing to launder it",
                    quota=False)))
                continue

            records = observed_event.get("records", [])
            ctx.scoreboard.write(_runs_from_records(records), commit, intent.get("parent"))
            ctx.ledger.append({
                "kind": "accepted_fitness", "source": source,
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
            # 本次调用内新写的事实要立刻并进账本快照，否则同一批里的第二条
            # intent 会对着一份过期快照重跑同样的补写。
            accepted_sources.add(source)
            committed.add(commit)
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
