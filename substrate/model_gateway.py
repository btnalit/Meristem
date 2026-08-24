"""土壤侧模型网关（S7 的执行端，v5 §8.1.3 / §10.2 / §18 v5.9 行）。

**这是 `meristem/llm.py` 的 `call_model()` 唯一会说话的对面。** v5.9 落地时抓到
的缺陷是「IPC 只有一端」——`llm.py` 早就写好了「经土壤 IPC 请求一次调用」的
subprocess 契约，但 `substrate/` 清单里没有任何模块会应答。本模块补上应答的
那一端，且**逐字**匹配 `llm.py` 已经写死的契约（不是本模块定契约，是本模块
兑现 `llm.py` 那份已经存在的契约）：

    stdin  : `{"role": <str>, "prompt": <str>}` 的 JSON，一次性读完
    stdout : 恰好一行 `{"status": "allowed"|"refused"|"deferred", "content"?, "reason"?}`
    退出码 : `llm.py` 从不检查，本模块统一返回 0——一个非零退出码不会让种子看到
             任何不同的东西，只会让「进程本身跑不起来」与「跑起来但拒绝」两种
             情形更难从外面区分（`llm.py` 两种都归一到 refused，见其 docstring）。

**不与 `substrate/budget.py` 合并**（§18 v5.9 行原话）：「预算判定藏进调用
执行，正是 S7 那次死锁的成因」。本模块只回答「这次调用最终 allowed / refused /
deferred」，配额判定整段委托给 `budget.check()`；budget.py 自己不知道 provider、
不知道 stdin/stdout 是什么形状。两个模块分开，才能被独立观测、独立测试、
独立修——v3.1 的教训是焊死在一起之后，连「查得到但改不了」是卡在哪一层都分不清。

**只吐三态，绝不吐配额数字**（§8.1.3：「不暴露配额数字」，「种子从接口只能知道
一次调用的结果」）。`budget.check()` 返回的违规描述带着具体计数与上限——那是
给土壤操作员看 stderr 用的，本模块把它打到 stderr 后，**回给种子的永远是一个
不带数字的固定原因**（`"reason": "budget"`）。一旦具体数字进了 stdout 的 JSON，
种子就能靠这些数字反推「还剩多少」，§8.1.3 点名的那句话——「不暴露配额数字，
否则种子可以通过挑角色、改 retry、卡窗口来影响选择压力」——就被这一行代码
原样推翻了。同理，槽位 id、provider 报错细节等只进 stderr，不进 stdout。

**凭据缝（P0-a，本环境无凭据，不得编造）**：生产 policy 只声明
`MERISTEM_CREDENTIALS_FILE` 这个文件指针环境变量，不保存也不要求
`SENSENOVA_API_KEY` 出现在 seed 环境。`_call_provider()` 是真正对接 provider 的
唯一入口：凭据文件不在、不可读或权限不安全时**必须**在打任何网络请求之前
fail closed，原因是一个独立于 `budget` / `gateway_not_injected` 之外的第三个名字
（`no_credentials`），不是「假装调用成功后编一段回复」——那和造假响应是同一类
错误，本模块不做。
"""
from __future__ import annotations

import json
import os
import pwd
import stat
import sys
import tomllib
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from substrate import budget as _budget  # noqa: E402
from substrate import provider_client as _provider_client  # noqa: E402

POLICY_DIR = REPO / "soil" / "model-policies"
DEFAULT_EXECUTION_MODE = "agnes-temporary"
BACKUP_EXECUTION_MODES = ("openrouter-free", "sensenova")
EXECUTION_MODES = frozenset({"agnes-temporary", "openrouter-free", "sensenova"})


def execution_mode(mode: str | None = None) -> str:
    """Resolve the soil-selected provider mode, fail-closed on unknown values."""
    selected = mode if mode is not None else os.environ.get(
        "MERISTEM_MODEL_MODE", DEFAULT_EXECUTION_MODE)
    if not isinstance(selected, str) or selected not in EXECUTION_MODES:
        raise ValueError(f"unsupported model execution mode: {selected!r}")
    return selected


