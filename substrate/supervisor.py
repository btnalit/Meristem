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
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from root import panic  # noqa: E402
from substrate import pipeline as _pipeline  # noqa: E402
from substrate import probe_runner as _probe_runner  # noqa: E402
from substrate import soil_state as _soil_state  # noqa: E402

#: §13.3 表 C（v5.9 补）：种子经 `meristem/llm.py` 的每次模型调用都靠这个变量
#: 转发到 `substrate/model_gateway.py`。**必须是绝对路径，不能是 `python -m
#: substrate.model_gateway`。** `_seed_candidate()` 给种子子进程的 `PYTHONPATH`
#: 指向候选 worktree（见下），若网关命令依赖 PYTHONPATH 解析，Python 会把
#: `substrate.model_gateway` 解析成候选 worktree 里的那份拷贝——种子写不了
#: `substrate/`（白名单挡着），拷贝内容不会被篡改，但网关随后要靠自己的
#: `__file__` 找 `soil/model-policy.toml` 与 `state/`：worktree 里的 `state/`
#: 要么是空的（`state/` 被 gitignore，新 worktree 里根本不存在），要么将来若
#: 意外产生内容也是一份与真实台账脱钩的影子副本。绝对路径直接指向本仓库的
#: `model_gateway.py`，它的 `__file__` 落在真实仓库根，与 `budget.py` 的 REPO
#: 解析同一套逻辑，不依赖调用者的 cwd / PYTHONPATH。
MODEL_GATEWAY_ENTRYPOINT = f"{sys.executable} {REPO / 'substrate' / 'model_gateway.py'}"


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
    env = {**_probe_runner._sandboxed_env(), "PYTHONPATH": str(worktree),
           "MERISTEM_SOIL_CYCLE": str(ctx.soil_cycle),
           "MERISTEM_MODEL_GATEWAY": MODEL_GATEWAY_ENTRYPOINT}
    try:
        result = subprocess.run([sys.executable, "-m", "meristem.loop", "cycle"],
                                cwd=str(worktree), env=env, capture_output=True,
                                text=True, timeout=1800)
    except (subprocess.SubprocessError, OSError) as exc:
        result = SimpleNamespace(returncode=-1, stdout="", stderr=str(exc))
    head = _pipeline.git(worktree, "rev-parse", "HEAD") if result.returncode == 0 else None
    # 退出码 0 但 HEAD 没动 = 种子没提交任何东西。**那不是一个候选**，
    # 台账里就不能把 base 记成本拍产出的 commit —— 错标签比没有标签更坏，
    # 它看起来像数据（同 loop.py 拒绝把未知拍号伪造成 0 的理由）。
    commit = head if head is not None and head != base else None
    ctx.ledger.append({"kind": "cycle", "commit": commit, "task_id": task.task_id,
                       "generation": ctx.generation, "soil_cycle": ctx.soil_cycle,
                       "exit_code": result.returncode})
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


def manual_cycle(*, calibration: bool = False, candidate=None, task_path=None) -> int:
    """§12.0.2：**走与未来 heartbeat 完全相同的代码路径。** 唯一的区别是判决位上坐着人。"""
    repo = REPO
    ctx = _soil_state.SoilContext.open(
        repo, generation=_generation(repo), soil_cycle=_next_soil_cycle(repo),
        calibration=calibration)

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
                       "generation": ctx.generation, "soil_cycle": ctx.soil_cycle,
                       "exit_code": None,
                       "path": "candidate" if candidate else "seed",
                       "calibration": calibration})

    if calibration and candidate is None:
        print("--calibration 必须配 --candidate <sha>：校准是**人工给定的确定能提升的"
              "变更**（§12.0.1），不经种子产出。", file=sys.stderr)
        return 2

    task = _load_task(repo, task_path)
    worktree = None
    if candidate is None:
        commit, worktree = _seed_candidate(repo, ctx, task)
        if commit is None:
            _drop_worktree(repo, worktree)
            return 1
    else:
        commit = _pipeline.git(repo, "rev-parse", candidate)

    try:
        outcome = _pipeline.process_candidate(commit, task, repo=repo,
                                              panel=manual_prompt, ctx=ctx)
    except _pipeline.TaskDeclarationError as exc:
        # **设计内的拒绝不该以 traceback 出现。** Task 声明违反 §8.1.4 是一个
        # 预期结果（C1 的 eligible_after 就是靠它生效的），操作员该看到的是
        # 一句说明为什么，不是一段栈 —— 栈会让人以为土壤坏了。
        print(f"Task 声明被拒：{exc}", file=sys.stderr)
        return 2
    finally:
        if worktree is not None:
            _drop_worktree(repo, worktree)

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
                        choices=["manual-cycle", "ignition-status", "freeze-probe"])
    parser.add_argument("--proposal", default=None,
                        help="freeze-probe：要冻结的提案文件（seed/probe-proposals/<id>.json）")
    parser.add_argument("--calibration", action="store_true",
                        help="装置对照组（§12.0.1）：人工给定的变更，强制回滚、永不 merge")
    parser.add_argument("--candidate", default=None,
                        help="处理一个已存在的候选 commit，而不是跑种子产出候选")
    parser.add_argument("--task", default=None,
                        help=f"Task 声明路径（默认 {DEFAULT_TASK_DECLARATION}）")
    args = parser.parse_args(argv)

    if args.command == "ignition-status":
        # 判据求值是**只读**的，闩不该挡住读数 —— 停机时最需要看的就是它。
        return ignition_status()

    if _refuse_if_latched():
        return 3

    if args.command == "freeze-probe":
        if not args.proposal:
            print("freeze-probe 需要 --proposal <path>", file=sys.stderr)
            return 2
        return freeze_probe(args.proposal)

    return manual_cycle(calibration=args.calibration, candidate=args.candidate,
                        task_path=args.task)


if __name__ == "__main__":
    raise SystemExit(main())
