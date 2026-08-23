"""土壤持久状态：唯一权威台账 · 记分板 · 跨进程晋升锁 · vault 定位。

§8.2（`state/soil-ledger.jsonl`）· §8.3（`state/soil-scoreboard.jsonl`）·
§8.1.5（前缀族命名与属主规则）· §10.2（`ctx.*` 的实际归属）

**为什么这是一个独立模块（§18 v5.10 勘误行）。** §10.2 的 pipeline 伪代码通篇使用
`ctx.ledger.append(...)` / `ctx.scoreboard.write(...)` / `with ctx.promotion_lock`，
而 §10 的土壤模块清单里**没有任何模块拥有这三样东西** —— 与 v5.9 抓到的
`model_gateway` 是同一形状的缺陷（当时的说法是「IPC 只有一端」，这里是
「上下文只有读取方」）。规格没有裁定它们住在哪里，而把它们塞进 `pipeline.py`
会让那个文件同时负责两件不同的事：**持久状态的写入机制**，与**候选处理的判决流程**。

**分开的是机制与内容，不是权限。** 唯一写入者仍然是 `substrate/pipeline.py`：
C4「种子不写台账」不因多了一个模块而松动 —— 本模块在 `substrate/` 下，
CA-4 断言 `substrate/` 不导入 `meristem`，反向种子侧也没有任何导入路径。

**event_id 是本模块新增的封套字段，理由写在这里而不是留给读者猜。**
§10.2 写的是 `oid = ctx.ledger.append({...})`，随后 `"source": oid` —— 即
append **必须返回一个标识**，且该标识必须能在台账里被重新找到，否则 CA-10 的
`accepted_fitness.source == promotion_intent.source` 这条对应关系没有东西可解析。
§8.2 的封套示例里没有这个字段，是示例只画了「一条事件长什么样」、没有画
「两条事件如何互指」。取内容哈希而不是自增计数：**自增计数需要一个全局单写者**，
而 `kind:"cycle"` 这类事件写在晋升锁之外，计数会在锁外相撞；内容哈希无此依赖，
且可在任意一份台账副本上离线重算（与 §1.2 要求判据可重放同一条理由）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from substrate import fitness

#: §8.1.5：`state/` 下的文件名必须**两端锚定**地匹配这个正则。
#: glob 式的 `soil-*` 会把 `soil-ledger.jsonl.bak` / `soil-ledger.jsonl.tmp`
#: 一并算进族里 —— 备份与半截临时文件因此获得与权威台账相同的属主与信任
#: （v5.8 外部审计 P1）。两端锚定同时封死两个方向。
STATE_FILENAME_RE = re.compile(r"^soil-[a-z0-9-]+\.jsonl$")

#: §8.2 的六个强制字段。**在写入侧拒绝，不留给读取侧发现**：CA-7 断言台账里
#: 不存在缺字段的 fitness 事件，而一个写得出违规事件的写入器，等于把那条断言的
#: 成立寄托在每个调用方的记性上。§1.2 的谓词严格下标、缺键即抛错，两侧同一纪律。
MANDATORY_FITNESS_FIELDS = ("task_id", "primary_probe", "generation",
                            "soil_cycle", "calibration", "counts_as_progress")

#: 带上述六个强制字段的两类事件（§8.2 的两阶段事件，C2）。
FITNESS_KINDS = frozenset({"observed_fitness", "accepted_fitness"})


class SoilStateError(RuntimeError):
    """土壤持久状态层的拒绝。**一律显式抛出，不静默降级** —— 本层的每一种失败
    （vault 找不到、文件名不合族、fitness 事件缺强制字段、锁拿不到）若退化成
    「继续跑但结果不对」，最终都表现为台账里多一条看起来正常的假事件。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def resolve_vault(explicit=None) -> Path:
    """定位 vault。**只从 `MERISTEM_VAULT` 读；读不到就拒绝运行**（C-65）。

    **绝不退回「相对当前目录往上一层」的缺省。** 2026-08-23 的事故正是这个缺省造成的：
    有人在 worktree 里跑了 bootstrap，`../meristem-vault` 落在 `.claude/worktrees/`
    而不是仓库外，于是 anchor cases 的完整副本躺进了工作树 —— 而 vault 存在的全部
    理由就是「物理上不可见胜过要求 prompt 不要看」。
    **那个缺省失败时不报错，只是把 vault 定位到错的地方**，这才是它真正的毒性：
    `catalogue()` 对着一个不存在的目录返回 `[]`，`run_all` 返回空清单，
    `pair()` 产出零条记录，整条流水线一路绿灯跑完 —— **一次什么都没量的测量**。

    `substrate/supervisor.py` 里那个 v3.1 时代的 `VAULT = ...os.environ.get(
    "MERISTEM_VAULT", REPO.parent / "meristem-vault")` 是同一个缺省的现存实例，
    已随本轮一并改掉。
    """
    if explicit is not None:
        candidate = Path(explicit)
    else:
        raw = os.environ.get("MERISTEM_VAULT", "").strip()
        if not raw:
            raise SoilStateError(
                "MERISTEM_VAULT 未设置。vault 必须显式给出绝对路径 —— 本层不提供任何"
                "相对路径缺省（C-65：那个缺省失败时不报错，只是把 vault 定位到错的地方）。")
        candidate = Path(raw)
    if not candidate.is_dir():
        raise SoilStateError(f"vault 路径不是目录：{candidate}")
    return candidate.resolve()