def policy_path_for_mode(mode: str | None = None) -> Path:
    """Return only an allowlisted soil-owned policy path."""
    selected = execution_mode(mode)
    path = POLICY_DIR / f"{selected}.toml"
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"model policy unavailable for mode: {selected}")
    return path


def load_execution_policy(mode: str | None = None) -> dict:
    return _budget.load_policy(policy_path_for_mode(mode))

#: seed 端可见角色的权威来源在 `seed/model-interface.json`（土壤写、种子只读，
#: §16 权威矩阵）。**不信任种子自己的检查**：`llm.py._roles_available()` 已经
#: 挡过一次角色白名单，但那是种子自己代码里的检查——一个绕开 `llm.py`、直接
#: 手搓 `{"role":"review", ...}` 发给本网关的调用者不受那道检查约束。
#: `review` 槽位配的是评审面板的真实凭据（S4），若网关只看
#: `soil/model-policy.toml` 里 `role` 存不存在，`review` 就会被当成一个合法角色
#: 服务——那正是 v3.1 「种子能改判它自己的那份提示词与判据」的同一形状，只是
#: 换了个入口。纵深两层，不是一层（与 §10.1 engine.py 的白名单原则同一条理由）。
MODEL_INTERFACE_PATH = REPO / "seed" / "model-interface.json"

CALLS_LEDGER_PATH = _budget.DEFAULT_CALLS_LEDGER


def _seed_visible_roles() -> set[str]:
    try:
        doc = json.loads(MODEL_INTERFACE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 读不到 -> 不猜「大概率还是那几个角色」，当成没有任何角色可用。
        # 一个读不出白名单的网关，唯一诚实的行为是拒绝一切角色，不是照单全收。
        return set()
    roles = doc.get("roles_available_to_seed") if isinstance(doc, dict) else None
    return set(roles) if isinstance(roles, list) else set()


def _select_slot(policy: dict, role: str) -> dict | None:
    """P0-a：每个角色最多一个槽位在跑（`mutate` 的 deepseek 回退已被移除，
    `review` 的槽位刻意不做回退，见 `soil/model-policy.toml` 注释）。这里仍按
    「取第一个」写，不是因为要挑，是因为槽位顺序本身就不该被网关以外的任何东西
    观察到——种子拿不到 `content` 之外的任何东西，天然看不见「顺序」。
    """
    slots = policy.get("roles", {}).get(role, {}).get("slots")
    if not isinstance(slots, list) or not slots:
        return None
    slot = slots[0]
    return slot if isinstance(slot, dict) else None


def _credential_value(slot: dict) -> str | None:
    """Read a provider credential on the soil side, never in the seed.

    Production slots name an environment variable containing a *file pointer*
    (``MERISTEM_CREDENTIALS_FILE``), not an environment variable containing the
    secret.  The seed receives only that pointer.  The file must be absolute,
    non-symlink, regular, and private to its owner; otherwise the gateway
    fails closed before any network request.  A legacy ``api_key_env`` is kept
    only for injected unit-test policies, so the production policy cannot
    silently fall back to a secret-bearing environment variable.
    """
    pointer_env = slot.get("credentials_file_env")
    if isinstance(pointer_env, str):
        raw_path = os.environ.get(pointer_env, "")
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            return None
        try:
            st = os.stat(path, follow_symlinks=False)
            if (not stat.S_ISREG(st.st_mode) or (st.st_mode & 0o077)
                    or st.st_uid != pwd.getpwnam("soil").pw_uid):
                return None
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path, flags)
            try:
                value = os.read(fd, 8193)
            finally:
                os.close(fd)
        except OSError:
            return None
        if len(value) > 8192:
            return None
        try:
            credential = value.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        return credential or None

    # Test-only compatibility for policies injected directly into handle().
    api_key_env = slot.get("api_key_env")
    return os.environ.get(api_key_env) if isinstance(api_key_env, str) else None


