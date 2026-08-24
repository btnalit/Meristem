"""SUBSTRATE —— v5 土壤的运行入口。晋升权威在这里，不在种子里。

**本文件只剩 v5。** v3.1 的那 1200 余行（`promote` / `canary` / `heartbeat` /
`rollback` / `run` / journal / proposals / cap case / lifecycle 守卫 /
probe staging）**已整块删除**，而不是加闸关起来。

**为什么是删而不是关。** 上一版给那些入口加了默认拒绝，还留了一个
`MERISTEM_ALLOW_LEGACY=1` 的后门。但一个加了锁的第二入口仍然是第二入口 ——
而这个项目自己的规矩是「同一件事只能有一个权威判定入口」。
更要紧的是：那个后门存在的**唯一理由**就是 v3.1，
而 v3.1 已经在 §13 清盘里判了死刑。**留着它就是为一个已经不存在的系统保留能力。**

§13.3 早就写明了处置，我上一版没照做：
「cap case 相关逻辑 → **v5 无 LOC 闸门，整段删除**」·
「`guard_lifecycle()` → **重写或删除**」·
结论那一行是「**保留骨架、重写判决回路**」，不是「保留 1255 行改三处」。

**删掉的东西没有丢**：git 历史与 `/RSI/meristem-v3-archive` 的 bundle 里都在。
P0-c 需要 rollback 阶梯 / keeper / breaker 时，是**照 v5 的语义重新设计**，
从历史里取材，而不是把 v3.1 的实现解冻 —— 那正是 §13.3 说的「逐项对照状态语义」。

当前入口只有两个：

    python -m substrate.supervisor manual-cycle [--calibration] [--candidate <sha>]
    python -m substrate.supervisor ignition-status

`manual-cycle` 走与未来 heartbeat 完全相同的代码路径（§12.0.2），
唯一的区别是判决位上坐着人。`ignition-status` 是 §1.2 判据的唯一求值点。
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import pwd
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from root import panic  # noqa: E402
from substrate import feedback_projection  # noqa: E402
from substrate import pipeline as _pipeline  # noqa: E402
from substrate import probe_runner as _probe_runner  # noqa: E402
from substrate import soil_state as _soil_state  # noqa: E402
from substrate import learning_state, strategy_memory
from substrate import runtime_manifest

#: §13.3 表 C（v5.9 补）：种子经 `meristem/llm.py` 的每次模型调用都靠这个变量
#: 转发到 soil-owned `model_gateway_client.py`。client 只读
#: `MERISTEM_MODEL_SOCKET`，真正读取 provider credential 的 server 进程使用
#: 真实仓库路径与 soil UID；不能让 seed 的候选 worktree 解析 soil 模块。
MODEL_GATEWAY_ENTRYPOINT = f"{sys.executable} {REPO / 'substrate' / 'model_gateway_client.py'}"
MODEL_GATEWAY_SERVER = REPO / "substrate" / "model_gateway_server.py"


_WORKER_COPY_DIRS = ("meristem", "body", "seed")
_WORKER_COPY_FILES = ("substrate/model_gateway_client.py",)
_WORKER_WRITABLE = (
    "seed/constitution.md", "seed/agenda.md", "seed/narrative.md",
    "seed/probe-proposals/", "body/organs/", "tests/",
)


def _is_worker_writable(rel: str) -> bool:
    return any(rel == item.rstrip("/") or
               (item.endswith("/") and rel.startswith(item))
               for item in _WORKER_WRITABLE)


def _worker_surface_materialized(rel: str) -> bool:
    """Whether absence in worker can represent an intentional deletion.

    Recovery may infer deletions only inside materialized source directories.
    A writable path outside that surface was never presented to the worker, so
    its absence is not evidence of a mutation.
    """
    top = rel.split("/", 1)[0]
    return top in _WORKER_COPY_DIRS


def _copy_worker_surface(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Materialize only the seed-visible source surface for worker execution."""
    destination.mkdir(mode=0o700, parents=True)
    for name in _WORKER_COPY_DIRS:
        src = source / name
        if src.is_symlink():
            raise OSError(f"worker source contains symlink: {src}")
        if not src.is_dir():
            continue
        _reject_symlinks(src)
        shutil.copytree(src, destination / name, symlinks=True)
    for rel in _WORKER_COPY_FILES:
        src = source / rel
        if src.is_symlink():
            raise OSError(f"worker source file invalid: {src}")
        if not src.is_file():
            continue
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    uid = pwd.getpwnam("worker").pw_uid
    gid = grp.getgrnam("worker").gr_gid
    os.chown(destination, uid, gid)
    for root, dirs, files in os.walk(destination, followlinks=False):
        for name in (*dirs, *files):
            path = pathlib.Path(root) / name
            if path.is_symlink():
                raise OSError(f"worker surface contains symlink: {path}")
            os.chown(path, uid, gid, follow_symlinks=False)


def _reject_symlinks(root: pathlib.Path) -> None:
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = pathlib.Path(current) / name
            if path.is_symlink():
                raise OSError(f"worker source contains symlink: {path}")


