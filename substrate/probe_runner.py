"""土壤探针运行器（S3, v5 §9.2 / §10.2 / §15.6 C6）。

土壤读 vault、执行声明式 Internal Probe manifest、算 passed/total。种子侧不得出现
任何探针运行代码或 vault 路径常量 —— 本模块是唯一读 vault 的地方。

判定路径：input -> organ ABI (subprocess) -> output -> cmp(output, expect)。

执行契约（§15.6 C6，P0-a 只做「最小完整性隔离」，不做 namespace/seccomp）：
organ 是种子拥有的代码，不得直接跑在土壤权威进程内 —— subprocess + 严格 JSON ABI +
不传 secrets（裁剪环境变量）+ 不传 vault 路径（本模块从不把 vault 交给 organ）+
墙钟超时 + 非法输出记 `unmeasured`（不是 0 分：0 分在后续修好后会被读成一次
`improved`，凭空制造点火事件，见 §15.6 末尾）。

已知不足（best effort，写明而不是假装做到）：本档不做「禁网」的操作系统级强制 ——
无 root/无 namespace 的开发环境里，标准库 subprocess 无法真正切断一个决心联网的
子进程的 socket。此处只裁剪了环境变量与工作目录，联网仍是**信任边界之外的攻击**，
留给 P0-b 的资源强化档处理。

organ 入口路径（`body/organs/<organ>/run.py`）与输出 ABI（恰好一个字符串值的
JSON 对象，key 名不固定）现由规格写死（§10.2「organ 入口与输出 ABI」），不再是
本模块自己的假设——此前这份约定只分别记在本模块与 body/organs/classifier/run.py
两处 docstring 里，「两处各写一份应保持一致」正是 §17.7 点名的语义漂移温床。
这里选的形状经与仓库里已存在的 body/organs/classifier/run.py（只读，未修改）
核对一致。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RUNNER_VERSION = "1"
EXECUTION_POLICY_VERSION = "1"
PROBE_TIMEOUT_SECONDS = 5.0

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
    try:
        result = subprocess.run(
            [sys.executable, str(entrypoint)],
            input=json.dumps({"input": check_input}),
            capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            cwd=str(entrypoint.parent),
            env=_sandboxed_env(),
        )
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
    """扫描 vault/internal/active/*/probe.json，返回冻结 manifest 全集。

    **返回 None 表示这份清单这一次编不出来**——与 `run_probe` / `run_all` 已有的
    「一把坏尺，整轮 UNMEASURED」契约一致。三种情形都返回 None，而不是悄悄跳过
    那一条、把其余的凑成一份清单交出去：

    1. 某个 probe 目录下的 `probe.json` 读不到（缺文件 / 不是普通文件）。
    2. 某个 `probe.json` 解析不出合法 JSON。
    3. 某个 manifest 含 `entrypoint` 字段（§8.1.1：种子可写 schema 里没有这个
       字段，出现即拒绝——CA-5 的语义是「出现即拒绝」，不是「出现即假装没看见」；
       冻结阶段本应已挡过一次，这里是纵深防御的第二层）。

    原先这三处是 `continue`：一把坏尺被静默略过，`run_all` 拿到的是一份**少一把
    尺的清单**——而 `run_all` 自己的契约明写「不做跳过坏的、凑一批好的，那样
    before 与 after 会覆盖不同的探针集合，两边根本不可比，且 `fitness.pair()`
    对此毫无察觉」。catalogue 曾经在做 run_all 声明绝不做的事；现在两者一致：
    一把坏尺 → 这一次整份清单都编不出来。

    vault 尚未存在（还没冻结过任何 probe）不算「坏」，返回空列表 `[]`——这是
    合法的初始状态，不是某把尺读不出来。
    """
    root = Path(vault) / "internal" / "active"
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
        if "entrypoint" in manifest:
            return None
        manifests.append(manifest)
    return manifests


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
    except KeyError:
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
