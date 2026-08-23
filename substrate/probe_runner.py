"""土壤探针运行器（S3, v5 §9.2 / §10.2 / §15.6 C6 / §15 C1）。

土壤读 vault、执行声明式 Internal Probe manifest、算 passed/total。种子侧不得出现
任何探针运行代码或 vault 路径常量 —— 本模块是唯一读写 vault 的地方（原先只读；
`validate_proposal()` / `freeze_into_vault()` 加入之后也写——见两者各自的
docstring：写入动作仍然只在本模块内部发生，「vault 路径只有一个入口」这条边界
没有因为多了写入而松动，只是那个入口现在既读也写）。

判定路径：input -> organ ABI (subprocess) -> output -> cmp(output, expect)。

**C1 冻结契约的土壤半**（§15 C1）：`validate_proposal()` 校验 seed 提案
（§8.1.1 schema + I2 的 ≥5 检查数），`freeze_into_vault()` 把校验通过的
manifest 写进 vault 并返回 `probe_manifest_sha`。冻结登记本身（谁/何时/
`eligible_after`）不在本模块——那是 `substrate/soil_state.FrozenProbeRegistry`
的职责（登记是「土壤持久状态」，不是「vault 内容」，见其 docstring）；
把两件事的编排串起来（读提案文件 -> 校验 -> 写 vault -> 写登记）的是
`substrate/pipeline.freeze_proposal()`。

执行契约（§15.6 C6，P0-a 只做「最小完整性隔离」，不做 namespace/seccomp）：
organ 是种子拥有的代码，不得直接跑在土壤权威进程内 —— subprocess + 严格 JSON ABI +
不传 secrets（裁剪环境变量）+ 不传 vault 路径（本模块从不把 vault 交给 organ）+
墙钟超时 + 非法输出记 `unmeasured`（不是 0 分：0 分在后续修好后会被读成一次
`improved`，凭空制造点火事件，见 §15.6 末尾）。

**隔离分两级，级别可被读出（`isolation_status()`），不靠注释声明。**

- `enforced`（Linux + root + `unshare`/`setpriv` + `worker` 账户）：
  organ 跑在 **worker UID、无附加组、私有网络命名空间**里，且只看得见
  **自己的隔离工作副本**。配合 vault `soil:0500` 与 `state/soil-*.jsonl` `soil:0600`，
  §15.6 P0-a 档的三条（vault 不可读 · ledger 不可写 · 禁网）**由操作系统强制**。
- `best_effort`（Windows / 非 root / 缺 util-linux / 无 worker 账户）：
  退回「subprocess + 裁剪环境 + 超时」。此时 **vault 可被绝对路径读取、
  台账可被写入、可联网** —— 这不是理论风险，是复现过的攻击。
  **本级别下不得声称满足 §15.6 的 P0-a 档。**

写明而不是假装做到：§15.6 自己要求，环境兑现不了时必须列出哪些隔离只是 best effort。

organ 入口路径（`body/organs/<organ>/run.py`）与输出 ABI（恰好一个字符串值的
JSON 对象，key 名不固定）现由规格写死（§10.2「organ 入口与输出 ABI」），不再是
本模块自己的假设——此前这份约定只分别记在本模块与 body/organs/classifier/run.py
两处 docstring 里，「两处各写一份应保持一致」正是 §17.7 点名的语义漂移温床。
这里选的形状经与仓库里已存在的 body/organs/classifier/run.py（只读，未修改）
核对一致。
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RUNNER_VERSION = "1"

#: **由 "1" 升到 "2"：执行策略变了，旧分数与新分数不可直接比较。**
#: `execution_policy_version` 是 Measurement 身份的三个维度之一（§4.1），
#: 它存在的全部理由就是让这种不可比**显式发生**而不是静默发生。
#: v1 = subprocess + 裁剪环境 + 超时（最小完整性隔离**未兑现**）。
#: v2 = 外加 worker UID 降权 + 私有网络命名空间 + organ 独立工作副本。
EXECUTION_POLICY_VERSION = "2"

PROBE_TIMEOUT_SECONDS = 5.0

#: §15.6 的执行身份模型：organ / rubric worker 跑在 `worker` UID，**无附加组**。
WORKER_USER = "worker"

#: cmp 白名单（§8.1.1）。regex 只接受这里预置的命名正则，不接受种子提供的字面量。
#: P0-a 尚无 internal probe 用到 regex；新增时在此登记，不接受调用方传入的模式串。
NAMED_REGEXES: dict[str, str] = {}

#: 最小完整性隔离：不继承调用者环境，只留跑通 python 解释器所需的系统变量。
_ENV_ALLOWLIST = ("PATH", "SystemRoot", "SYSTEMROOT", "windir", "COMSPEC", "TEMP", "TMP")


@dataclasses.dataclass
class ProbeRun:
    probe_id: str
    score: float
    checks_passed: int
    checks_total: int
    detail: list
    # §4.1 Measurement 身份的四项，必须在测量时刻产出，不能事后补。
    probe_manifest_sha: str
    tree_sha: str
    runner_version: str
    execution_policy_version: str


def _manifest_sha(manifest: dict) -> str:
    canon = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _tree_sha(tree: Path) -> str:
    """被测树的身份（§4.1）。优先取 git 的 tree 对象；不是 git 树时退回内容哈希。

    没有它，§8.2 的 tree_before / tree_after 填不出来 —— 那两个字段正是「这两次测量
    确实跑在不同的树上」的唯一凭据。
    """
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=str(tree),
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    h = hashlib.sha256()
    for p in sorted(x for x in tree.rglob("*") if x.is_file() and ".git" not in x.parts):
        h.update(str(p.relative_to(tree)).replace("\\", "/").encode("utf-8"))
        h.update(p.read_bytes())
    return "content:" + h.hexdigest()


def _sandboxed_env() -> dict:
    return {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}


def isolation_status() -> tuple:
    """本机能否兑现 §15.6 的「最小完整性隔离」。返回 `(level, reason)`。

    `level` ∈ `{"enforced", "best_effort"}`。**必须能被读出来**：隔离是否真的生效
    只存在于运行环境里，而台账上的分数长得一模一样 —— 不把它读出来，
    「已隔离」就又是一句声明了没断言的话。§15.6 也明确要求，
    环境兑现不了时必须写明**哪些隔离只是 best effort**。
    """
    if os.name != "posix":
        return "best_effort", f"platform is not POSIX (os.name={os.name!r})"
    if os.geteuid() != 0:
        return "best_effort", "no privilege to drop to a separate worker UID"
    if shutil.which("unshare") is None or shutil.which("setpriv") is None:
        return "best_effort", "util-linux unshare/setpriv unavailable"
    try:
        import grp
        import pwd
        entry = pwd.getpwnam(WORKER_USER)
    except (ImportError, KeyError):
        return "best_effort", f"no {WORKER_USER!r} account on this host"
    # **附加组必须当场核实，不能只核实账户存在。** 「worker 无附加组」是整个隔离
    # 论证的地基（§15.6 逐字写着「无附加组」），而它此前只在**创建账户那一刻**成立：
    # 一个早已存在的、带着附加组的同名账户会被照单全收，然后 `isolation_status()`
    # 照样报 `enforced`。**一个只在首次部署时为真的不变量，不是不变量。**
    extra = sorted(g.gr_name for g in grp.getgrall()
                   if WORKER_USER in g.gr_mem and g.gr_gid != entry.pw_gid)
    if extra:
        return "best_effort", (f"{WORKER_USER!r} has supplementary groups {extra} — "
                               "§15.6 requires none; they are the shortest path to "
                               "extra read access")
    return "enforced", (f"organ runs as {WORKER_USER} (uid={entry.pw_uid}, no "
                        f"supplementary groups) in a private network namespace; "
                        f"soil euid={os.geteuid()}")


def _isolation_prefix() -> list:
    """argv 前缀：私有网络命名空间 + 降权到 worker。

    `--fork --kill-child` 是为了超时能真的杀干净：`subprocess.run(timeout=...)`
    只杀直接子进程（`unshare`），中间隔一层的话孙子进程会活下来 ——
    **一个杀不掉的 organ 就是 §15.6 要防的「卡死 runner」**。
    """
    level, _ = isolation_status()
    if level != "enforced":
        return []
    return ["unshare", "--net", "--fork", "--kill-child", "--",
            "setpriv", "--reuid", WORKER_USER, "--regid", WORKER_USER, "--clear-groups"]


class OrganRefused(Exception):
    """organ 的形状本身不可信，这一次测不出来（走 `unmeasured` 出口）。"""


def _refuse_links_in_organ(organ_dir: Path) -> None:
    """organ 目录里**不得存在任何链接**，有就拒绝执行。

    **这是主防线，`symlinks=True` 只是纵深。** 理由：organ 是种子拥有的代码
    （`body/organs/` 在种子可写白名单上），而任何遍历它的土壤侧动作都带着土壤的
    权限。保住「不解引用」只挡住了复制这一条路径；**下一个遍历它的人未必记得**
    ——校验和、打包、渲染报告、统计行数，每一个都是同一个洞的新入口。

    organ 没有任何正当理由需要链接：它是一个 `input -> label` 的纯函数，
    §10.2 把入口写死为 `body/organs/<organ>/run.py`。**不列举攻击，直接不允许这类文件。**
    """
    if not organ_dir.is_dir():
        return
    for path in [organ_dir, *organ_dir.rglob("*")]:
        if path.is_symlink():
            raise OrganRefused(
                f"organ 目录含符号链接，拒绝执行：{path.relative_to(organ_dir.parent)}")
        try:
            attributes = os.stat(path, follow_symlinks=False).st_file_attributes
        except (OSError, AttributeError):
            continue
        if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT（junction 等）
            raise OrganRefused(
                f"organ 目录含 reparse point，拒绝执行：{path.relative_to(organ_dir.parent)}")


@contextlib.contextmanager
def _isolated_organ(entrypoint: Path):
    """把 organ 复制成**它自己的隔离工作副本**再执行，用完即毁。

    §15.6 的执行身份模型逐字写着 worker「只能读写自己的隔离工作副本」——
    这就是那句话的实现。**不直接在候选树里跑**：那棵树属主是土壤，
    要让 worker 读得到就得放宽整棵树的权限，等于为了跑一个 organ
    把整棵被测树对本机所有用户敞开。复制一份更小、更好收回，
    而且天然满足「只读自己那份」。
    """
    _refuse_links_in_organ(entrypoint.parent)
    staging = Path(tempfile.mkdtemp(prefix="meristem-organ-"))
    try:
        work = staging / "organ"
        # **`symlinks=True` 不可省。** 默认的 `symlinks=False` 会**解引用**链接：
        # 把目标的内容读出来、落成普通文件 —— 而这次读取发生在**土壤进程里、
        # 降权之前**，土壤有 vault 的完整读权限。于是种子只要在自己的 organ 目录里
        # 放一个指向 vault 的链接，vault 就被搬进 worker 自己的工作副本，
        # organ 再用一个普通相对路径读走它。**为隔离而加的这一步，
        # 恰好成了绕过隔离的那条路**（2026-08-23 独立审查实测，单文件与整个
        # vault 子树两种都成立）。链接指向目录时 copytree 会递归进去，
        # 一个链接就够把整棵 vault 复制出来。
        shutil.copytree(entrypoint.parent, work, symlinks=True)
        level, _ = isolation_status()
        os.chmod(staging, 0o755)
        for path in [work, *work.rglob("*")]:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        if level == "enforced":
            # 副本归 worker —— 它可以读写自己这份，仅此而已。
            for path in [staging, work, *work.rglob("*")]:
                shutil.chown(path, user=WORKER_USER, group=WORKER_USER)
        yield work / entrypoint.name
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _entrypoint(tree: Path, organ: str) -> Path:
    # 固定路径，规格写死（§10.2「organ 入口与输出 ABI」；没有 entrypoint 字段，
    # 见 §8.1.1）：body/organs/<organ>/run.py。与本仓库现有的
    # body/organs/classifier/run.py 对齐（读取，未修改；本模块不允许碰 body/）。
    return Path(tree) / "body" / "organs" / organ / "run.py"


def _invoke(entrypoint: Path, check_input) -> tuple[bool, str]:
    """input -> organ ABI -> output。返回 (ok, output_or_reason)。

    输出 key 的名字不固定（分类器用 "category"，规格里 "output" 只是抽象说法，
    不是字面 JSON key）——因此只认「恰好一个字符串值的 JSON 对象」这一种形状，
    不认 key 的名字。这样跑不同 organ 不需要为每个 organ 的语义 key 改 runner。
    """
    # 必须先转绝对路径。argv 里留相对路径而 cwd 又切到 organ 目录，Python 会拿新的
    # cwd 去解析它 —— 得到一个翻倍的、不存在的路径，于是每个 check 都非零退出、
    # 全部记 unmeasured。这不会报错，只会静默地把管道故障变成一次「测量」。
    entrypoint = entrypoint.resolve()
    if not entrypoint.is_file():
        return False, "entrypoint missing"
    started = time.monotonic()
    try:
        with _isolated_organ(entrypoint) as isolated:
            # **超时预算要盖住准备阶段，不只是 exec。** `_isolated_organ` 的复制与
            # chmod/chown 遍历发生在 `subprocess.run` 之前，原先完全不在任何时限内 ——
            # 一个够大（或够坏）的 organ 目录能在那里把 runner 拖住，
            # 而 `--kill-child` 那套只管 exec 之后。**卡死 runner 是 §15.6 点名要防的。**
            remaining = PROBE_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                return False, "timeout preparing isolated organ copy"
            result = subprocess.run(
                [*_isolation_prefix(), sys.executable, str(isolated)],
                input=json.dumps({"input": check_input}),
                capture_output=True, text=True,
                timeout=remaining,
                cwd=str(isolated.parent),
                env=_sandboxed_env(),
            )
    except OrganRefused as exc:
        # 形状不可信 -> 这一次测不出来（unmeasured），不是 0 分。
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as exc:
        return False, f"exec error: {exc}"
    if result.returncode != 0:
        return False, f"nonzero exit {result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "non-JSON stdout"
    if not isinstance(payload, dict) or len(payload) != 1:
        return False, "output ABI violation"
    (output,) = payload.values()
    if not isinstance(output, str):
        return False, "output ABI violation"
    return True, output


def _cmp(kind: str, output: str, expect: str) -> bool:
    if kind == "equals":
        return output == expect
    if kind == "contains":
        return expect in output
    if kind == "regex":
        pattern = NAMED_REGEXES.get(expect)
        if pattern is None:
            raise ValueError(f"unregistered named regex: {expect!r}")
        return re.search(pattern, output) is not None
    raise ValueError(f"cmp not in whitelist: {kind!r}")


def catalogue(vault):
    """**全套探针**：internal（`internal/active/`）+ anchor（`anchors/`）。

    `run_all` 量的就是这一套 —— §12.2 写死了 anchor 的非对称处置
    「回归即拒 / 上升不加分」，而**「回归即拒」要成立，anchor 必须真的在被测集合里**：
    `run_all` 本就跑全套、`has_regression` 对任一 probe 触发，此处只是把 anchor
    接进那个「全套」。接线之前，反过拟合是一句形容词。

    需要区分类别时用 `catalogue_by_class()` —— `primary_probe` 的校验必须按**来源**
    判别 anchor，不能靠它缺席（见该函数的 docstring）。

    **返回 None 表示这份清单这一次编不出来**——与 `run_probe` / `run_all` 已有的
    「一把坏尺，整轮 UNMEASURED」契约一致，而不是悄悄跳过那一条、把其余的凑成
    一份清单交出去。

    **判定原则（不是清单）**：只要有任何一个 probe 目录没能产出一份可信的
    manifest，就返回 None。**刻意不写成穷举列表** —— 上一版这里写「三种情形」，
    独立审查随即找出了第四种（合法 JSON 但不是对象：`42` / `null` / 数组 /
    裸字符串），而那种情形当时会抛未捕获的 TypeError。**一句穷举断言只要漏一项
    就是假的**，而假注释比没有注释更坏。当前实际拦下的包括但不限于：

    - `probe.json` 读不到（缺文件 / 不是普通文件）
    - 解析不出合法 JSON
    - 是合法 JSON 但不是对象
    - 含 `entrypoint` 字段（§8.1.1：种子可写 schema 里没有这个字段，出现即拒绝
      —— CA-5 的语义是「出现即拒绝」，不是「出现即假装没看见」）

    > **注**：`entrypoint` 这一道目前是**唯一**在跑的一道，不是「第二层」。
    > `substrate/supervisor.py` 的 `promote_probes()` 里没有对应检查，且它读的是
    > `state/probe-proposals`（v3.1 的路径），而 v5 §10.1 把提案面定在
    > `seed/probe-proposals/` —— CA-5 扫的正是后者。**两个路径对不上，是 §13.3
    > 列的 supervisor 波次 2 改造项，不是这里能修的。**

    原先这三处是 `continue`：一把坏尺被静默略过，`run_all` 拿到的是一份**少一把
    尺的清单**——而 `run_all` 自己的契约明写「不做跳过坏的、凑一批好的，那样
    before 与 after 会覆盖不同的探针集合，两边根本不可比，且 `fitness.pair()`
    对此毫无察觉」。catalogue 曾经在做 run_all 声明绝不做的事；现在两者一致：
    一把坏尺 → 这一次整份清单都编不出来。

    vault 尚未存在（还没冻结过任何 probe）不算「坏」，返回空列表 `[]`——这是
    合法的初始状态，不是某把尺读不出来。
    """
    by_class = catalogue_by_class(vault)
    if by_class is None:
        return None
    return by_class["internal"] + by_class["anchor"]


#: vault 里两类探针各自的位置（§8.1 的二分：internal 冻结后入 vault，anchor 土壤私有）。
#: **两类共享同一个 Probe schema**（§12.2「anchor 不设 I2 豁免」），
#: 因此走同一个 runner、同一套形状校验，只有**来源目录**不同。
INTERNAL_SUBPATH = ("internal", "active")
ANCHOR_SUBPATH = ("anchors",)


def catalogue_by_class(vault):
    """按类别返回 `{"internal": [...], "anchor": [...]}`；任一把坏尺 → `None`。

    **为什么要分类别，而不是给 `catalogue()` 加个 anchor 目录了事。**
    §8.1.4 硬性要求 `primary_probe` 必须是 internal —— anchor 不得被声明为 primary，
    否则 evaluator 面前摆着两把尺，哪把有利就用哪把。
    上一版 `validate_task()` 是靠「在清单里找不到就拒绝」来实现这条的，
    而那份清单当时**根本不含 anchor** —— 也就是说它挡住 anchor 靠的是 anchor 缺席。
    **靠缺席做判别，正是 anchor 一进入被测集合就会静默失效的写法。**
    分类别之后，判别依据变成来源目录，anchor 在场也照样被拒。
    """
    result = {}
    for name, subpath in (("internal", INTERNAL_SUBPATH), ("anchor", ANCHOR_SUBPATH)):
        scanned = _scan_probe_dir(Path(vault).joinpath(*subpath))
        if scanned is None:
            return None
        result[name] = scanned
    return result


def _scan_probe_dir(root: Path):
    manifests = []
    if not root.is_dir():
        return manifests
    for probe_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = probe_dir / "probe.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict):
            # 合法 JSON 但不是对象（`42` / `null` / `true` / 裸字符串 / 数组）。
            # 少了这一行，下一行的 `in` 会对 int/None/bool 抛 TypeError 炸穿 run_all，
            # 而数组和字符串更坏 —— 它们支持 `in`，于是悄悄混进清单，直到 run_probe
            # 才崩。**崩溃比「少一把尺的清单」更糟**：它连优雅退化成 UNMEASURED
            # 都做不到。形状不对与字段缺失走同一条 None 出口。
            return None
        if "entrypoint" in manifest:
            return None
        manifests.append(manifest)
    return manifests


#: 一个 check 必须带齐的字段与类型（§8.1.1）。
#: **`cmp` 只认白名单**，与 `_cmp()` 的白名单是同一份来源：那里对未知 kind 抛
#: ValueError，这里在跑之前就把它挡下来，两处不得各写各的。
_CMP_WHITELIST = ("equals", "contains", "regex")


def _checks_well_formed(checks: list) -> bool:
    """逐 check 校验。**不校验数量** —— I2（≥5 个子检查）的执行点在
    「土壤对 seed 提案文件做 schema 校验」（§5 的 I2 行、§7 的 `author_probe`），
    也就是**冻结路径**，不是运行时。在 runner 入口再加一道数量检查，会把
    「这把尺不合法」与「这把尺这次测不出来」两件事混成同一个出口，
    而它们对应完全不同的处置。**I2 的执行点是 `validate_proposal()`**
    （见下方，C1 冻结契约的一部分）；本函数继续只做逐 check 的形状校验，
    两者的字段/白名单规则共用同一份（`validate_proposal` 直接调用本函数），
    但「数量」这条只在冻结路径查一次，不在这里查——运行时不该因为一把已经
    冻结过的尺凑巧少了一个 check（不该发生，但不该在这里假设它不会发生）
    就把「这次测不出来」误判成别的东西。
    """
    seen = set()
    for check in checks:
        cid = check.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            return False
        seen.add(cid)
        if not isinstance(check.get("input"), str):
            return False
        if check.get("cmp") not in _CMP_WHITELIST:
            return False
        if not isinstance(check.get("expect"), str):
            return False
    return True


#: I2（§5）：一个 Probe 必须声明 ≥5 个子检查，且分数 = 通过数/总数×100。
#: **执行点是 `validate_proposal()`**（冻结路径），不是运行时——见
#: `_checks_well_formed` 的 docstring。
MIN_CHECKS = 5


class ProbeProposalError(ValueError):
    """提案不满足 §8.1.1 的种子可写 schema，或不满足 I2 的 ≥5 检查数，
    土壤在**冻结点**拒绝（C1：`author_probe` 能力的土壤半）。

    与 `OrganRefused` 是两个不同的拒绝：`OrganRefused` 是「这把已经冻结过的
    尺，这一次测不出来」（运行时，走 unmeasured 出口，不是错误）；
    `ProbeProposalError` 是「这份提案本身不合法，永远不会成为一把尺」
    （冻结时，是真的错误，调用方应当拒绝整个冻结请求）。
    """


def validate_proposal(manifest) -> None:
    """§8.1.1 的种子可写 schema 校验 + I2 的 ≥5 检查数校验。**这是 I2 的
    唯一执行点**（`_checks_well_formed` 故意不做这件事，见其 docstring；
    §5 的 I2 行、§7 的 `author_probe`：「土壤校验后写冻结登记与 vault
    manifest」——这里就是那个「校验」）。

    与 `catalogue()` 对**已冻结** manifest 的校验共享 `_checks_well_formed()`
    （同一份 cmp 白名单、同一份 check 形状规则，不各写各的），但本函数额外做
    两件 `catalogue()` 不做的事：

    1. **数量检查（I2）**——`catalogue()` 信任已经通过过一次冻结校验的 vault
       内容，只查 entrypoint 与 checks 的形状；提案还没有这层信任，必须在
       这里把 I2 的门槛真正挡住。
    2. **顶层字段完整性**（`id` / `capability` / `organ` 必须是非空字符串）
       ——`catalogue()` 的 manifest 已经是「曾经通过冻结」的产物，这些字段
       不会缺；提案可能缺，必须现查。

    **不返回 bool，直接抛 `ProbeProposalError`**：冻结路径没有「这次测不出来，
    先记 unmeasured，下次再试」这种中间状态——提案不合法就是不合法，拒绝
    整个冻结请求即可，异常携带的原因文本比一个 `False` 更有用（调用方是人
    或种子，都需要知道具体哪条不满足，而不是自己重新猜一遍）。
    """
    if not isinstance(manifest, dict):
        raise ProbeProposalError(f"提案不是 JSON 对象，收到 {type(manifest).__name__}")
    probe_id = manifest.get("id")
    if not isinstance(probe_id, str) or not probe_id:
        raise ProbeProposalError(f"提案缺 id 或 id 不是非空字符串，收到 {probe_id!r}")
    if not isinstance(manifest.get("capability"), str) or not manifest.get("capability"):
        raise ProbeProposalError(f"{probe_id}: 缺 capability 或不是非空字符串")
    if not isinstance(manifest.get("organ"), str) or not manifest.get("organ"):
        raise ProbeProposalError(f"{probe_id}: 缺 organ 或不是非空字符串")
    if "entrypoint" in manifest:
        # CA-5 的语义是「出现即拒绝」（§8.1.1）：种子可写 schema 里没有这个
        # 字段，出现即说明提案在申请一条种子在土壤内执行任意代码的入口。
        raise ProbeProposalError(
            f"{probe_id}: 含 entrypoint 字段，种子可写 schema 里不存在这个字段（§8.1.1）")
    checks = manifest.get("checks")
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise ProbeProposalError(f"{probe_id}: checks 必须是对象数组")
    if not _checks_well_formed(checks):
        raise ProbeProposalError(
            f"{probe_id}: 存在不合规的 check（字段缺失、id 重复，或 cmp 不在白名单，§8.1.1）")
    if len(checks) < MIN_CHECKS:
        raise ProbeProposalError(
            f"{probe_id}: 只有 {len(checks)} 个 check，I2 要求 ≥{MIN_CHECKS} 个"
            "（§5 I2；冻结路径是 I2 唯一的执行点）")


def freeze_into_vault(vault, manifest: dict) -> str:
    """把**已校验**的 manifest 写进 `vault/internal/active/<id>/probe.json`。
    调用方须先调用 `validate_proposal()`——本函数不重复那份校验，只做
    「写入 + 拒绝重复」，理由见下方对 `probe_id` 前置检查的说明。

    返回 `probe_manifest_sha`（`_manifest_sha(manifest)`）——**与 `run_probe()`
    测量时对同一份 manifest 计算 `ProbeRun.probe_manifest_sha` 用的是同一个
    函数**。C1 要求「冻结登记的 `frozen_probe_manifest_sha` 必须等于该 probe
    每一次 Measurement 的 `probe_manifest_sha`」（CA-9）；这里让两处调用同一个
    纯函数对同一份数据求值，是这条相等成立的**构造性保证**，不是写完之后
    再核对出来的巧合——即便 vault 上落盘的 JSON 格式（indent、字段顺序）与
    调用方内存里的 manifest 不完全一样，`_manifest_sha()` 内部用
    `sort_keys=True` 规范化后再取哈希，格式差异不影响结果。

    **拒绝覆盖已存在的 probe_id。** C1：同一 probe_id 的 manifest 不得变化；
    改 active probe 必须新建 probe_id。这里用「vault 目录已存在即拒绝」实现，
    **不比较内容是否相同**——冻结是一次性事件，不是幂等的 upsert：即便种子
    提交了逐字节相同的内容，重复冻结同一个 id 仍然拒绝，因为「内容相同」
    这件事本身就不该由种子的提案来主张。
    """
    probe_id = manifest.get("id") if isinstance(manifest, dict) else None
    if not isinstance(probe_id, str) or not probe_id:
        raise ProbeProposalError(
            "freeze_into_vault 需要一个已通过 validate_proposal() 校验的 manifest")
    probe_dir = Path(vault) / "internal" / "active" / probe_id
    if probe_dir.exists():
        raise ProbeProposalError(
            f"{probe_id}: vault 中已存在该 probe_id，拒绝再次冻结"
            "（C1：同一 probe_id 的 manifest 不得变化；改 active probe 必须新建 probe_id）")
    probe_dir.mkdir(parents=True)
    manifest_path = probe_dir / "probe.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    return _manifest_sha(manifest)


def run_probe(manifest: dict, tree):
    """input -> organ ABI -> output -> cmp(output, expect)。score = passed/len(checks)*100。

    **返回 None 表示这把尺这一次测不出来**（§10 pipeline 的 `before is None or
    after is None` 出口，Outcome.UNMEASURED）。两种情形返回 None：

    1. manifest 缺必需字段 —— 坏的 manifest 不得连累其它探针（原先直接 KeyError,
       会从 run_all 的列表推导里炸出去,一把坏尺让整轮测量全灭）。
    2. **任何一个 check 记了 unmeasured** —— 见下方那段。
    """
    try:
        checks = manifest["checks"]
        organ = manifest["organ"]
        probe_id = manifest["id"]
    except (KeyError, TypeError):
        # TypeError：manifest 根本不是 dict（下标操作抛的）。缺字段与形状不对
        # 是同一类事故 —— 这把尺读不出来 —— 必须走同一条 None 出口，不能一条
        # 优雅退化、另一条抛异常炸穿调用者。
        return None
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        # `"checks": null` 或 checks 里混了非对象元素。少了这一行，下面的 for
        # 循环或 check["input"] 会抛 TypeError —— 同一个洞换个位置。
        return None
    if not _checks_well_formed(checks):
        # **逐 check 校验，与「形状不对」走同一条 None 出口。**
        # 少了这一段，一个缺 `cmp` 的 check 会在下面的 `check["cmp"]` 抛
        # 未捕获的 KeyError，从 run_all 的循环里炸穿到 pipeline —— 而规格要求的
        # 是记 `unmeasured`。**优雅退化与抛异常必须选一个，不能一半一半**：
        # 上面刚为 manifest 根对象写过同一条理由，checks 的元素当时没跟上。
        return None
    entrypoint = _entrypoint(Path(tree), organ)
    detail = []
    passed = 0
    unmeasured = 0
    for check in checks:
        ok, out = _invoke(entrypoint, check["input"])
        if ok:
            try:
                hit = _cmp(check["cmp"], out, check["expect"])
            except ValueError as exc:
                ok, out = False, str(exc)
        if not ok:
            # 非法输出（含 organ 因读不到 secrets 而崩溃）—— 记 unmeasured，不是 fail/0。
            detail.append({"id": check["id"], "result": "unmeasured", "reason": out})
            unmeasured += 1
            continue
        if hit:
            passed += 1
            detail.append({"id": check["id"], "result": "pass"})
        else:
            detail.append({"id": check["id"], "result": "fail", "got": out})
    if unmeasured:
        # **一条 check 测不出来，这把尺这一次就没有可信分数。**
        #
        # 原先只把 unmeasured 记进 detail，score 照样按 passed/total 算 —— 而
        # fitness.pair() 只看得到 score。于是 5 条全 unmeasured 会得到一个漂亮的
        # 0.0，被下游当成一次合法测量。§15.6 正是点名这件事：一个 crash 的 organ
        # 若记 0 分，后续把它修好会读成一次 improved —— **凭空制造出点火判据要的
        # 那个事件**。unmeasured 记录了却不兑现，就是 runner 自己身上的
        # 「声明了没断言」。
        return None
    total = len(checks)
    score = (passed / total * 100) if total else 0.0
    return ProbeRun(
        probe_id=probe_id, score=score, checks_passed=passed, checks_total=total,
        detail=detail, probe_manifest_sha=_manifest_sha(manifest),
        tree_sha=_tree_sha(Path(tree)),
        runner_version=RUNNER_VERSION, execution_policy_version=EXECUTION_POLICY_VERSION,
    )


def run_all(tree, vault):
    """全套探针。**任一把测不出来就整体返回 None**（§10：before/after 为 None → UNMEASURED）。

    不做「跳过坏的那把、把好的那些凑成一批」——那样 before 与 after 可能覆盖不同的
    探针集合，两边根本不可比，而 fitness.pair() 无从察觉。宁可整轮记 unmeasured
    （机制故障，不计入拒绝额度），也不要一批悄悄换了尺的分数。

    `catalogue()` 本身也可能编不出清单（坏 manifest，见其 docstring）；那种情形
    同样整体返回 None，而不是拿着一份少一把尺的清单继续跑下去。
    """
    manifests = catalogue(vault)
    if manifests is None:
        return None
    runs = []
    for manifest in manifests:
        run = run_probe(manifest, tree)
        if run is None:
            return None
        runs.append(run)
    return runs