def _call_provider(slot: dict, prompt: str, *, retry: dict | None = None,
                   before_attempt=None, on_attempt=None, on_result=None) -> tuple[str, str | None, str | None]:
    """Call one OpenAI-compatible provider through the soil-owned SDK seam.

    The SDK performs exactly one request (`max_retries=0`); this function owns
    retry classification, budget callbacks, and the worker-visible three-state
    result.
    """
    api_key = _credential_value(slot)
    if not api_key:
        return "refused", None, "no_credentials"

    if retry is None:
        max_attempts, backoff = 1, []
    else:
        max_attempts = retry["max_attempts"]
        backoff = retry["backoff_seconds"]

    for attempt in range(max_attempts):
        if before_attempt is not None and not before_attempt():
            return "refused", None, "budget"
        if on_attempt is not None:
            on_attempt(attempt + 1)

        result = _provider_client.chat_once(
            base_url=str(slot.get("base_url", "")).rstrip("/"),
            api_key=api_key,
            model=str(slot.get("model", "")),
            prompt=prompt,
            max_tokens=int(slot.get("max_tokens", 4096)),
            temperature=float(slot.get("temperature", 0.0)),
            timeout=float(slot.get("timeout", 60)),
            response_format=(
                {"type": "json_object"}
                if slot.get("response_format") == "json_object" else None
            ),
        )
        if on_result is not None:
            on_result(result)
        if result.ok:
            return "allowed", result.content, None
        if result.error_kind == "rate_limited":
            if attempt + 1 >= max_attempts:
                return "deferred", None, "rate_limited"
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
            continue
        if result.error_kind == "connection_error":
            if retry is not None and attempt + 1 < max_attempts:
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            return "refused", None, "provider_error"
        if result.error_kind == "bad_response":
            return "refused", None, "provider_bad_response"
        return "refused", None, "provider_error"

    return "deferred", None, "rate_limited"


def _retry_config(policy: dict) -> dict | None:
    """Validate the soil retry declaration, or return None for test policies."""
    if "retry" not in policy:
        return None
    cfg = policy.get("retry")
    if not isinstance(cfg, dict):
        return None
    delays = cfg.get("backoff_seconds")
    attempts = cfg.get("max_attempts")
    if (not isinstance(delays, list) or not delays
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) or x < 0
                   for x in delays)
            or isinstance(attempts, bool) or not isinstance(attempts, int)
            or attempts < 1 or attempts > len(delays) + 1):
        return None
    return {"backoff_seconds": delays, "max_attempts": attempts}


