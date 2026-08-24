"""模型调用预算执行（S7, v5 §5 I1 / §8.1.3 / §10.2 / §13.2）。

**I1 存在的全部理由是 v3.1 死过一次的这个形状**（§13.2 复盘）：
`campaign_calls = 1000` 是一个全时段累计计数器，撞顶之后 `check()` 在**每一次**
mutation 与**每一次** reflection 上都抛错——循环因此死锁，而唯一能修这道门的人
（土壤操作员）被这道门自己挡在外面。**这不是「预算太紧」的问题，是「计数方式」
的问题**：任何单调递增、只升不降、封顶不退役的计数器，只要挂在执行路径上，
就会在撞顶那一刻把自己变成一把锁死系统的闸。

I1 的强制规则因此只有一条：**本模块的任何计数都不得是全时段累计，一律滚动窗口**。
`soil/model-policy.toml` 已经把这条写死在注释里（`window_cycles` 一行）；本模块
只是把那条注释变成机制。窗口以 **soil_cycle 序号**为轴，不是墙钟时间——
`window_cycles=12` 的意思是「只数最近 12 个拍号」，第 13 个拍号一到，
第 1 个拍号的调用记录自动滚出窗口，**不需要任何人手动清零、退役或重置**。
这正是修复死锁的机制：撞顶只会拒绝当前这一拍多出来的调用，
下一拍窗口自然前移，锁会自己松开。

**本模块不拒绝人**。`check()` 只在 `substrate/model_gateway.py` 处理种子经
`meristem/llm.py` 发起的调用请求时被调用——操作员的 `manual-cycle` /
`ignition-status` / `panic.py` 都不经过这条路径，S7 的锁只锁种子的模型调用权，
不锁土壤操作员本人的任何命令。v3.1 那次「唯一能修门的人被锁在门外」，
锁的恰恰是操作员自己要跑的 `campaign` 级检查；v5 把预算判定收窄到
「这一次种子调用批不批」，操作员没有任何命令挂在这道闸后面。

**返回违规描述而不是抛异常**：`check()` 的签名是 `str | None`，从不 raise。
一个会抛错的预算检查，只要挂在调用路径上，撞顶那一刻就是 v3.1 的死锁重演——
无论异常处理写得多小心，「达到上限」都必须是一个可以被正常处理的返回值，
不是一个需要 try/except 才能不崩的事件。

**不与 `substrate/model_gateway.py` 合并**（§18 v5.9 行的原话）：
「预算判定藏进调用执行，正是 S7 那次死锁的成因」。本模块只回答
「配额允不允许」，不知道 provider、不知道 prompt、不碰 stdin/stdout；
网关那一侧独立存在，调用本模块的 `check()` 作为其中一步。
两者分开，才能被独立观测、独立测试、独立修——而不是像 v3.1 那样焊死在一起，
连问题出在哪一层都分不清。
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from substrate.soil_state import _AppendOnlyJsonl

#: 与 soil_state.py 同一套推导（REPO = 本文件的上两级目录）。**不依赖调用者的
#: cwd / PYTHONPATH / 环境变量**——见 substrate/model_gateway.py 与
#: substrate/supervisor.py 里 `_model_gateway_entrypoint()` 的说明：网关会被种子
#: 的候选 worktree 子进程 spawn 出来，那个子进程的 PYTHONPATH 指向候选树而不是
#: 本仓库，只有从 `__file__` 现算的绝对路径才靠得住。
REPO = Path(__file__).resolve().parent.parent

#: 土壤私有策略文件，§8.1.3：种子不可读、不可写。
DEFAULT_POLICY_PATH = REPO / "soil" / "model-policy.toml"

#: `state/soil-model-calls.jsonl` —— 调用记录台账（§8.1.5 前缀族）。
#: 与 `state/soil-ledger.jsonl` 分开：那本台账记的是候选晋升事件（C4 独占写入
#: 语义已经很重），把「模型调用发生过一次」这种高频、低语义的事件混进去，
#: 只会让 §17.8 的 CA-6a/6b（`kind` 集合核对）多背一堆与晋升无关的噪音。
DEFAULT_CALLS_LEDGER = REPO / "state" / "soil-model-calls.jsonl"
DEFAULT_PROVIDER_EVENTS_LEDGER = REPO / "state" / "soil-provider-events.jsonl"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ModelCallLedger(_AppendOnlyJsonl):
    """`state/soil-model-calls.jsonl` 的写入端。

    **复用 `soil_state._AppendOnlyJsonl`，不另起一套**：命名前缀族校验
    （§8.1.5）、`O_NOFOLLOW` + `O_APPEND` 的原子追加、symlink 拒绝，
    这些机制已经在那里写过一次、测过一次；本模块若另写一份 open/write，
    就是同一件事的第二份实现，两份实现迟早分岔——这正是这份规格通篇在防的
    「两处各写一份、该信哪份」。

    **只记「已放行且已尝试」的调用**：budget 本身拒绝的、或因缺凭据从未真正
    发出请求的，都不落这本账——见 `model_gateway.handle()` 的调用点与注释。
    没打过 provider 的一次，不该消耗配额；否则「拒绝」本身会变成新的拒绝理由。
    """

    def record(self, *, cycle: int, role: str, slot_id: str) -> None:
        self._append_raw({"ts": _utcnow(), "cycle": cycle, "role": role, "slot_id": slot_id})


class ProviderEventLedger(_AppendOnlyJsonl):
    """Soil-only provider telemetry; never exposed through the seed ABI.

    This is deliberately separate from ``soil-model-calls.jsonl`` because the
    budget reader counts call rows.  Result rows must not consume another call
    or alter the rolling-window calculation.
    """

    def record(self, *, cycle: int, mode: str, role: str, slot_id: str,
               model: str, event: str, status: str | None = None,
               reason: str | None = None, attempt: int | None = None) -> None:
        row = {"ts": _utcnow(), "cycle": cycle, "mode": mode, "role": role,
               "slot_id": slot_id, "model": model, "event": event}
        if status is not None:
            row["status"] = status
        if reason is not None:
            row["reason"] = reason
        if attempt is not None:
            row["attempt"] = attempt
        self._append_raw(row)


def load_policy(path: Path | str | None = None) -> dict:
    """读 `soil/model-policy.toml`。种子侧永远不会调用这个函数——它在
    `substrate/` 里，I9/CA-4 保证 `meristem/` 没有任何导入路径能碰到它。
    """
    resolved = Path(path) if path is not None else DEFAULT_POLICY_PATH
    with resolved.open("rb") as fh:
        return tomllib.load(fh)


def _read_calls(ledger: Path) -> list[dict]:
    """全量读回调用记录。P0-a 的调用量级（一拍至多 `calls_per_cycle` 次，
    目前 12）不需要增量读取或索引——与 `fitness.degenerate_probes()` 同一个
    「不为想象中的规模预先写复杂度」的判断。"""
    ledger = Path(ledger)
    if not ledger.is_file():
        return []
    rows = []
    with ledger.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def check(ledger: Path, cycle: int, *, policy: dict | None = None) -> str | None:
    """I1 的求值点。返回违规描述（拒绝理由，**可能带具体数字**）或 `None`（放行）。

    **调用方注意**：这里返回的字符串专供土壤侧记录/打日志——它含有当前计数与
    上限这类具体数字。`substrate/model_gateway.py` 绝不能把这串话原样转给种子；
    §8.1.3 的「不暴露配额数字」由网关那一侧负责，本函数只负责判定本身。

    `policy=None` 时现读 `soil/model-policy.toml`；调用方（网关）通常应自己
    读一次、传进来，避免每次调用都重新解析同一份 TOML。

    **策略缺失/损坏本身就是一次拒绝**，不是「查不到上限就当无限额度」——一扇
    没配置的闸和一扇不存在的闸看起来一模一样，都不该被误读成「放行」
    （C-65 同一条家族教训：缺省失败时不报错，只是悄悄指错方向）。
    """
    policy = policy if policy is not None else load_policy()
    cfg = policy.get("budget") if isinstance(policy, dict) else None
    if not isinstance(cfg, dict):
        return "budget_policy_invalid: soil/model-policy.toml 缺少 [budget] 表"

    calls_per_cycle = cfg.get("calls_per_cycle")
    window_cycles = cfg.get("window_cycles")
    calls_per_window = cfg.get("calls_per_window")
    for name, value in (("calls_per_cycle", calls_per_cycle),
                        ("window_cycles", window_cycles),
                        ("calls_per_window", calls_per_window)):
        # bool 是 int 的子类；`True == 1` 会让一个手误写的布尔值悄悄通过
        # isinstance(int) 检查，与 soil_state._validate_fitness_envelope 同一条防线。
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return f"budget_policy_invalid: {name} 缺失或非正整数（收到 {value!r}）"

    if isinstance(cycle, bool) or not isinstance(cycle, int):
        return f"budget_policy_invalid: cycle 必须是整数（收到 {cycle!r}）"

    records = _read_calls(ledger)

    cycle_count = sum(1 for r in records if r.get("cycle") == cycle)
    if cycle_count >= calls_per_cycle:
        return (f"calls_per_cycle exceeded: {cycle_count}/{calls_per_cycle} "
                f"already recorded in cycle {cycle}")

    # 滚动窗口（I1 的核心）：只数 [cycle - window_cycles + 1, cycle] 这段拍号。
    # cycle 每前移一拍，窗口左端跟着前移一拍——旧记录不需要被清理或退役，
    # 它们只是自然地滚出这个区间，不再被计入。这就是「不得全时段累计」的实现。
    window_start = cycle - window_cycles + 1
    window_count = sum(
        1 for r in records
        if isinstance(r.get("cycle"), int) and not isinstance(r.get("cycle"), bool)
        and window_start <= r["cycle"] <= cycle
    )
    if window_count >= calls_per_window:
        return (f"calls_per_window exceeded: {window_count}/{calls_per_window} "
                f"in the last {window_cycles} cycle(s) ({window_start}..{cycle})")

    return None