def _reject_target_links(root: pathlib.Path, rel: str) -> None:
    current = root
    for component in rel.split("/"):
        current /= component
        if current.is_symlink():
            raise OSError(f"soil worktree target contains symlink: {current}")


def _recover_worker_changes(worker_root: pathlib.Path, worktree: pathlib.Path) -> list[str]:
    """Copy only soil-approved changed files back into the soil worktree."""
    _reject_symlinks(worker_root)
    changed: list[str] = []
    for root, _dirs, files in os.walk(worker_root, followlinks=False):
        for name in files:
            path = pathlib.Path(root) / name
            if path.is_symlink():
                raise OSError(f"worker returned symlink: {path}")
            rel = path.relative_to(worker_root).as_posix()
            if not _is_worker_writable(rel):
                continue
            target = worktree / rel
            _reject_target_links(worktree, rel)
            old = target.read_bytes() if target.is_file() else None
            new = path.read_bytes()
            if old != new:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                changed.append(rel)
    for root, _dirs, files in os.walk(worktree, followlinks=False):
        for name in files:
            target = pathlib.Path(root) / name
            rel = target.relative_to(worktree).as_posix()
            if (_is_worker_writable(rel)
                    and _worker_surface_materialized(rel)
                    and not (worker_root / rel).exists()):
                _reject_target_links(worktree, rel)
                if target.is_symlink():
                    raise OSError(f"soil worktree contains symlink: {target}")
                target.unlink()
                changed.append(rel)
    return changed


def _start_model_gateway(*, socket_path: pathlib.Path, soil_cycle: int):
    """Start the credential-reading gateway outside the seed process.

    The pointer is intentionally present only in this soil-owned process. On
    POSIX production hosts, `setpriv` drops the gateway to the soil identity;
    without that boundary the supervisor refuses to start a model call.
    """
    credentials_file = os.environ.get("MERISTEM_CREDENTIALS_FILE")
    setpriv = shutil.which("setpriv")
    if not credentials_file or not setpriv:
        print("model gateway refused: soil credential process unavailable", file=sys.stderr)
        return None
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(REPO),
        "MERISTEM_MODEL_SOCKET": str(socket_path),
        # Soil operator selects one immutable mode for the whole gateway
        # process; the worker never receives this variable.
        "MERISTEM_MODEL_MODE": os.environ.get(
            "MERISTEM_MODEL_MODE", "openrouter-free"),
        "MERISTEM_CREDENTIALS_FILE": credentials_file,
        "MERISTEM_SOIL_CYCLE": str(soil_cycle),
    }
    command = [setpriv, "--reuid=soil", "--regid=soil", "--clear-groups",
               sys.executable, str(MODEL_GATEWAY_SERVER), "--socket", str(socket_path)]
    try:
        proc = subprocess.Popen(command, cwd=str(REPO), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True)
    except OSError as exc:
        print(f"model gateway refused: {exc}", file=sys.stderr)
        return None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if socket_path.exists():
            os.chown(socket_path, pwd.getpwnam("soil").pw_uid,
                     grp.getgrnam("worker").gr_gid)
            os.chmod(socket_path, 0o660)
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=2)
    print("model gateway refused: soil socket did not become ready", file=sys.stderr)
    return None


def _stop_model_gateway(proc) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def _refuse_if_latched() -> bool:
    """panic 闩：上着就不跑（`root/panic.py`，权威在 root/ 这一级）。

    **这一条是把闩从 v3.1 的遗物改造成 v5 的机制。** 闩本来只有 v3.1 的 keeper
    在看；v3.1 删掉之后，若没人接手看它，`root/panic.py` 就成了一个零调用点的
    机制 —— 而这份规格自己写过，`advance()` 零调用点就是这么活过 400 拍的。
    **一个没有读者的安全开关不是安全开关。**

    清闩只有人能做（`python3 root/panic.py clear`）；种子无任何路径触及它。
    """
    try:
        latch = panic.latch_path()
    except panic.ControlPathMissing as exc:
        # **闩查不了就不许跑。** 「不知道闩在哪」与「闩没上」必须是两件事 ——
        # 把前者当成后者，等于在一个无法急停的系统上开工。
        print(f"无法定位 panic 闩，拒绝运行：\n{exc}", file=sys.stderr)
        return True
    if not latch.exists():
        return False
    print(f"panic 闩已上，拒绝运行：{latch}\n"
          f"  内容：{latch.read_text(encoding='utf-8').strip()}\n"
          f"  清闩只有人能做（记得先 source 环境文件）：\n"
          f"    set -a && . /RSI/meristem-env && set +a && python3 root/panic.py clear",
          file=sys.stderr)
    return True


DEFAULT_TASK_DECLARATION = "soil/p0a-task.json"


def _generation(repo=None) -> str:
    """世代权威在 `root/`（root of trust），不在土壤自己手里。

    读不到就**抛错，不退回 `gen-0`**：`generation` 是 §8.2 的六个强制字段之一，
    猜一个值会让台账带着一个看起来正常的错标签，而错标签比缺字段更坏——它像数据。
    """
    path = (pathlib.Path(repo) if repo is not None else REPO) / "root" / "generations.json"
    return json.loads(path.read_text(encoding="utf-8"))["live"]