def handle(request, *, policy: dict, calls_ledger: Path, cycle: int) -> dict:
    """一次请求 -> 一次响应的纯函数核心。`main()` 负责从环境/文件系统解析
    `policy` / `calls_ledger` / `cycle` 后调用它——测试可以直接灌这三样，
    不需要每次都真的起一个子进程或真的设 `MERISTEM_SOIL_CYCLE`。
    """
    role = request.get("role") if isinstance(request, dict) else None
    prompt = request.get("prompt") if isinstance(request, dict) else None
    if not isinstance(role, str) or not isinstance(prompt, str):
        return {"status": "refused", "reason": "bad_request"}

    if role not in _seed_visible_roles():
        # `role` 不在种子可见白名单里：可能是 `review`（土壤专用，见上）,
        # 也可能是拼错的角色名。两种都归一个原因——**是否存在这个角色**本身
        # 也不该被种子用来探测网关内部结构。
        return {"status": "refused", "reason": "role_unavailable"}

    slot = _select_slot(policy, role)
    if slot is None:
        return {"status": "refused", "reason": "role_unavailable"}

    violation = _budget.check(calls_ledger, cycle, policy=policy)
    if violation is not None:
        # 带数字的那句话只打 stderr，给土壤操作员看；stdout 回给种子的
        # `reason` 永远是这个不带数字的固定值（§8.1.3）。
        print(f"model_gateway: budget refused role={role!r} slot={slot.get('id')!r} "
              f"cycle={cycle}: {violation}", file=sys.stderr)
        return {"status": "refused", "reason": "budget"}

    retry = _retry_config(policy)
    if "retry" in policy and retry is None:
        return {"status": "refused", "reason": "policy"}

    mode = execution_mode()
    telemetry_path = (_budget.DEFAULT_PROVIDER_EVENTS_LEDGER
                      if Path(calls_ledger) == _budget.DEFAULT_CALLS_LEDGER
                      else Path(calls_ledger).with_name("soil-provider-events.jsonl"))
    telemetry = _budget.ProviderEventLedger(telemetry_path)
    attempts_seen = 0

    def before_attempt() -> bool:
        attempt_violation = _budget.check(calls_ledger, cycle, policy=policy)
        if attempt_violation is not None:
            print(f"model_gateway: retry budget refused role={role!r} "
                  f"cycle={cycle}: {attempt_violation}", file=sys.stderr)
            return False
        # Each actual provider attempt consumes one call, including a 429.
        _budget.ModelCallLedger(calls_ledger).record(
            cycle=cycle, role=role, slot_id=str(slot.get("id")))
        return True

    def on_attempt(attempt: int) -> None:
        nonlocal attempts_seen
        attempts_seen = attempt
        telemetry.record(cycle=cycle, mode=mode, role=role,
                         slot_id=str(slot.get("id")), model=str(slot.get("model")),
                         event="attempt", attempt=attempt)

    def on_result(result) -> None:
        telemetry.record(cycle=cycle, mode=mode, role=role,
                         slot_id=str(slot.get("id")), model=str(slot.get("model")),
                         event="result_meta",
                         finish_reason=getattr(result, "finish_reason", None),
                         prompt_tokens=getattr(result, "prompt_tokens", None),
                         completion_tokens=getattr(result, "completion_tokens", None))

    status, content, reason = _call_provider(slot, prompt, retry=retry,
                                              before_attempt=before_attempt,
                                              on_attempt=on_attempt,
                                              on_result=on_result)
    telemetry.record(cycle=cycle, mode=mode, role=role,
                     slot_id=str(slot.get("id")), model=str(slot.get("model")),
                     event="result", status=status, reason=reason,
                     attempt=attempts_seen)

    response: dict = {"status": status}
    if content is not None:
        response["content"] = content
    if reason is not None:
        response["reason"] = reason
    return response


def _resolve_cycle() -> int | None:
    """拍号只从 `MERISTEM_SOIL_CYCLE` 读——这个变量种子自己也看得见
    （`meristem/loop.py` 用它给 commit message 打标签），不是新增的泄露面。
    **读不到就是 `None`，不猜一个 0**：`loop.py` 自己的注释已经写过同一条
    理由——每次都标成 "cycle 0" 是个静默的错误标签，比没有标签更坏。
    """
    raw = os.environ.get("MERISTEM_SOIL_CYCLE", "").strip()
    return int(raw) if raw.isdigit() else None


def main(argv=None) -> int:
    raw_stdin = sys.stdin.read()
    try:
        request = json.loads(raw_stdin)
    except json.JSONDecodeError:
        print(json.dumps({"status": "refused", "reason": "bad_request"}))
        return 0

    try:
        policy = load_execution_policy(execution_mode())
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        print(json.dumps({"status": "refused", "reason": "policy_unavailable"}))
        return 0

    cycle = _resolve_cycle()
    if cycle is None:
        print(json.dumps({"status": "refused", "reason": "cycle_unknown"}))
        return 0

    try:
        response = handle(request, policy=policy, calls_ledger=CALLS_LEDGER_PATH, cycle=cycle)
    except Exception as exc:  # 网关绝不能把 traceback 烧穿到调用方——
        # `llm.py` 已经能兜住「stdout 不是合法响应」（`gateway_bad_response`），
        # 但明确吞掉更诚实：这是网关自己的故障，不是「响应读不出来」。
        print(f"model_gateway: internal error: {exc!r}", file=sys.stderr)
        response = {"status": "refused", "reason": "gateway_internal_error"}

    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
