"""Fitness 记录与 schema（S5, v5 §4.1 / §8.2 / §10.2）。

`pair()` 产出符合 §8.2 records[] schema 的记录；`status` 由 before/after 计算，
非由任何人声明。**配对前先比对三个版本维度**（§4.1 的强制机制）：逐 probe_id 检查
`probe_manifest_sha` / `runner_version` / `execution_policy_version`，任一不等就是
不可比 —— `status = "unmeasured"`，`delta = None`，绝不产出 `improved`。

已知的规格内部缺口（未自行裁决，见交付报告）：
- §8.2 records[] 示例含 `tree_before` / `tree_after`，但 §10.2 定义的 `ProbeRun`
  没有 tree_sha 字段，`pair()` 的入参（before/after ProbeRun 列表 + 一个 commit
  字符串）也无法派生出两个不同的树哈希。本实现不编造这两个字段。
- `degenerate_probes()` 的入参是 `ledger`（§10.2 原文），而不是更符合直觉的
  scoreboard；本实现照 `ledger` 的字面签名读 `state/soil-ledger.jsonl` 里的
  `observed_fitness` 事件（每个候选恰好写一次，不会重复计入 `accepted_fitness`
  对同一批 records 的第二次落盘）。
"""
from __future__ import annotations

import json
from pathlib import Path

#: I5：status 只能取这五个值。
STATUSES = frozenset({"improved", "no_regression", "regressed", "baseline", "unmeasured"})


def pair(before: list, after: list, commit: str) -> list:
    before_map = {p.probe_id: p for p in before}
    records = []
    for a in after:
        b = before_map.get(a.probe_id)
        record = {
            "probe_id": a.probe_id,
            "checks_after": a.checks_passed,
            "checks_total": a.checks_total,
            "measured_by": "soil",
            "commit": commit,
            "probe_manifest_sha": a.probe_manifest_sha,
            "runner_version": a.runner_version,
            "execution_policy_version": a.execution_policy_version,
            # §8.2 要求的两个树哈希，取自两次 Measurement 各自的身份（§4.1）。
            "tree_before": (b.tree_sha if b is not None else None),
            "tree_after": a.tree_sha,
        }
        if b is None:
            # 无 before：这是该 probe 第一次产出可比分数，没有基线可比。
            record.update(before=None, after=a.score, delta=None,
                          status="baseline", checks_before=None)
            records.append(record)
            continue

        record["checks_before"] = b.checks_passed
        version_mismatch = (
            b.probe_manifest_sha != a.probe_manifest_sha
            or b.runner_version != a.runner_version
            or b.execution_policy_version != a.execution_policy_version
        )
        if version_mismatch:
            # 任一版本维度不等 -> 不可比。把它算成一次进步就是 proved_better_by
            # 换了个藏身处（§4.1）。
            record.update(before=b.score, after=a.score, delta=None, status="unmeasured")
            records.append(record)
            continue

        delta = a.score - b.score
        if delta > 0:
            status = "improved"
        elif delta < 0:
            status = "regressed"
        else:
            status = "no_regression"
        record.update(before=b.score, after=a.score, delta=delta, status=status)
        records.append(record)
    return records


def has_regression(records: list) -> bool:
    """任一 record 回归即为真。§10 的 pipeline 用它决定是否走 REGRESSED 出口。

    **注意 `unmeasured` 不算回归**：不可比不是变坏。它走 §10 的 UNMEASURED 出口
    （机制故障，不计入拒绝额度），而 REGRESSED 是计额度的语义失败 —— 两者混淆
    就会把一次换 runner 版本记成种子把事情做坏了。
    """
    return any(r.get("status") == "regressed" for r in records)


# 本模块**不提供** write()。台账的唯一写入路径是 substrate/pipeline.py 的
# ctx.ledger.append()，因为 §8.2 的六个强制字段（task_id / primary_probe /
# generation / soil_cycle / calibration / counts_as_progress）只有 SoilContext
# 拿得到。一个只收 records 的 write() 填不出它们，谁调它谁就产出一条 schema
# 违规事件 —— 而 CA-7 恰恰断言每条 fitness 事件都带齐那六个字段。
# 唯一权威台账，唯一写入者（C4）。


def degenerate_probes(ledger, window: int) -> list:
    """I3：最近 window 次运行只占用 <=2 个不同档位 -> degenerate_suspected 候选。

    只读 `observed_fitness` 事件（每个候选恰好写一次），避免 `accepted_fitness`
    对同一批 records 的重复落盘把同一次测量计两次。样本不足 window 次的 probe
    证据不够，不判定（I3：粗糙的统计规则会淘汰合法量尺）。
    """
    ledger = Path(ledger)
    history: dict = {}
    if ledger.is_file():
        with ledger.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("kind") != "observed_fitness":
                    continue
                for record in event.get("records", []):
                    probe_id = record.get("probe_id")
                    after = record.get("after")
                    if probe_id is None or after is None:
                        continue
                    history.setdefault(probe_id, []).append(after)
    suspects = []
    for probe_id, scores in history.items():
        recent = scores[-window:]
        if len(recent) < window:
            continue
        if len(set(recent)) <= 2:
            suspects.append(probe_id)
    return sorted(suspects)