def _next_soil_cycle(repo) -> int:
    """下一个拍号 = 台账里出现过的最大 `soil_cycle` + 1。

    **上一版数的是 `observed_fitness` 的条数，那是一个死锁。** 实测于服务器：
    `observed_fitness` 只在 `validate_task()` **通过之后**才写；而 C1 的
    `eligible_after` 要求「冻结那一拍不可用」，即拍号必须先前进。
    于是拍号只能靠穿过闸门来前进，而闸门用拍号判断能不能过 ——
    **计数器永远停在原地，任务永远被拒。**

    这与 v3.1 的 `campaign_calls` 是同一个形状（§13.2）：
    **一个只能穿过闸门才能前进的计数器，而那道闸门正用它做判断。**
    I1 把「一切计数皆滚动窗口」写成规则，是为了防这类东西；
    这里的教训更窄也更基本：**推进拍号的动作，不得挂在拍号所守的那道闸后面。**

    改用「台账里出现过的最大拍号 + 1」，并由 `manual_cycle()` 在**任何校验之前**
    先写一条 `kind:"cycle"` —— 于是一次被拒的尝试同样让拍号前进。
    取 `max` 而不是计数，也让「一拍里写了多条带拍号的事件」不会把计数推歪。

    仍然**不另设计数文件**：台账就是权威（§8.2），这个数可在任意副本上离线重算。
    """
    ledger = _soil_state.Ledger(pathlib.Path(repo) / "state" / "soil-ledger.jsonl")
    seen = [r["soil_cycle"] for r in ledger.read()
            if isinstance(r.get("soil_cycle"), int) and not isinstance(r.get("soil_cycle"), bool)]
    return (max(seen) + 1) if seen else 1


