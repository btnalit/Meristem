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
JSON 对象，key 名不固定）是本模块的假设，不是规格逐字写定的——规格只给了
"input -> organ ABI -> output -> cmp(output, expect)" 这句抽象描述。这里选的
形状经与仓库里已存在的 body/organs/classifier/run.py（只读，未修改）核对一致。
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
    # 约定的固定路径（没有 entrypoint 字段，见 §8.1.1）：body/organs/<organ>/run.py。
    # 与本仓库现有的 body/organs/classifier/run.py 对齐（读取，未修改；本模块不
    # 允许碰 body/）。该文件自己的 docstring 记了同一份 ABI 契约，两处应保持一致。
    return Path(tree) / "body" / "organs" / organ / "run.py"


def _invoke(entrypoint: Path, check_input) -> tuple[bool, str]:
    """input -> organ ABI -> output。返回 (ok, output_or_reason)。

    输出 key 的名字不固定（分类器用 "category"，规格里 "output" 只是抽象说法，
    不是字面 JSON key）——因此只认「恰好一个字符串值的 JSON 对象」这一种形状，
    不认 key 的名字。这样跑不同 organ 不需要为每个 organ 的语义 key 改 runner。
    """
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


def catalogue(vault) -> list:
    """扫描 vault/internal/active/*/probe.json，返回冻结 manifest 全集。"""
    root = Path(vault) / "internal" / "active"
    manifests = []
    if not root.is_dir():
        return manifests
    for probe_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = probe_dir / "probe.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "entrypoint" in manifest:
            # 种子可写 schema 里没有这个字段（§8.1.1），出现即拒绝 —— 纵深防御，
            # 冻结阶段本应已挡过一次，这里是第二层。
            continue
        manifests.append(manifest)
    return manifests


def run_probe(manifest: dict, tree) -> ProbeRun:
    """input -> organ ABI -> output -> cmp(output, expect)。score = passed/len(checks)*100。"""
    checks = manifest["checks"]
    entrypoint = _entrypoint(Path(tree), manifest["organ"])
    detail = []
    passed = 0
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
            continue
        if hit:
            passed += 1
            detail.append({"id": check["id"], "result": "pass"})
        else:
            detail.append({"id": check["id"], "result": "fail", "got": out})
    total = len(checks)
    score = (passed / total * 100) if total else 0.0
    return ProbeRun(
        probe_id=manifest["id"], score=score, checks_passed=passed, checks_total=total,
        detail=detail, probe_manifest_sha=_manifest_sha(manifest),
        tree_sha=_tree_sha(Path(tree)),
        runner_version=RUNNER_VERSION, execution_policy_version=EXECUTION_POLICY_VERSION,
    )


def run_all(tree, vault) -> list:
    return [run_probe(manifest, tree) for manifest in catalogue(vault)]