# ---------------------------------------------------------------------------
# 跨进程文件锁
# ---------------------------------------------------------------------------
try:  # POSIX
    import fcntl

    def _lock_exclusive(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _lock_exclusive(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class PromotionLock:
    """晋升锁。**必须是跨进程文件锁**（§10.2 写死）：heartbeat / manual-cycle /
    keeper 是三个不同进程，`threading.Lock` 在它们之间等于没有锁。

    锁文件不放在 `state/` —— CA-8 断言 `state/` 下每个文件都匹配
    `^soil-[a-z0-9-]+\\.jsonl$`，一个锁文件要么被断言判违规，要么需要给断言开一条
    例外，**而例外正是列举式边界漏水的方式**（v5-reset 那轮拒绝 `.gitkeep` 的同一理由）。
    """

    def __init__(self, path, timeout: float = 60.0):
        self.path = Path(path)
        self.timeout = timeout
        self._fd = None
        self._depth = 0
        self._owner = None

    def __enter__(self) -> "PromotionLock":
        # **重入必须认人。** 上一版只看 `_depth`：另一个线程在持有者的临界区内
        # 调同一个实例的 `__enter__`，会被当成重入直接放行 —— 互斥被打穿
        # （2026-08-23 对抗性审查实测赢下这个竞态）。文件锁本身是跨进程的，
        # 但同进程内的第二个线程根本走不到 `flock` 那一步就返回了。
        # 当前调用点是单线程 CLI，够不到它；而 P0-c 的无人值守守护进程够得到。
        if self._depth and self._owner == threading.get_ident():
            self._depth += 1
            return self
        if self._depth:
            raise SoilStateError(
                f"promotion lock 已被本进程另一线程（{self._owner}）持有；"
                "同一实例不得跨线程重入 —— 那会绕过互斥。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _lock_exclusive(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise SoilStateError(
                        f"promotion lock 在 {self.timeout}s 内未取得：{self.path}")
                time.sleep(0.05)
        self._fd = fd
        self._depth = 1
        self._owner = threading.get_ident()
        return self

    def __exit__(self, *exc) -> None:
        self._depth -= 1
        if self._depth:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None
            self._owner = None


# ---------------------------------------------------------------------------
# 追加写 JSONL 基类
# ---------------------------------------------------------------------------
class _AppendOnlyJsonl:
    """`state/` 下前缀族成员的共同写入机制。

    **不做 temp + rename。** §8.1.5 要求的「临时文件置于同分区他处 → O_NOFOLLOW →
    原子 rename 就位」针对的是**整文件替换**；台账是追加写的，一次 `O_APPEND`
    单次 `write()` 本身就是原子的，而且**根本不产生临时文件** —— 恰好满足同一节
    「临时文件与备份文件一律不得落在 `state/`」这条更强的要求。
    """

    def __init__(self, path):
        self.path = Path(path)
        if not STATE_FILENAME_RE.match(self.path.name):
            raise SoilStateError(
                f"{self.path.name!r} 不匹配 state/ 前缀族 ^soil-[a-z0-9-]+\\.jsonl$（§8.1.5）")

    def _append_raw(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            # §8.1.5：`state/` 下不得存在 symlink。名字合规、属主合规，内容却可能
            # 坐在种子的可写面上 —— 同一条攻击换个方向而已。
            raise SoilStateError(f"{self.path} 是 symlink，拒绝写入（§8.1.5）")
        line = json.dumps(record, ensure_ascii=False) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self.path), flags, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def read(self) -> list:
        """全量读回。台账在 P0-a 的量级下（每候选 2~4 条事件）不需要增量读取；
        真需要时再加，不预先为想象中的规模写复杂度。"""
        if not self.path.is_file():
            return []
        rows = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows


def _content_id(record: dict) -> str:
    canon = json.dumps({k: v for k, v in record.items() if k != "event_id"},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "ev-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _validate_fitness_envelope(kind: str, event: dict) -> None:
    """六个强制字段的**存在性与取值有效性**（§8.2）。

    只查「键在不在」是不够的。2026-08-23 的对抗性审查用
    `task_id=None` / `generation=None` / `soil_cycle="not-a-number"` 写进了一条
    键全在、值全是垃圾的 `accepted_fitness`，而 §1.2 的谓词按设计**不看这三个字段**
    （它是单行纯函数），于是那条垃圾事件被判为一次合法点火。
    本模块 docstring 自称「写入侧拒绝，不留给读取侧发现」——
    那句话此前只对缺键成立。**声明了没断言，就是这个项目的家族病本身。**
    """
    missing = [f for f in MANDATORY_FITNESS_FIELDS if f not in event]
    if missing:
        raise SoilStateError(
            f"{kind} 缺少 §8.2 强制字段 {missing}；缺键即台账损坏，写入侧拒绝。")
    if not isinstance(event["task_id"], str) or not event["task_id"]:
        raise SoilStateError(f"{kind}.task_id 必须是非空字符串，收到 {event['task_id']!r}")
    if not isinstance(event["primary_probe"], str) or not event["primary_probe"]:
        raise SoilStateError(
            f"{kind}.primary_probe 必须是非空字符串，收到 {event['primary_probe']!r}")
    if not isinstance(event["generation"], str) or not event["generation"]:
        raise SoilStateError(f"{kind}.generation 必须是非空字符串，收到 {event['generation']!r}")
    if isinstance(event["soil_cycle"], bool) or not isinstance(event["soil_cycle"], int):
        raise SoilStateError(f"{kind}.soil_cycle 必须是整数，收到 {event['soil_cycle']!r}")
    if not isinstance(event["calibration"], bool):
        raise SoilStateError(f"{kind}.calibration 必须是 bool，收到 {event['calibration']!r}")
    if not isinstance(event["counts_as_progress"], bool):
        raise SoilStateError(
            f"{kind}.counts_as_progress 必须是 bool，收到 {event['counts_as_progress']!r}")
    records = event.get("records")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise SoilStateError(f"{kind}.records 必须是对象数组，收到 {type(records).__name__}")
    for index, record in enumerate(records):
        _validate_fitness_record(kind, index, record)


def _validate_fitness_record(kind: str, index: int, record: dict) -> None:
    """`records[]` 单条元素的 schema（§8.2）。

    **写入侧与判据侧必须对得上。** §1.2 的谓词严格下标 `r["probe_id"]` / `r["status"]`
    —— 那是规格要求的（缺键即台账损坏，不许读者猜方向）。但上一版的写入侧只校验
    「records 是对象数组」，于是 `records: [{}]` 能写进去，而
    `ignition-status`（判据的**唯一**求值点）读到它就抛 `KeyError` 崩掉。
    **一个写得进去、却读不出来的台账行，就是把崩溃留给最需要读数的那一刻。**
    2026-08-23 第三份独立审查复现了这条。

    `before` / `delta` / `checks_before` 允许为 `None`：`fitness.pair()` 在
    `baseline`（没有前值可比）与 `unmeasured`（版本维度不匹配）两种合法情形下
    就是产出 `None`，把它们判成非法会让 runner 自己写不出自己的记录。
    """
    where = f"{kind}.records[{index}]"
    probe_id = record.get("probe_id")
    if not isinstance(probe_id, str) or not probe_id:
        raise SoilStateError(f"{where}.probe_id 必须是非空字符串，收到 {probe_id!r}")
    status = record.get("status")
    if status not in fitness.STATUSES:
        raise SoilStateError(
            f"{where}.status 必须属于 I5 枚举 {sorted(fitness.STATUSES)}，收到 {status!r}")
    for field in ("before", "after", "delta"):
        value = record.get(field, "__missing__")
        if value == "__missing__":
            raise SoilStateError(f"{where} 缺字段 {field!r}")
        if value is not None and not isinstance(value, (int, float)):
            raise SoilStateError(f"{where}.{field} 必须是数字或 None，收到 {value!r}")
    # 三个版本维度是可比性的前提（§4.1 / §8.3）。缺了它们，不可比会静默发生 ——
    # 规格把这一条叫做「又一个声明了没断言」。
    for field in ("probe_manifest_sha", "runner_version", "execution_policy_version"):
        if not isinstance(record.get(field), str) or not record.get(field):
            raise SoilStateError(f"{where}.{field} 必须是非空字符串（§4.1 可比性三维度）")


class Ledger(_AppendOnlyJsonl):
    """`state/soil-ledger.jsonl` —— 唯一权威台账，只由土壤写入（§8.2 / C4）。

    **校验落在 `_append_raw` 上，不落在 `append` 上。** 上一版把校验放在 `append`，
    而 `_append_raw` 继承自共用基类且完全不校验 —— 于是
    `ctx.ledger._append_raw({...})` 一行就能写出一条被 §1.2 判据认可、
    却从未测量过也从未 merge 过的 `accepted_fitness`。
    **一道能被同一个对象上的另一个方法绕开的闸门，不是闸门。**
    """

    def _append_raw(self, record: dict) -> None:
        kind = record.get("kind")
        if not kind:
            raise SoilStateError("台账事件必须带 kind（§8.2 封套）")
        if kind in FITNESS_KINDS:
            _validate_fitness_envelope(kind, record)
        super()._append_raw(record)

    def append(self, event: dict) -> str:
        record = {"ts": _utcnow()}
        record.update(event)
        record["event_id"] = _content_id(record)
        self._append_raw(record)
        return record["event_id"]


class Scoreboard(_AppendOnlyJsonl):
    """`state/soil-scoreboard.jsonl` —— 历史 measurement 台账（§8.3）。

    **不是当前 baseline 缓存**：任何比较都必须回到树上现测，不得从台账取数当基准。
    """

    def write(self, runs, commit: str, parent_sha: str) -> int:
        """**每次晋升写全套**，不是逐拍抽样 —— S2 的直接实现（v3.1 的 13/16 陈旧问题）。"""
        for run in runs:
            self._append_raw({
                "ts": _utcnow(),
                "kind": "probe",
                "probe_id": run.probe_id,
                "score": run.score,
                "checks_passed": run.checks_passed,
                "checks_total": run.checks_total,
                "tree_sha": run.tree_sha,
                "commit": commit,
                "parent_sha": parent_sha,
                # 三个版本维度是可比性的前提，与 §4.1 的 Measurement 身份逐字相同。
                "probe_manifest_sha": run.probe_manifest_sha,
                "runner_version": run.runner_version,
                "execution_policy_version": run.execution_policy_version,
                "source": "promotion-full-set",
            })
        return len(runs)


@dataclasses.dataclass
class SoilContext:
    """§10.2 的 `ctx`：pipeline 的全部外部依赖，**无隐式全局**。

    `generation` / `soil_cycle` / `calibration` 是 §8.2 六个强制字段中三个的来源；
    它们住在上下文里而不是从环境变量现读，是为了让同一次 `process_candidate`
    写出的每条事件都带**同一个**拍号与世代 —— 现读会让一次跨午夜的运行写出两个值。
    """

    repo: Path
    vault: Path
    ledger: Ledger
    scoreboard: Scoreboard
    promotion_lock: PromotionLock
    generation: str
    soil_cycle: int
    calibration: bool = False
    policy: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def open(cls, repo, *, generation: str, soil_cycle: int,
             calibration: bool = False, vault=None, state_dir=None) -> "SoilContext":
        repo = Path(repo)
        state = Path(state_dir) if state_dir is not None else repo / "state"
        return cls(
            repo=repo,
            vault=resolve_vault(vault),
            ledger=Ledger(state / "soil-ledger.jsonl"),
            scoreboard=Scoreboard(state / "soil-scoreboard.jsonl"),
            # 锁文件刻意放在 state/ 之外（见 PromotionLock 的 docstring）。
            promotion_lock=PromotionLock(repo / "soil" / ".promotion.lock"),
            generation=generation,
            soil_cycle=soil_cycle,
            calibration=calibration,
        )