def _task_id(text: str) -> str:
    """镜像 `meristem.task.task_id`（内容哈希即身份，§4.1）。

    **土壤不导入种子**（I9 / CA-4 断言 `substrate/` 不得 import `meristem`），
    所以这里复刻规则而不是调用它——与 `_is_guarded_proposal` 复刻围栏、
    `_score_probe` 复刻 rubric 契约同一个理由：**让种子回答「种子做得对不对」，
    等于让它给自己判分。** 代价是两处须保持一致，而这一致性**目前没有断言在守**。
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _agenda_first_task(repo):
    """议程首条可做的任务文本；没有议程返回 None。

    复刻 `meristem.task.take_task` 的取题规则（理由同 `_task_id`），但**只复刻到
    「首条非空非注释行」为止**：done/parked 过滤要读 `seed/feedback.json`，
    而那份投影 P0-a 尚未产出。两者在 P0-a 等价，之后不等价——记为未闭合项。
    """
    agenda = pathlib.Path(repo) / "seed" / "agenda.md"
    if not agenda.is_file():
        return None
    for raw in agenda.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line[:2] in ("- ", "* "):
            line = line[2:].strip()
        if line:
            return line
    return None


def _load_task(repo, task_path=None):
    """读 Task 声明，并与议程首条**核对身份**。

    不一致即拒绝：**一个任务两个身份**正是本规格反复点名的那种漂移
    （`campaign` 一词的代价是循环死锁），而 `task_id` 是台账里事件↔Task 的唯一锚。
    """
    path = pathlib.Path(task_path) if task_path else pathlib.Path(repo) / DEFAULT_TASK_DECLARATION
    if not path.is_file():
        raise SystemExit(f"Task 声明不存在：{path}（§8.1.4；P0-a 由实验者给出）")
    task = _pipeline.Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
    agenda_text = _agenda_first_task(repo)
    if agenda_text is not None and task.task_id != _task_id(agenda_text):
        raise SystemExit(
            f"task_id 与议程首条不一致：声明 {task.task_id}，"
            f"议程 {_task_id(agenda_text)}（{agenda_text[:60]!r}）。"
            "一个任务只能有一个身份，拒绝继续。")
    return task


def manual_prompt(commit: str, diff: str, task):
    """P0-a 的 **Panel adapter**（§12.0.2）。

    **y/n 落在 Verdict 位置，不落在 merge 位置** —— `process_candidate` 一行不改，
    `promotion_intent` / scoreboard / `accepted_fitness` / `promotion_committed`
    这条晋升事务链完整走完。P0-b 把这个函数换成真 panel，其余代码一行不动：
    **拆脚手架 = 换一个函数指针。**

    **不打印 fitness。** §12.0.2 的散文写着「`manual_prompt` 把 fitness 打给实验者」，
    而 §10.2 的签名与其理由写着「面板只收 diff + 任务声明，**不传 observed** ——
    评审员看见 +20 分会锚定向批准，判决就被测量污染了」。两处冲突，**按 §10.2 实现**：
    它是签名的定义处，且带着不变量的理由；§12.0.2 那句是描述性散文。
    已记入 §18 勘误（v5.10）。
    """
    print(f"\n=== 候选 {commit[:12]} ===")
    print(f"任务 {task.task_id} · target={task.target} · expected={task.expected} "
          f"· minimum_delta={task.minimum_delta}")
    print(f"--- diff（{len(diff.splitlines())} 行）---")
    print(diff)
    print("--- diff 结束 ---")
    print("按土壤版评审清单判决。**你看不到分数，这是刻意的**："
          "兑现声明的核验由 task_evaluator 在判决之外已经做完。")
    passed = input("accept? [y/N] ").strip().lower() in ("y", "yes")
    reason = "manual accept" if passed else (input("拒绝理由：").strip() or "manual reject")
    return _pipeline.Verdict(passed=passed, authority="manual", reason=reason)


def _seed_candidate(repo, ctx, task):
    """跑一次种子 `cycle`，产出候选 commit。返回 `(commit, worktree_path)`。

    种子在**独立 worktree** 里提交，主线 HEAD 不动 —— 于是
    `candidate.parent == HEAD` 成立，C3 的祖先检查才有意义（§10.1「提交到 worktree」）。
    若种子直接提交在主线上，HEAD 会跟着候选一起走，那条检查恒假。

    `kind:"cycle"` 记录**由土壤据退出码与 commit 生成**，种子不写台账（C4 / §10.1）。
    """
    base = _pipeline.git(repo, "rev-parse", "HEAD")
    worktree = pathlib.Path(tempfile.mkdtemp(prefix="meristem-candidate-")) / "wt"
    _pipeline.git(repo, "worktree", "add", "-q", "--detach", str(worktree), base)
    worker_parent = pathlib.Path(tempfile.mkdtemp(prefix="meristem-worker-", dir="/tmp"))
    os.chown(worker_parent, pwd.getpwnam("worker").pw_uid,
             grp.getgrnam("worker").gr_gid)
    worker_parent.chmod(0o700)
    worker_root = worker_parent / "surface"
    _copy_worker_surface(worktree, worker_root)
    projection = pathlib.Path(repo) / "seed" / "feedback.json"
    if projection.is_file():
        target = worker_root / "seed" / "feedback.json"
        shutil.copy2(projection, target)
        os.chown(target, pwd.getpwnam("worker").pw_uid,
                 grp.getgrnam("worker").gr_gid)
    socket_dir = pathlib.Path(tempfile.mkdtemp(prefix="meristem-gateway-", dir="/tmp"))
    os.chown(socket_dir, pwd.getpwnam("soil").pw_uid, grp.getgrnam("worker").gr_gid)
    socket_dir.chmod(0o730)
    socket_path = socket_dir / "model-gateway.sock"
    gateway_proc = _start_model_gateway(socket_path=socket_path,
                                         soil_cycle=ctx.soil_cycle)
    if gateway_proc is None:
        shutil.rmtree(socket_dir, ignore_errors=True)
        shutil.rmtree(worker_parent, ignore_errors=True)
        return None, worktree
    # **裁剪环境，不整份传递**（§15.6 C6）。种子的 `cycle` 是种子自己的代码，
    # 整份 `os.environ` 会把 `MERISTEM_VAULT`（C-65 保证一定在）连同任何模型 key、
    # webhook 一起交给它 —— vault 存在的全部理由就是种子看不见它。
    # 与 `pipeline.canary()` 复用同一份 allowlist，不各写各的。
    #
    # **`MERISTEM_MODEL_GATEWAY` 必须显式写进这个字典，不能指望它「本来就在
    # `os.environ` 里、会被带过去」**（§13.3 表 C，v5.9 补）。`_sandboxed_env()`
    # 是一份不含它的允许列表——哪怕运维在 supervisor 自己的进程环境里正确设置了
    # 这个变量，`{**_sandboxed_env(), ...}` 也只会原样丢弃它，因为它不在
    # `_ENV_ALLOWLIST` 里，而这里又没有像 `PYTHONPATH` / `MERISTEM_SOIL_CYCLE`
    # 那样单独把它加回来。**一个设对了却被静默滤掉的变量，和一个从没设置过的
    # 变量，效果完全一样**——两者都会让 `llm.py` fail closed 成
    # `gateway_not_injected`，而这正是 v5.9 那行原文点名要防的「哑故障」。
    # 与 `MERISTEM_SOIL_CYCLE` 同一处理方式：现算，不依赖继承。
    worker_client = worker_root / "substrate" / "model_gateway_client.py"
    worker_gateway_entrypoint = f"{sys.executable} {worker_client}"
    env = {**_probe_runner._sandboxed_env(), "PYTHONPATH": str(worker_root),
           "MERISTEM_SOIL_CYCLE": str(ctx.soil_cycle),
           "MERISTEM_MODEL_GATEWAY": worker_gateway_entrypoint,
           "MERISTEM_DEFER_COMMIT": "1"}
    # The seed gets only a socket endpoint. The credential pointer is held by
    # the soil-owned gateway process, never copied into this environment.
    env["MERISTEM_MODEL_SOCKET"] = str(socket_path)
    try:
        setpriv = shutil.which("setpriv")
        if not setpriv:
            raise OSError("setpriv unavailable")
        command = [setpriv, "--reuid=worker", "--regid=worker", "--clear-groups",
                   sys.executable, "-m", "meristem.loop", "cycle"]
        result = subprocess.run(command,
                                cwd=str(worker_root), env=env, capture_output=True,
                                text=True, timeout=4200)
    except (subprocess.SubprocessError, OSError) as exc:
        result = SimpleNamespace(returncode=-1, stdout="", stderr=str(exc))
    _stop_model_gateway(gateway_proc)
    shutil.rmtree(socket_dir, ignore_errors=True)
    recovered: list[str] = []
    try:
        if result.returncode == 0:
            recovered = _recover_worker_changes(worker_root, worktree)
    except (OSError, ValueError) as exc:
        result = SimpleNamespace(returncode=1, stdout=result.stdout,
                                 stderr=result.stderr + f"\nWORKER_RECOVER_FAILED {exc}")
    shutil.rmtree(worker_parent, ignore_errors=True)
    if result.returncode == 0 and recovered:
        commit_result = subprocess.run(
            ["git", "add", "--", *recovered], cwd=str(worktree),
            capture_output=True, text=True)
        if commit_result.returncode == 0:
            commit_result = subprocess.run(
                ["git", "-c", "user.email=seed@meristem.local",
                 "-c", "user.name=meristem-seed", "commit", "-m",
                 f"seed cycle {ctx.soil_cycle}: {task.task_id}"],
                cwd=str(worktree), capture_output=True, text=True)
        if commit_result.returncode != 0:
            result = SimpleNamespace(returncode=1, stdout=result.stdout,
                                     stderr=result.stderr + commit_result.stderr)
    head = _pipeline.git(worktree, "rev-parse", "HEAD") if result.returncode == 0 and recovered else None
    # 退出码 0 但 HEAD 没动 = 种子没提交任何东西。**那不是一个候选**，
    # 台账里就不能把 base 记成本拍产出的 commit —— 错标签比没有标签更坏，
    # 它看起来像数据（同 loop.py 拒绝把未知拍号伪造成 0 的理由）。
    commit = head if head is not None and head != base else None
    failure_reason = None
    stderr = result.stderr or ""
    if "PATH_VIOLATION" in stderr:
        failure_reason = "path_violation"
    elif "PROPOSE_FAILED" in stderr:
        failure_reason = "propose_failed"
    elif "PROMPT_OVER_BUDGET" in stderr:
        failure_reason = "prompt_over_budget"
    elif result.returncode != 0:
        failure_reason = "worker_error"
    feedback_source_hash = next((line.split("=", 1)[1].strip()
                                 for line in stderr.splitlines()
                                 if line.startswith("SOIL_FEEDBACK_SOURCE_HASH=") and "=" in line), None)
    reflection_source_attempts = next((int(line.split("=", 1)[1].strip())
                                       for line in stderr.splitlines()
                                       if line.startswith("SOIL_REFLECTION_SOURCE_ATTEMPTS=")
                                       and line.split("=", 1)[1].strip().isdigit()), None)
    def marker(name, default=None):
        value = next((line.split("=", 1)[1].strip() for line in stderr.splitlines()
                      if line.startswith(name + "=") and "=" in line), None)
        return default if value is None else value
    prompt_hash = marker("SOIL_PROMPT_HASH")
    response_hash = marker("SOIL_RESPONSE_HASH")
    returned_paths_hash = marker("SOIL_RETURNED_PATHS_HASH")
    parse_status = marker("SOIL_PARSE_STATUS")
    prompt_feedback_present = marker("SOIL_PROMPT_FEEDBACK_PRESENT")
    prompt_tokens = marker("SOIL_PROMPT_TOKENS")
    response_length = marker("SOIL_RESPONSE_LENGTH")
    if prompt_tokens is not None and prompt_tokens.isdigit():
        prompt_tokens = int(prompt_tokens)
    if response_length is not None and response_length.isdigit():
        response_length = int(response_length)
    strategy_shape = strategy_memory.diff_shape(repo, commit) if commit else None
    strategy_id = (strategy_memory.strategy_fingerprint(recovered, strategy_shape)
                   if recovered else None)
    ctx.ledger.append({"kind": "cycle", "commit": commit, "task_id": task.task_id,
                       "attempt_id": getattr(ctx, "attempt_id", learning_state.new_attempt_id()),
                       "generation": ctx.generation, "soil_cycle": ctx.soil_cycle,
                       "exit_code": result.returncode,
                       "changed_paths": recovered,
                       "strategy_fingerprint": strategy_id,
                       "strategy_shape": strategy_shape,
                       **({"prompt_hash": prompt_hash} if prompt_hash else {}),
                       **({"prompt_tokens": prompt_tokens} if prompt_tokens is not None else {}),
                       **({"prompt_feedback_present": prompt_feedback_present} if prompt_feedback_present else {}),
                       **({"response_hash": response_hash} if response_hash else {}),
                       **({"response_length": response_length} if response_length is not None else {}),
                       **({"parse_status": parse_status} if parse_status else {}),
                       **({"returned_paths_hash": returned_paths_hash} if returned_paths_hash else {}),
                       **({"feedback_source_hash": feedback_source_hash} if feedback_source_hash else {}),
                       **({"reflection_source_attempts": reflection_source_attempts} if reflection_source_attempts is not None else {}),
                       **({"failure_reason": failure_reason} if failure_reason else {})})
    if commit is None:
        print(f"种子未产出候选（exit {result.returncode}）："
              f"{(result.stdout + result.stderr)[-400:]}", file=sys.stderr)
        return None, worktree
    return commit, worktree


def _drop_worktree(repo, worktree) -> None:
    """拆掉候选 worktree，**失败要出声**。

    原先这里静默吞掉 `git worktree remove` 的失败：注册项会一次次泄漏而没有任何
    痕迹。同时删掉 `mkdtemp` 建的那层父目录 —— `git worktree remove` 只认
    `wt/` 那一级，父目录每跑一次 `manual-cycle` 就留一个空壳，
    无人值守跑一夜会攒出一堆。
    """
    worktree = pathlib.Path(worktree)
    result = subprocess.run(["git", "worktree", "remove", "--force", str(worktree)],
                            cwd=str(repo), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"worktree remove 失败（注册项可能泄漏）：{result.stderr.strip()[:200]}",
              file=sys.stderr)
    shutil.rmtree(worktree.parent, ignore_errors=True)


def _preflight_has_pending_promotion(repo: pathlib.Path) -> bool:
    """Preflight refuses to reconcile promotion transactions into acceptance."""
    path = repo / "state" / "soil-ledger.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, ValueError):
        return True
    intents = [row for row in rows if row.get("kind") == "promotion_intent"]
    if not intents:
        return False
    def resolved(intent: dict) -> bool:
        source = intent.get("source") or intent.get("event_id")
        attempt = intent.get("attempt_id")
        commit = intent.get("commit")
        for row in rows:
            if row.get("kind") == "accepted_fitness":
                if ((source and row.get("source") == source)
                        or (attempt and row.get("attempt_id") == attempt)):
                    return True
            if row.get("kind") == "promotion_committed":
                if ((attempt and row.get("attempt_id") == attempt)
                        or (commit and row.get("commit") == commit)):
                    return True
            if row.get("kind") == "promotion_outcome":
                if ((source and row.get("source") == source)
                        or (attempt and row.get("attempt_id") == attempt)):
                    return True
        return False
    return any(not resolved(intent) for intent in intents)


def _preflight_panel(commit: str, diff: str, task):
    """H1-preflight is measurement-only: it can never authorize promotion."""
    return _pipeline.Verdict(False, "manual", "H1-preflight: promotion disabled")


def manual_cycle(*, calibration: bool = False, candidate=None, task_path=None,
                 preflight: bool = False) -> int:
    """§12.0.2：**走与未来 heartbeat 完全相同的代码路径。** 唯一的区别是判决位上坐着人。"""
    repo = REPO
    task = _load_task(repo, task_path)
    try:
        runtime_manifest.verify(repo, task_id=task.task_id)
    except runtime_manifest.RuntimeManifestError as exc:
        print(f"runtime manifest refused: {exc}", file=sys.stderr)
        return 2
    ctx = _soil_state.SoilContext.open(
        repo, generation=_generation(repo), soil_cycle=_next_soil_cycle(repo),
        calibration=calibration)

    # Preflight must not let startup reconciliation create accepted/promotion events.
    if preflight and _preflight_has_pending_promotion(repo):
        print("H1-preflight refused: pending promotion transaction requires soil recovery first",
              file=sys.stderr)
        return 2
    # 启动必跑。**P0-a 不只是在测种子，也是在测土壤自己的事务链**：
    # 崩溃恢复要到 P0-c 无人值守时才第一次被真正需要，绕过它就等于没测（§12.0.2）。
    for commit, outcome in _pipeline.reconcile_on_start(repo, ctx):
        print(f"reconcile: {commit[:12]} -> {outcome.name}")

    # **先记这一拍发生过，再做任何校验。**
    # 拍号由台账里的最大拍号推进（见 `_next_soil_cycle`）；若只在校验通过后才写
    # 带拍号的事件，一次被拒的尝试就不推进拍号 —— 而 C1 的 `eligible_after`
    # 恰恰要求拍号先前进。那会死锁，实测过。
    # **推进拍号的动作，不得挂在拍号所守的那道闸后面。**
    ctx.ledger.append({"kind": "cycle", "commit": None, "task_id": None,
                       "attempt_id": getattr(ctx, "attempt_id", learning_state.new_attempt_id()),
                       "generation": ctx.generation, "soil_cycle": ctx.soil_cycle,
                       "exit_code": None,
                       "path": "candidate" if candidate else "seed",
                       "calibration": calibration})
    if calibration and candidate is None:
        print("--calibration 必须配 --candidate <sha>：校准是**人工给定的确定能提升的"
              "变更**（§12.0.1），不经种子产出。", file=sys.stderr)
        return 2

    feedback_projection.write_projection(repo, task_id=task.task_id)
    if not feedback_projection.projection_is_fresh(repo):
        raise RuntimeError("task-scoped feedback projection freshness gate failed before worker start")
    try:
        feedback_doc = json.loads((repo / "seed" / "feedback.json").read_text(encoding="utf-8"))
        task_state = feedback_doc.get("facts", {}).get("task_states", {}).get(task.task_id, {})
    except (OSError, ValueError, TypeError):
        task_state = {}
    if task_state.get("state") in {"parked", "fulfilled", "blocked", "promotion_gated"}:
        ctx.ledger.append({"kind": "cycle", "commit": None, "task_id": task.task_id,
                           "attempt_id": getattr(ctx, "attempt_id", learning_state.new_attempt_id()),
                           "generation": ctx.generation, "soil_cycle": ctx.soil_cycle,
                           "exit_code": 2, "failure_reason": "task_guarded",
                           "task_state": task_state.get("state")})
        feedback_projection.write_projection(repo, task_id=task.task_id)
        print(f"task guarded: {task.task_id} state={task_state.get('state')}", file=sys.stderr)
        return 2
    worktree = None
    if candidate is None:
        commit, worktree = _seed_candidate(repo, ctx, task)
        if commit is None:
            feedback_projection.write_projection(repo, task_id=task.task_id)
            _drop_worktree(repo, worktree)
            return 1
    else:
        commit = _pipeline.git(repo, "rev-parse", candidate)

    try:
        outcome = _pipeline.process_candidate(
            commit, task, repo=repo,
            panel=_preflight_panel if preflight else manual_prompt,
            ctx=ctx)
    except _pipeline.TaskDeclarationError as exc:
        # **设计内的拒绝不该以 traceback 出现。** Task 声明违反 §8.1.4 是一个
        # 预期结果（C1 的 eligible_after 就是靠它生效的），操作员该看到的是
        # 一句说明为什么，不是一段栈 —— 栈会让人以为土壤坏了。
        print(f"Task 声明被拒：{exc}", file=sys.stderr)
        return 2
    finally:
        if worktree is not None:
            _drop_worktree(repo, worktree)

    feedback_projection.write_projection(repo, task_id=task.task_id)
    print(f"outcome: {outcome.name}")
    if calibration:
        print("校准：已测量、强制回滚、**永不 merge** —— 结构上产不出 accepted_fitness"
              "，因此永不计入点火（§12.0.1）。")
    return 0


def freeze_probe(proposal: str, repo=None) -> int:
    """把一份提案冻结进 vault 并写 C1 登记（`§7 author_probe` 的土壤半）。

    **没有这条命令，冻结机制就没有调用方** —— 而一个零调用点的机制，
    这份规格自己写过它能怎样活过 400 拍。C1 的实现落地时刻意把 CLI 留在范围外
    （交付机制与测试），这里补上那一步。

    `proposed_commit` 取当前 HEAD：冻结这个动作发生在**这棵树**上，
    `eligible_after` 因此从当前拍号起算 —— 这正是 C1 要挡的东西：
    **同一个 Change 里先写尺、再写刚好通过这把尺的能力**，
    那样写出来的尺在它自己那一拍不可用。
    """
    repo = pathlib.Path(repo) if repo is not None else REPO
    ctx = _soil_state.SoilContext.open(
        repo, generation=_generation(repo), soil_cycle=_next_soil_cycle(repo))
    try:
        entry = _pipeline.freeze_proposal(
            proposal, ctx=ctx,
            proposed_commit=_pipeline.git(repo, "rev-parse", "HEAD"),
            created_by="operator")
    except _probe_runner.ProbeProposalError as exc:
        print(f"冻结被拒：{exc}", file=sys.stderr)
        return 1
    print(f"已冻结 {entry['probe_id']}：")
    print(f"  frozen_probe_manifest_sha = {entry['frozen_probe_manifest_sha']}")
    print(f"  eligible_after            = {entry['eligible_after']}")
    return 0


def learning_status(repo=None) -> int:
    """Read-only Learning Runway status; never declares ignition/H1."""
    repo = pathlib.Path(repo) if repo is not None else REPO
    path = repo / "seed" / "feedback.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        facts = doc["facts"]
    except (OSError, ValueError, KeyError, TypeError):
        print("learning_status: projection unavailable", file=sys.stderr)
        return 1
    attempts = facts.get("recent_attempts", [])
    strategies = facts.get("strategy_memory", {})
    states = facts.get("task_states", {})
    repeated = sum(1 for item in strategies.values()
                   if item.get("repeated_failure"))
    diagnostics = [item.get("diagnosis_class") for item in attempts]
    try:
        rows = [json.loads(line) for line in (repo / "state" / "soil-ledger.jsonl").read_text().splitlines()
                if line.strip()]
    except (OSError, ValueError):
        rows = []
    promotions = sum(1 for row in rows if row.get("kind") == "accepted_fitness")
    linked = [row for row in rows if row.get("kind") in {"cycle", "observed_fitness", "promotion_outcome", "accepted_fitness"}]
    linked_count = sum(1 for row in linked if row.get("attempt_id"))
    fault_complete = (linked_count / len(linked)) if linked else 1.0
    print(json.dumps({
        "h1": "frozen",
        "feedback_readable": True,
        "attempts_observed": len(attempts),
        "strategy_count": len(strategies),
        "repeated_strategy_count": repeated,
        "task_states": states,
        "diagnosis_classes": diagnostics,
        "reflection_present": bool(facts.get("reflection")),
        "fault_attribution_completeness": fault_complete,
        "best_delta": max((item.get("delta") for item in attempts
                            if isinstance(item.get("delta"), (int, float))),
                           default=None),
        "promotion_count": promotions,
    }, sort_keys=True))
    return 0


def ignition_status(repo=None) -> int:
    """§1.2 判据的**唯一求值点**（§12.0.2）。只读台账，不查 task registry。

    **退出码只区分「读数产出了」与「读数产不出来」，不区分计数多少。**
    0 = 读数已产出（计数是 0 还是 5 都算产出）；1 = **台账损坏，读数不可得**。
    绝不让退出码携带判据语义 —— 判据的定义在 §1.2，不在某个人对退出码的理解里，
    否则下一个读者就会拿 `if ignition-status; then` 当判据用。

    **台账损坏走 fail closed，不是抛 traceback。** §1.2 要求谓词严格下标、
    缺键当场抛错（不许读者猜方向）——那条纪律留在谓词里；而**命令**必须把它
    翻译成一句可处置的话。一个读不出数的仪表要说「我读不出」，
    不是把栈打在操作员脸上：这正是判据最需要成立的时刻（崩溃恢复、事后审计）。
    """
    repo = pathlib.Path(repo) if repo is not None else REPO
    rows = _soil_state.Ledger(repo / "state" / "soil-ledger.jsonl").read()
    try:
        hits = [ev for ev in rows if _pipeline.is_ignition_event(ev)]
    except (KeyError, TypeError) as exc:
        print(f"台账损坏，出生判据无法求值：缺失或类型错误的字段 {exc}\n"
              f"  台账：{repo / 'state' / 'soil-ledger.jsonl'}\n"
              f"  §8.2 的强制字段与 records schema 由写入侧保证；"
              f"读到不合规的行意味着有东西绕过了 substrate/soil_state.Ledger。",
              file=sys.stderr)
        return 1

    print(f"ignition events: {len(hits)}   (criterion §1.2)")
    for ev in hits:
        rec = next(r for r in ev["records"]
                   if r["probe_id"] == ev["primary_probe"] and r["status"] == "improved")
        print(f"  soil_cycle {ev['soil_cycle']}  commit {str(ev['commit'])[:12]}  "
              f"task {ev['task_id']}  {ev['primary_probe']}  "
              f"{rec['before']} → {rec['after']}")

    counts: dict = {}
    try:
        for ev in rows:
            reason = _pipeline.ignition_exclusion_reason(ev)
            if reason is not None:
                counts[reason] = counts.get(reason, 0) + 1
    except (KeyError, TypeError) as exc:
        print(f"台账损坏，excluded 归因无法求值：{exc}", file=sys.stderr)
        return 1
    # 归因顺序定死（§12.0.2）：读数不稳定的仪表比没有仪表更坏。
    # **顺序从 `pipeline.IGNITION_CONJUNCTS` 派生，不在这里手抄一份** ——
    # 抄一份就是两个独立维护的副本，而这个项目到处在防的正是这种漂移。
    # `ignition_exclusion_reason` 对第一项返回的是带说明的 `kind≠accepted_fitness`，
    # 其余三项与合取项同名，故按前缀匹配对齐。
    parts = []
    for conjunct in _pipeline.IGNITION_CONJUNCTS:
        for key, count in counts.items():
            if key == conjunct or key.startswith(conjunct):
                parts.append(f"{count} {key}")
    print("excluded: " + (" · ".join(parts) if parts else "0"))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="supervisor")
    parser.add_argument("command",
                        choices=["manual-cycle", "ignition-status", "learning-status", "freeze-probe"])
    parser.add_argument("--proposal", default=None,
                        help="freeze-probe：要冻结的提案文件（seed/probe-proposals/<id>.json）")
    parser.add_argument("--calibration", action="store_true",
                        help="装置对照组（§12.0.1）：人工给定的变更，强制回滚、永不 merge")
    parser.add_argument("--candidate", default=None,
                        help="处理一个已存在的候选 commit，而不是跑种子产出候选")
    parser.add_argument("--preflight", action="store_true",
                        help="H1-preflight：允许测量但强制禁止 promotion")
    parser.add_argument("--task", default=None,
                        help=f"Task 声明路径（默认 {DEFAULT_TASK_DECLARATION}）")
    args = parser.parse_args(argv)

    if args.command == "ignition-status":
        # 判据求值是**只读**的，闩不该挡住读数 —— 停机时最需要看的就是它。
        return ignition_status()

    if args.command == "learning-status":
        return learning_status()

    if _refuse_if_latched():
        return 3

    if args.command == "freeze-probe":
        if not args.proposal:
            print("freeze-probe 需要 --proposal <path>", file=sys.stderr)
            return 2
        return freeze_probe(args.proposal)

    return manual_cycle(calibration=args.calibration, candidate=args.candidate,
                        task_path=args.task, preflight=args.preflight)


if __name__ == "__main__":
    raise SystemExit(main())
