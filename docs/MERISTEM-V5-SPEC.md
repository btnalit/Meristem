# Meristem v5：设计与实施规格

> 全新种子。除必要土壤层外，v3.1 的一切不继承。
> 本文档是**可开工的规格**，不是回顾报告——每个模块给出职责、不变量、数据结构、函数签名、验收判据。

**spec-v5.15-amended** · 日期：2026-08-24 · 方法：本体论优先（Ontology → Domain → Invariants → State → Capability → Schema → Contract → Implementation → Verification）

> **正文冻结**（§17.7 终止条款）。v5.8 修掉外部独立审计判定的 **1 项 P0 + 6 项 bootstrap/P1**；
> 该审计对架构的裁定是 **APPROVED**，对开工的裁定是 **APPROVED WITH BOOTSTRAP FIXES**——**修完即开工**。
> 冻结靠的是 §17.8 的 CI 断言集，不是自觉。此后新发现走代码评审 + §18 勘误行。

---

## 1. 目标与判据

**目标**：一个能证明自己在变好的系统。

### 1.1 三个假设（分阶段检验，每个都有否证条款）

v3.1 的根本错误：**直接假设 H3，从未检验 H1**。

| 假设 | 内容 | 检验阶段 | **否证条款** |
|---|---|---|---|
| **H1** | 给定一把分级量尺，本模型档位的种子能沿梯度爬 | **P0-a** | 三圈不过 → **换模型档位或改尺，不是改实验结构** |
| **H2** | 加上全部闸门后 H1 仍成立 | **P0-c** | H1 过而 H2 不过 → 闸门的成本被量化了，**逐项二分找出谁杀了梯度** |
| **H3** | 种子能自己造出可爬的梯度（自撰新尺） | **P1** | H3 不过 → 自进化的边界找到了，如实记录，不是失败 |

**自我改进是 H3，不是 H1。** v3.1 的 day-0 缺口 4 自己写着「Loop B 在中档模型上可能停滞」——然后就没有然后了，400 拍无人检验。

### 1.2 出生判据（**全文唯一定义点**）

> **P0-a 的三圈手工循环内，`primary_probe` 至少产生过一次被接受的提升（H1 成立）。**
> 不出现 → 按 H1 否证条款处理，**不许在死引擎上装闸门**。

上一版写的是「**至少有一个探针**的分数上升过一次」。这句话有两个洞，且都会被无意触发：
**anchor 的上升满足它**（而 §12.2 裁定 anchor 上升不加分），**`observed` 但未晋升的上升也满足它**（而 C2 裁定只数 accepted）。

**机器判据（`state/soil-ledger.jsonl` 上的事件谓词；实现者不得自撰等价物）：**

```python
def is_ignition_event(ev) -> bool:
    """**单条台账行的纯函数。** 不接 task 参数、不查任何注册表、不做多跳关联。"""
    return (ev["kind"] == "accepted_fitness"           # C2：仅已 merge 进 main 的
        and ev["calibration"] is not True              # §12.0.1：校准永不计数
        and ev["counts_as_progress"] is True           # §10：唯一非晋升出口写 False
        and any(r["probe_id"] == ev["primary_probe"]   # 事件自带；不查 task registry
                and r["status"] == "improved"          # I5 枚举
                for r in ev["records"]))               # §8.2：records 是封套内元素
```

> **为什么必须是纯函数（v5.8，外部独立审计的 P0）。**
> v5.7 的签名是 `is_ignition_event(ev, task)`，而 `ignition-status` 只扫 `soil-ledger`——
> **它无从知道某条 `accepted_fitness` 属于哪个 Task，也就取不到那个 Task 的 `primary_probe`**。
> 判据名义上唯一，实际上不可自包含求值、**不可重放**。
> 修法是让事件自带 Task 身份（§8.2），而不是让判据去做 `source → observed → task_id → registry` 的多跳追溯——
> **多跳追溯在崩溃恢复与审计时最先断，而那正是最需要它成立的时刻。**
>
> **严格下标，不用 `.get()`（同轮修正 v5.7 自己的错）。** v5.7 在这里写 `.get()` 并称之为「读者侧的纵深」。
> 那个说法是错的：`.get("calibration") is not True` 在缺键时**判True，事件被计入**；
> `.get("counts_as_progress") is True` 在缺键时**判False，事件不计入**——**同一种缺失，两个相反的方向**。
> 字段既已是强制的（§8.2），缺键就是台账损坏，**应当当场抛错，而不是由读者猜一个方向**。

**四个合取项各挡住一种误计，缺一不可：**

| 合取项 | 挡住的误计 | 缺了它会怎样 |
|---|---|---|
| `kind == "accepted_fitness"` | 被面板或 canary 拒掉的候选 | 一个 `grep '"status":"improved"'` 就把 `observed_fitness` 一并算进来 |
| `calibration is not True` | 实验者自己造的确定提升 | **用装置对照组的读数宣布装置活着——循环论证** |
| `counts_as_progress is True` | 任何非晋升出口 | `finalize_nonpromotion()` 写的记录被当成进步 |
| `probe_id == ev["primary_probe"]` | anchor 的上升 | anchor 从外部锚变成第二把可挑的尺（§12.2） |

> **事件上的 `primary_probe` 由谁写、能不能被伪造**：由**土壤**在 `process_candidate` 写入事件时，
> 从已校验的 Task 声明**复制**进来（§8.1.4 已强制它必须是 internal probe）。
> 种子既不能写台账（C4），也就无法事后改动它。**复制发生在校验之后、写入的同一步**，
> 中间没有种子可介入的窗口。

**求值只有一个实现点**：`python -m substrate.supervisor ignition-status`（§12.0.2）。
本判据在全文其余各处**只被引用，不被复述**（§12、§12.0、C2）。

> **为什么必须给一条命令，而不是让实现者自己去 grep。**
> 上一版把判据写成散文，于是 §12 的表格里长出了 `三圈内出现一次 status:"improved"`——
> **一句照着抄就会写错的话**。判据一旦有两个求值处，它们迟早不一致；而这是整个 P0-a 的唯一出口，
> 不一致的代价是**在一个并没有活过来的系统上宣布 H1 成立**。
> 判据由土壤求值、由土壤打印——这与 S8「种子不得叙述成绩」是同一条原则，**延伸到实验者自己身上**。

---

## 2. 现实约束（实测，非假设）

| 事实 | 值 | 来源 |
|---|---|---|
| `substrate/supervisor.py` | 1255 行，**只 import `from root import panic`** | 全仓 grep 零命中 meristem |
| `root/` | 133 行，零外部依赖 | panic.py 76 + successor_loader.py 57 |
| 土壤↔种子接缝 | **CLI 契约**，非 Python API | 6 处 `subprocess.run([...,"-m","meristem.loop",...])` |
| 待清盘 | **10,631 行**（meristem 2999 + body 3619 + tests 4013） | `wc -l` |
| 待保留 | **1,388 行**（substrate 1255 + root 133） | 同上 |
| 删种子的后果 | **不崩溃**，3 拍失败 → rollback → keeper 停机 | 所有调用点有存在性防护 |

**两处语义债务**（清盘时必须主动处理，它们不会报错）：

- `substrate/supervisor.py:80-82` 硬编码 `"meristem/gates/deterministic.py"` 与正则 `meristem/[\w/.-]+\.py`
- `substrate/supervisor.py:379-383` 手工镜像 `LIFECYCLE_STAGES`

---

## 3. 本体论反推：土壤是什么

### 3.1 推导原则

v3.1 的土壤定义是**否定式**的——「种子不许碰的一切」。这个定义有两个后果，都已被 400 拍证实：

1. 边界靠**列举**维持，列举必然漏（`body/` 可写不可见、`state/*.jsonl` 可写无闸、`.pytest_cache` 混入上下文）
2. 定义没有说**谁来维护土壤**，于是维护者成了系统里唯一不过闸门的变异路径（C-1~C-64 全部是土壤层的坑）

**v5 的肯定式定义：**

> **土壤 = 种子不得自行撰写的那一组东西，因为一旦它能撰写，它就能伪造自己的进步。**

这条原则可判定：对任何模块问一句「**如果种子能改它，它能不能因此让一个没变好的变更看起来变好了？**」——能，则归土壤。

### 3.2 八个不变核心模块（逐条反推）

脊柱是 `选题 → 变更 → 测量 → 保留/丢弃 → 历史累积`。逐环节问上面那句话：

#### S1. 脉搏（Pulse）——「何时行动」

**若种子自定**：它可以一直重试直到撞上一个幸运结果，而「进步」变成采样偏差。
**土壤提供**：心跳，间隔由土壤决定且种子不可读写。
**实证**：现有 heartbeat（15–45 分钟随机间隔，14 拍一轮）已验证有效，**原样保留**。

#### S2. 基线身份（Baseline Identity）——「之前是什么」

**若种子自定**：它可以改写「之前的分数」，任何变更都能显示为提升。
**土壤提供**：① 分数记录的**唯一写入权**；② 变更的祖先关系（git ancestry）。
**实证**：v3.1 把逐拍抽样的分数写入交给了内核，导致记分板 **13/16 陈旧**，闸门拿一个从未测过的数当基线。**这是把基线身份留在种子手里的直接后果。**
**v5 变更**：**分数只由土壤在晋升后写入**（全套探针，带 commit 归属）。
种子**不直接读记分板**——记分板在 `state/soil-*.jsonl` 族内，worker 不可读（§15.6）；
种子只经 `seed/feedback.json` 的土壤投影看到**摘要**（T5，§12.0.3），与 §16 矩阵一致。

#### S3. 量尺仓库（Vault）——「用什么量」

**若种子能读 rubric**：它会针对 rubric 优化而非针对能力。
**土壤提供**：仓库位于仓库树与 worktree 之外，路径只由土壤解析，种子无任何读取入口。
**实证**：现有 vault 机制正确；**两次泄漏都是维护者造成的**（C-43/C-47），不是种子。
**v5 变更**：vault 路径解析**只在土壤代码里**；种子侧连常量都不该有。

#### S4. 判决（Verdict）——「谁说它可以留下」

**若种子自判**：判决无价值。
**土壤提供**：评审面板的**调用权**与**晋升决定权**。
**实证**：2/2 异构面板已验证能抓到 fail-open 转换、隐藏依赖、关键词收窄；且 cycle 391 批准了种子的一次自我拒绝。
**v5 变更**：面板的**提示词与判据文本**归土壤（种子可提案修改，但须经面板本身批准）。

#### S5. 记录（Record）——「发生过什么」

**若种子能改写历史**：「它变好了吗」不可回答。
**土壤提供**：append-only 日志的写入权，**以及「关于进步的记录长什么样」的 schema**。
**实证**：v3.1 的日志文件是共享的，而**记录的内容由内核撰写**——`proved_better_by` 在 166 个周期里声称证明了变好，实际发生 0 次。**文件是 append-only 的，语义不是。**
**v5 变更**：**fitness 记录的 schema 由土壤定义并由土壤写入**，种子不产出关于自身进步的断言。

#### S6. 停止（Stop）——「谁能叫停」

**若种子能阻止被停**：没有安全。
**土壤提供**：panic 闩、keeper、崩溃后的有界续跑。
**实证**：现有 `root/panic.py` + keeper 已验证；P-077 修好了「崩溃即停机等人」。**原样保留。**

#### S7. 资源（Resources）——「能花多少」

**若种子自定预算**：预算是建议。
**土壤提供**：模型槽位配置、配额与花费的记账与执行。
**实证**：v3.1 的 ledger 在**内核**里，`campaign_calls` 全时段累计撞上 1000 → `check()` 在每次变异和每次反思抛错 → **循环死锁，而唯一能修这道门的人被锁在门外**。
**v5 变更**：**预算执行移入土壤**，在种子进程之外判定；种子只读余量。

#### S7-Credential Adapter / Operator Wrapper（soil-owned boundary）

正式 provider 运行不得依赖操作者手工拼接 `MERISTEM_CREDENTIALS_FILE`。仓库提供唯一
operator wrapper：`substrate/run-soil.sh`。它是**启动适配器**，不是 gateway，也不是 worker
能力；高层架构仍为：

```text
/RSI/meristem-env
  → run-soil.sh（root/operator，临时文件生命周期）
  → MERISTEM_CREDENTIALS_FILE（soil:soil 0600）
  → soil gateway（setpriv → soil）
  → Unix socket ABI
  → worker（无 key、无 pointer）
```

**Contract：**

1. wrapper 必须以 root/operator 身份启动；不得由 worker 或 seed 调用。
2. credential source 默认固定为 `/RSI/meristem-env`，可通过显式 `MERISTEM_ENV_FILE`
   覆盖；source 文件必须是 root:root、0600、regular、非 symlink，否则 fail closed。
   它是 **assignment-only data file**：只允许固定变量名的 `KEY=value` / `export KEY=value`
   与注释/空行；wrapper 不执行 source 文件，命令、函数、命令替换、管道、重定向、分号等
   executable syntax 一律拒绝。
3. mode 只能是 allowlist：`agnes-temporary`、`openrouter-free`、`sensenova`。
   缺省为当前 primary `agnes-temporary`；备用 mode 必须通过 `--mode` 显式选择，不能由
   worker 或 provider failure 隐式切换。
4. wrapper 按 mode 使用固定 source-env 映射：
   `agnes-temporary → AGNES_API_KEY`、`openrouter-free → OPENROUTER_API_KEY`、
   `sensenova → SENSENOVA_API_KEY`。禁止接受任意 env-name 作为参数。
5. wrapper 每次运行生成临时 credential file，写入后设置 soil:soil、0600，并只向
   soil gateway 注入 `MERISTEM_CREDENTIALS_FILE`；不得持久化 key、打印 key、写入
   ledger/prompt/summary 或传给 worker。
6. 子进程使用 allowlisted environment；三类 provider key 与 source 文件中的其他
   secret 不得继承到 supervisor、worker 或自定义子命令。
7. 正常退出、失败、可捕获信号和启动异常都必须删除临时 credential file；清理失败必须
   明确报错并使 wrapper 返回非零。`SIGKILL` 无法由任何 shell trap 捕获，属于明确残余风险；
   runtime directory 必须 root:root 0700，后续启动/运维检查必须发现并清理 stale credential files。
8. wrapper 只能把 `--` 后参数传给正式 `substrate.supervisor` CLI（由 supervisor 自己执行
   command allowlist）；wrapper 不接受任意 binary，不解析或修改 ledger，不启动 provider fallback，
   也不自行决定 promotion/H1。supervisor/pipeline 仍是唯一 authority；通过 wrapper 调用
   `manual-cycle --autonomous` 仍必须满足 supervisor 自身的正式 H1 gate。
9. wrapper 的验收必须包含：mode 映射、父环境 secret 拒绝、文件 owner/mode、worker
   surface 无 pointer/key、异常清理、SIGTERM/非零退出清理和真实 supervisor dry-run。

该 contract 把此前真实 smoke 使用的“一次性 soil:soil 0600 bridge”正式化为可审计的
operator 入口，但仍保持**每次运行临时、退出即删除**；不建立永久 credential 文件。

#### S8. 自省渲染（Self-report Rendering）——「成绩由谁叙述」

**若种子自撰成绩**：它可以在渲染层把「8 把尺读零」写成「一切正常」，下一拍它就不会去修尺。
**土壤提供**：REPORT.md 的 **fitness / pressure 区段**由土壤从自己的 scoreboard 与 soil-ledger 渲染；`status` 的压力数字由土壤计算，种子只转发。
**种子保留**：**方向的叙述**——议程摘要、parked 列表、它对自己下一步的判断。

> **种子可以叙述方向，不得叙述成绩。**

**实证**：`proved_better_by` 是一次**渲染层的谎言**——底层文件 append-only，数据也没被篡改，**假话出在「把 score>0 叫做证明变好了」这一步渲染上**。防篡改防不住这个。

**实现：三个文件，不是一个文件两段。**

```
soil/report-facts.json     土壤独占写   成绩事实
seed/narrative.md          种子独占写   方向叙事
REPORT.md                  土壤渲染生成  = renderer(facts, narrative)
```

> **两段式仍然是共享写入面。** 标记可以被删、整个文件可以被重写、可以写临时文件再替换。
> **C4 已经裁定：共享写入面永远被逐渐扩权。** 同一条原则适用于 REPORT——
> **种子不直接写 `REPORT.md`，它是土壤的派生物。**

### 3.3 反推结论：模块归属的变化

| 模块 | v3.1 | v5 | 依据 |
|---|---|---|---|
| 心跳 | 土壤 | 土壤 | 不变 |
| panic / keeper | 土壤 | 土壤 | 不变 |
| 评审面板调用 | 土壤 | 土壤 | 不变 |
| vault | 土壤 | 土壤 | 不变 |
| **分数写入** | **内核** | **土壤** | S2：记分板 13/16 陈旧 |
| **fitness 记录 schema** | **内核** | **土壤** | S5：166 次假声明 |
| **预算执行** | **内核** | **土壤** | S7：死锁且不可自愈 |
| **评审提示词与判据** | **内核**（`gates/review.py`） | **土壤** | S4：**v3.1 的种子可以改自己的评审提示词** |
| **REPORT 的成绩区段** | **内核** | **土壤** | S8：渲染层的谎言 |
| 议程 / 选题 | 内核 | **内核** | 种子的方向由种子定 |
| 变更生成 | 内核 | **内核** | 同上 |
| rubric 撰写 | 种子（vault 侧） | **种子** | 禁止自撰就不是自进化实验 |

**五个模块从种子迁到土壤，每一个都锚定一次已发生的伪造、死锁或未被点名的漏洞。**

> 其中「评审提示词归土壤」是本次审查才发现的：v3.1 里 `meristem/gates/review.py` 在种子的可变面上，
> **种子可以修改判它自己的那份提示词与判据**。S4「判决归土壤」到 v5 才真正成立。

---

## 4. 领域模型

### 4.1 一等实体

| 实体 | 定义 | 身份 |
|---|---|---|
| **Task** | 一次「要改什么」的声明 | `task_id`（内容哈希，同文本即同任务） |
| **Change** | 一次具体的文件级变更 | `commit`（git sha） |
| **Probe** | 一把分级量尺 | `probe_id` |
| **Measurement** | 一次探针在一棵树上的运行结果 | `(probe_id, probe_manifest_sha, tree_sha, runner_version, execution_policy_version)` |
| **Verdict** | 面板对一次 Change 的裁决 | `(commit, panel_run)` |
| **Fitness** | 一次 Change 前后的分数对照 | `(commit, probe_id, probe_manifest_sha, runner_version, execution_policy_version)` |
| **Generation** | 一段连续的、契约未变的运行 | `gen_id` |

> **Fitness 的身份必须包含 Measurement 的三个版本维度**（本轮勘误）。
> 上一版 Fitness 身份是 `(commit, probe_id)`——**Measurement 好不容易带上的可比性，在配对这一步又被丢掉了**。
> 后果具体：用 runner v1 测的 before 和 runner v2 测的 after 配成一条，40→60 记作 `improved`，
> 而那 20 分可能整个来自换了 runner。**这与 v3.1 的 `proved_better_by` 是同一类错误**——
> 一个在测量层没有依据的进步断言，只不过这次藏在版本维度里。
>
> **强制机制（不是约定）**：`fitness.pair()` 逐条比对 before/after 的三个版本维度，
> **任一不等 → `status = "unmeasured"`**（复用 I5 既有枚举，不新增取值），
> 不产出 delta、不计入 `improved`、不计入拒绝额度（属机制故障，同 §10.2 的 `unmeasured` 语义）。

**刻意不是一等实体**（是投影或事件）：cycle（Task 与 Change 的一次配对尝试）、rejection（Verdict 的一种取值）、pressure（从 Measurement 派生）。

### 4.2 关系

```
Task      --proposes-->      Change        （1:N，一个任务可多次尝试）
Change    --measured_by-->   Measurement   （1:N，全套探针）
Change    --judged_by-->     Verdict       （1:1）
Measurement --pairs_into--> Fitness        （before/after 各一，配成一条）
Change    --supersedes-->   Change         （git parent）
Probe     --measures-->     Capability     （声明式；提案在 seed/，冻结件在 vault）
```

### 4.3 绝不允许存在的关系

| 禁止 | 理由 |
|---|---|
| `Seed --writes--> Measurement` | S2：种子写分数即可伪造基线 |
| `Seed --reads--> Probe.rubric` | S3：针对 rubric 优化 |
| `Seed --authors--> Fitness` | S5：`proved_better_by` 的原罪 |
| `Change --judged_by--> Verdict(author == judge)` | S4：自判 |
| `Operator --writes--> Change(bypassing Verdict)` | 裁定二：操作员在系统之内 |
| `Seed-owned code --runs inside--> Soil process` | **C6**：`probe_runner` 执行 organ 是穿透路径 |
| `Seed --writes--> REPORT.md` | S8：成绩是土壤的派生物 |
| `Seed --mutates--> frozen probe manifest` | C1：同一 `probe_id` 的 manifest hash 不得变化 |
| `Seed --writes--> soil-ledger` | C4：唯一台账，唯一写入者 |

---

## 5. 不变量（全部由机制强制，不靠提示词）

| ID | 不变量 | 强制点 |
|---|---|---|
| **I1** | 任何计数若单调递增且封顶，必须带滚动窗口或退役机制 | 土壤预算模块；新增计数须在土壤评审清单勾选 |
| **I2** | 一个 Probe 必须声明 ≥5 个子检查，且分数 = 通过数/总数×100 | 土壤对 seed 提案文件做 schema 校验 |
| **I3** | 一个 Probe 若最近 N 次运行只占用 ≤2 个不同档位，标记为 `degenerate_suspected`（**不是 `degenerate`**） | 土壤度量，写入 soil-ledger |
| **I4** | Fitness 记录只能由土壤写入，且必须含 `before/after/status` 三字段 | 土壤独占写入权 |
| **I5** | `status` 只能取 `{improved, no_regression, regressed, baseline, unmeasured}` | schema 枚举 |
| **I6** | 免疫层代码不得外化为可变 organ | 路径规则 + 土壤评审清单 |
| **I7** | 任何变更（含操作员的）必须有 Verdict 才能进入 main | 晋升守卫 |
| **I8** | 记录只增不改；工作集有界（登记册轮转，标题仍可 grep） | 土壤轮转模块 |
| **I9** | 土壤代码不得 import 种子代码 | 测试断言（已存在，保留） |
| **I10** | 三个理解预算各自成立：突变闭包 / prompt 面 / 契约面 | 土壤度量 + 拒绝 |

**I3 为什么只能是「疑似」**：只占用两个档位有多种合法原因——本就是二元的安全不变量、
能力已饱和到 100、变化空间本来只有 pass/fail、候选长期没触碰该能力。
**粗糙的统计规则会淘汰合法量尺。**

真正要判定的是「**这把尺能不能区分不同质量的 Change**」，证据至少要有：

1. 最近 N 个候选的分数分布
2. 候选之间的代码/行为**是否真的变化过**
3. 是否存在**已知的坏 mutation 却仍然同分**
4. 输入空间是否覆盖多个边界
5. 分数是否与独立 anchor 的变化**完全无关**

`degenerate_suspected` 只进 soil-ledger 与 fitness 区段供种子看见；**退役需人工确认**。

---

## 6. 状态机

### 6.1 Task

```
open ──take──> in_progress ──verdict:accept──> done
                    │
                    ├──verdict:reject──> open        (重试，判词入 failure_history)
                    ├──fault──────────> open        (机制故障，不计入拒绝额度)
                    └──rejections>=3──> parked ──reflect 再生──> (新 task_id)
```

**关键**：`fault` 与 `reject` 是**不同的转移**（v3.1 的 P-016 教训，继承）。

### 6.2 Probe

```
draft ──土壤 schema 校验(I2)──> active ──I3──> degenerate_suspected ──人工确认──> retired
                                  │
                                  └──rubric 变更──> draft  (须重新校验)
```

**没有 candidate/calibrate/register 六段生命周期**——v3.1 那套 `advance()` 零调用点，是纯纸面机制。**v5 用两个状态 + 一个降级标记。**

### 6.3 Change

```
proposed ──deterministic──> measured ──panel──> judged ──canary──> promoted
     │            │                      │            │
     └─refuse─────┴──────fault───────────┴──reject────┴──canary_reject──> discarded
```

---

## 7. 能力与授权

| 能力 | 谁可执行 | 前置条件 | 记录 |
|---|---|---|---|
| `propose_task` | 种子（reflect） | — | soil-ledger |
| `take_task` | 种子 | task 状态为 open 且未 parked | soil-ledger |
| `emit_change` | 种子 | 有 in_progress 的 task | git commit（worktree 内） |
| `measure` | **土壤** | Change 已提交 | scoreboard + soil-ledger |
| `judge` | **土壤**（调面板） | 已 measure | soil-ledger |
| `promote` | **土壤** | Verdict 通过 + canary 通过 | soil-ledger + git merge |
| `write_fitness` | **土壤** | 已 measure | soil-ledger（schema 强制） |
| `author_probe` | 种子 | 符合 I2 | 种子写 `seed/probe-proposals/<id>.json`；**土壤校验后写冻结登记与 vault manifest** |
| `enforce_budget` | **土壤** | 每拍 | soil-ledger |
| `panic` | **人** | — | latch 文件 |
| `prosthetic_intervention` | **人** | **土壤无法产出可信的 measurement / verdict / promotion**（不限于「产不出 cycle」） | decisions.jsonl（须记退出条件） |
| `soil_recovery` | **人 + root 授权** | 土壤自身损坏，无法为「修复土壤的变更」写 Verdict | root recovery manifest（前后记 hash） |

**操作员通道**：操作员的变更走与种子同一条 `emit_change → measure → judge → promote`，唯一区别是评审清单换成土壤版。

### 7.1 Bootstrap 权威问题（外部审查发现，本规格初稿未闭合）

初稿的假肢条件是「循环无法产出下一个 cycle」。**但如果坏的是 `substrate/fitness.py` 或
`substrate/probe_runner.py`，循环照常产出 cycle，只是测量是错的**——假肢恰恰在最需要时被拒绝。

更深一层：**修复「写 Verdict 的那段代码」的变更，由谁写 Verdict？**

**不动点：修 substrate 的授权不能存在于 substrate 里。** 它归 `root/`
（`successor_loader.py` 这个「世代权威」桩正是它的家）。

```
soil recovery path（root 持有，不可递归）
  1. root 记录 recovery manifest（修复前树 hash）
  2. 人工本地授权，作用域**限 `substrate/` + `soil/`**（禁区见上表）
  3. 修复
  4. root 记录修复后树 hash
  5. **冻结自主运行**，等待重新点火（P0-a 判据重跑一次）
```

**两类介入必须分开，永不混用：**

| | 触发条件 | 作用域 | 事后 |
|---|---|---|---|
| `prosthetic_intervention` | 土壤产不出可信的测量/判决/晋升 | 任意 | 记退出条件，循环继续 |
| `soil_recovery` | 土壤自身损坏，无法为修它的变更写 Verdict | **`substrate/` + `soil/`** | **冻结自主运行，重新点火** |

**`soil_recovery` 的绝对禁区**（比作用域更重要——这是根级钥匙）：

不得修改 `root/` · 不得修改 anchor vault · 不得修改 recovery manifest 本身 ·
不得修改历史台账 · 不得修改世代身份

> 作用域含 `soil/` 的理由：**本次实验的 `campaign_calls` 死锁正是 config 层的故障**，而 model-policy 住在 `soil/`。

---

## 8. 数据 Schema

### 8.1 Probe：两类，边界不可混

> **本节是外部审查发现的最严重漏洞的修法。** 初稿让种子撰写的 `probe.json` 携带
> `"entrypoint": ["python3", "check.py"]`，而土壤会执行它——**种子因此拥有在土壤中执行任意
> 代码的入口**：读 `SENSENOVA_API_KEY`、改 soil-ledger、改 scoreboard、改 vault、联网。
> 用本文档 §3.1 的判定原则问一遍「种子能不能因此让没变好的变更看起来变好」——**能，而且最直接：
> `check.py` 直接往记分板写 100。** 我写下了那条原则，却没有拿它检查自己的 schema。

| | **Anchor Probe** | **Internal Probe** |
|---|---|---|
| 作者 | **人** | 种子可提案 |
| 存放 | 土壤私有 | vault（冻结后） |
| 判定逻辑 | **可含可执行 rubric** | **声明式，土壤解释** |
| 种子可读 | **永不** | 创作时刻可见，冻结后永不 |
| 用途 | 外部锚，防系统性自欺 | 日常能力度量 |

#### 8.1.1 Internal Probe schema（种子可提案的唯一形态）

```json
{
  "id": "probe-classify-basic",
  "capability": "把失败原因分类到命名类别",
  "organ": "classifier",
  "checks": [
    {"id": "c1", "input": "closure ~52704 > 50000 budget",
     "cmp": "equals", "expect": "closure-budget"},
    {"id": "c2", "input": "touches protected path root/",
     "cmp": "equals", "expect": "protected-path"}
  ]
}
```

**没有 `entrypoint` 字段。** 种子可写的 schema 里不存在这个字段；出现即拒绝。

判定由土壤内置 runner 执行：`input → organ ABI → output → cmp(output, expect)`。

**比较器白名单**（`cmp` 的全部合法取值，由土壤实现）：

| `cmp` | 语义 |
|---|---|
| `equals` | 精确字符串相等 |
| `contains` | 子串包含 |
| `regex` | **土壤库中预置的命名正则**，不接受种子提供的正则字面量 |

> **代价要写明**：internal probe 的表达力上限 = 土壤 runner 的解释能力。
> 种子需要更复杂的判定，只有两条路——把复杂度写进 `expect` 让字符串匹配可判，
> 或**申请人工把该 probe 升格为 anchor**。这是真实的表达力损失，不藏。

#### 8.1.2 cases 的可见性边界（按时间划线，不按用途划线）

**冻结后，种子树里只剩 metadata stub：**

```json
{"id": "probe-classify-basic", "capability": "...", "organ": "classifier",
 "checks_total": 5, "frozen": true, "vault_ref": "internal/active/probe-classify-basic"}
```

**完整 cases（含 `input` / `expect`）只存在于 vault，种子冻结后永不可读。**

为什么必须这样：初稿把 `input` 和 `expect` 一并放在种子可见的 `probe.json` 里——
**种子看不到评分程序，但看得到题目和标准答案**。它可以硬编码那五条 case，
分数 40→100，**而这会显示为一次真实的 `improved`——一个假的 A 类事件**。
「分级量尺」的设计反而让作弊更容易了。

> **按时间划线而不是按用途划线**：「普通能力可见、免疫能力不可见」是按用途划的线，
> 用途会变、线会漂，三个月后没人记得哪个 probe 算免疫。
> 时间线只有一条规则、对所有 probe 一致：**创作时刻可见（§3.4 本就承认防不了），冻结后永不可见。**

#### 8.1.3 模型策略的种子/土壤边界（S7 的完整实现）

初稿保留 `control/models.toml` 而 `control/` 在种子可变面上——**种子能改 `cycle_calls`、
`campaign_calls`、review 槽位、`max_tokens`，S7 并不成立**。

| 文件 | 归属 | 内容 |
|---|---|---|
| `soil/model-policy.toml` | **土壤私有** | 预算、配额、review 槽位与血统、endpoint、retry |
| `seed/model-interface.json` | **种子只读** | 哪些角色可用；**不含任何配额数字** |

种子从接口只能知道一次调用的**结果**：`allowed` / `refused` / `deferred`。

> **不暴露配额数字**：否则种子可以通过挑角色、改 retry、卡窗口来影响选择压力。

**score = 通过的 check 数 / checks_total × 100。** 写 5 个断言和写 1 个断言难度相同，分数自动获得 6 档分辨率。

> cycle 386 那个「三个断言过两个」在 v5 得 **66.7**，那是一个 A 类事件；v3.1 记 **0.00**。

### 8.1.4 Task 声明 schema（种子撰写，土壤验证兑现）

> **目标由种子提出，结果由土壤计算。** 土壤不替种子选方向，但必须验证它兑现了自己声明的方向。

```json
{
  "task_id": "…",
  "kind": "repair",
  "target": "classifier",
  "primary_probe": "probe-classify-basic",
  "expected": "score_increase",
  "minimum_delta": 20.0,
  "regression_policy": "all-active-probes"
}
```

**`primary_probe` 的硬约束（土壤在校验 Task 声明时执行，违反即拒绝该声明）：**

| 约束 | 理由 |
|---|---|
| **`primary_probe` 必须是 internal probe；anchor 不得被声明为 primary** | 否则 evaluator 面前摆着两把尺，**哪把有利就用哪把**——「兑现声明」的验证随之失效 |
| 该 probe 须处于 `active`，且已过 C1 的 `eligible_after` | 防「同一 Change 里先写尺、再写刚好通过这把尺的能力」 |
| `regression_policy` 固定 `all-active-probes`，种子不可改 | 回归面由土壤定；**能收窄回归面就等于能挑尺** |

> **选题自由与量尺自由不是一回事。** 种子可以决定改什么（`target`）、期望什么（`expected`），
> 但**不能决定用哪一类尺来判自己**。本节开头写着「目标由种子提出，结果由土壤计算」——
> 若允许种子在 internal 与 anchor 之间挑一把当 primary，「结果由土壤计算」就被还回去了一半。

`expected` 枚举（**缺一不可，每一条都锚定一次实证**）：

| 取值 | 何时用 | 土壤如何验证 | 锚 |
|---|---|---|---|
| `score_increase` | 能力改进 | `primary_probe` 的 delta ≥ `minimum_delta` | 默认 |
| `cost_reduction` | 维护型改进 | 指定 metric（如 `prompt_tokens_per_success`）下降 | 否则维护改进被误判为无价值 |
| `refusal_with_reason` | **种子判定该任务不该做** | 有记录理由的拒绝 + 面板批准该拒绝 | **cycle 391**：种子拒绝外化免疫层并写下 P-025，那是全实验最好的一次行为 |
| `no_measurement` | 确无可测目标 | 仅要求无回归；**计入「未度量」统计** | 不留此口会逼种子编造目标 |

> **`refusal_with_reason` 是不可省的**：任何「声称什么就必须交付什么」的强制，
> 都会把 cycle 391 那种最好的行为判成失败。**拒绝并说明理由，必须和交付一样算兑现。**

### 8.1.5 土壤记录文件的命名与属主规则（前缀族，不是文件名清单）

> **保护一个文件名，下一个文件就漏。** v3.1 的 `state/*.jsonl` 可写无闸正是这个形态；
> 而本规格前一版只点名保护了 `soil-ledger`，将来新增一个 `measurements.jsonl` 会原样重演。

| 规则 | 效果 |
|---|---|
| 一切土壤独占写入的记录文件，路径必须匹配严格正则 **`^state/soil-[a-z0-9-]+\.jsonl$`** | **命名即归属** |
| 属主与权限按**前缀族**授予（§15.6），不按文件名逐个授予 | 新增 `state/soil-measurements.jsonl` 默认就在保护面内，不需要有人记得去加一行 |
| `state/` 下不得存在不匹配该正则的文件 | 由 **CA-8** 断言（§17.8） |
| **`state/` 下不得存在 symlink，也不得存在指向 `seed/` 或 `soil/` 的 hardlink** | 由 **CA-8** 断言 |
| **临时文件与备份文件一律不得落在 `state/`** | 写入路径：临时文件置于同分区他处 → `O_NOFOLLOW` → **原子 rename** 就位 |

**P0-a 的两个成员**：`state/soil-ledger.jsonl`（§8.2）· `state/soil-scoreboard.jsonl`（§8.3）。

> **为什么正则要严格到 `^…$`（v5.8，外部审计 P1）。**
> glob 式的 `soil-*` 会把 `soil-ledger.jsonl.bak`、`soil-scoreboard.jsonl.tmp` 一并算进族里——
> **备份和半截临时文件因此获得与权威台账相同的属主与信任**；
> 而实现者若改用别的写法，又可能把合法文件排除在外。**一条两端锚定的正则，两个方向同时封死。**
>
> **为什么还要禁 symlink / hardlink。** 前缀族是**路径**规则，而文件系统语义可以让路径与内容脱钩：
> `state/soil-fake.jsonl` 若是一个指向 `seed/` 的 hardlink，**名字合规、属主合规，内容却坐在种子的可写面上**。
> §10.1 已经为 `seed/` 白名单写过 `O_NOFOLLOW` 与链接检查——**同一条攻击换个方向而已，这里必须同样封死。**

> 这是 §10.1「目录结构是唯一能把边界做成非列举式的地方」在 `state/` 内部的应用：
> **目录给出保护面，前缀给出可 grep 的族身份**——两者都不靠列举维持。

### 8.2 `state/soil-ledger.jsonl` —— 唯一权威台账（**只由土壤写入**）

**台账的一行是一个事件；fitness 记录是事件里的元素，不自己占一行。**

> **本轮勘误**：C2 定下 `observed_fitness` / `accepted_fitness` 两阶段事件之后，
> 本节仍留着 `"kind":"fitness"` 的单行示例——**一个规格里从不存在、而实现者会照抄的 kind**。
> 照抄的后果不是报错，是 §1.2 的出生判据永远数不到东西。已删除。

**事件封套**（`kind` 的合法取值集合见 §17.8 CA-6）：

```json
{"ts":"...","kind":"observed_fitness","commit":"abc123","source":null,
 "task_id":"…","primary_probe":"probe-classify-basic",
 "generation":"g0","soil_cycle":123,
 "candidate_state":"measured","promotion_state":"pending",
 "calibration":false,"counts_as_progress":false,"records":[ … ]}
```

**`records[]` 的元素 schema**（fitness 记录本身，**无 `kind` 字段**）：

```json
{"probe_id":"probe-x","before":40.0,"after":60.0,"delta":20.0,"status":"improved",
 "checks_before":2,"checks_after":3,"checks_total":5,
 "measured_by":"soil","tree_before":"sha","tree_after":"sha",
 "probe_manifest_sha":"...","runner_version":"1","execution_policy_version":"1"}
```

`status` ∈ `{improved, no_regression, regressed, baseline, unmeasured}`（I5）。
**三个版本维度取自 before/after 两次 Measurement，二者必须相同**（§4.1）；不同则 `status = "unmeasured"`，不产出 delta。

**`calibration` 标在封套上，不标在记录里**（§12.0.1）——判据要能一次读出，
不必下钻到 `records[]`；标在记录里就等于要求每个读者自己去聚合，**那是下一个「声明了没断言」**。

#### 封套的六个强制字段（缺一即 schema 违例）

| 字段 | 写入者 | 取自 | 为什么强制 |
|---|---|---|---|
| `task_id` | **土壤** | 当前 Task 声明 | 事件↔Task 的关联；审计与重放的锚 |
| `primary_probe` | **土壤** | Task 声明（已校验必为 internal，§8.1.4） | **§1.2 谓词得以自包含求值** |
| `generation` | **土壤** | `ctx.generation` | 跨世代可比性；与 C1 `eligible_after` **同名同义**，不引入变体 |
| `soil_cycle` | **土壤** | `ctx.soil_cycle` | 事件在时间线上的位置；同上 |
| `calibration` | **土壤** | 是否走 `manual-cycle --calibration` | §12.0.1；**CA-7 的咬合面** |
| `counts_as_progress` | **土壤** | 是否晋升 | §10 唯一非晋升出口写 `False` |

**六个字段必须显式写出；缺键即台账损坏，读取方应当抛错，不得取默认值。**
六个全部由土壤写入，**种子不写台账（C4），因此无一可被种子伪造**。

> **理由：缺省值会让断言空真。** CA-7 断言「台账中不存在 `calibration: true` 的 `accepted_fitness`」——
> 键若从不存在，断言**恒为真**：一条永远绿、也永远不检查任何东西的断言。
> 同样的空真会在 `primary_probe` 上原样重演：字段缺失而读者取默认，§1.2 的第四个合取项就形同虚设。
>
> **v5.7 曾把 `.get()` 称作「读者侧的纵深」，那个说法是错的**（详见 §1.2）：
> 缺省在不同字段上的兜底方向**相反**——所谓「纵深」，实际上是两个方向不一致的猜测。
> **强制写入 + 缺键抛错**才是一致的。

**种子不产出任何关于自身进步的断言字段。**

### 8.3 `state/soil-scoreboard.jsonl`（**只由土壤写入**）

```json
{"ts":"...","kind":"probe","probe_id":"probe-x","score":60.0,
 "checks_passed":3,"checks_total":5,
 "tree_sha":"...","commit":"abc123","parent_sha":"...",
 "probe_manifest_sha":"...","runner_version":"1","execution_policy_version":"1",
 "source":"promotion-full-set"}
```

**每次晋升写全套**，不是逐拍抽样——这是 S2 的直接实现（v3.1 的 13/16 陈旧问题）。

**三个版本维度是可比性的前提**——`probe_manifest_sha` / `runner_version` /
`execution_policy_version`，**与 §4.1 的 Measurement 身份逐字相同**。更换 probe runner、
probe manifest 或执行策略之后，旧分数**不可直接与新分数比较**；缺了它们，这种不可比会
静默发生——又一个「声明了没断言」。

> **术语统一（本轮勘误）**：本节前一版把这个 sha 叫 `vault_manifest_sha`，而 §4.1 叫
> `probe_manifest_sha`，C1 冻结登记叫 `frozen_vault_manifest_sha`——**同一个东西三个名字**。
> 这正是 §17.5 点名的那种漂移（v3.1 的 `campaign` 一词，代价是循环死锁）。
> **全文统一为 `probe_manifest_sha`**，C1 登记项为 `frozen_probe_manifest_sha`，
> 且二者必须相等（**CA-9**，§17.8）。
>
> **不用「后 N 个字段」这种数数的说法**：字段一增删，计数就悄悄失真。**点名，不数数。**

> **记分板是历史 measurement 台账，不是当前 baseline 缓存。**
> 任何比较都必须回到树上现测（见 §10.3），不得从台账取数当基准。

---

## 9. API / CLI 契约

### 9.1 土壤 → 种子（必须逐字兼容，否则要同步改 supervisor.py）

| 子命令 | 退出码 | stdout 契约 |
|---|---|---|
| `python -m meristem.loop selftest` | 0=通过 | 不解析 |
| `python -m meristem.loop cycle` | **0 = 产出了一个候选 commit；非 0 = 没有候选或发生异常**。**注意：0 不再意味着「闸门都过了」**——v5 的测量与判决全部在土壤 | 不解析 |
| `python -m meristem.loop reflect [--pressure]` | 同上 | 不解析 |
| `python -m meristem.loop report` | **不检查** | 副作用：写 `REPO/REPORT.md` |
| `python -m meristem.loop status` | 非 0 触发 fault | **必须含** `core pressure: <float>` 行、`open agenda item` 行 |

**包路径 `meristem/loop.py` 不可更名**，否则须同步修改 `supervisor.py` 6 处调用点。

> **语义变更警告**：v3.1 的 `cycle` 内部完成 deterministic + probes + review 三件事，退出码 0 意味着「全部闸门通过」。
> v5 把这三件事全部判给土壤，`cycle` 的语义收窄为「**产出一个候选，仅此**」。
> supervisor 侧因此不是「改三处」，而是**新增一条候选处理流水线**（见 §10.3）。

### 9.2 土壤 → 探针（土壤自持，不经种子代码）

**v5 变更**：v3.1 里土壤通过 `python -c "from meristem.gates import probes"` 调用**种子的**探针运行器——**这违反 S2/S3**（种子可以改写量尺的运行方式）。

v5 的探针运行器**在土壤**：`substrate/probe_runner.py`，直接读 vault、执行 rubric、计算 `passed/total`。种子侧无探针运行代码。

---

## 10. 目录结构与模块规格

```
/RSI/Meristem/
├── root/                    保留原样（133 行）
│   ├── panic.py             恐慌闩
│   ├── successor_loader.py  世代权威（P0 桩）
│   └── generations.json
├── substrate/               保留 + **五项新增**（土壤）
│   ├── supervisor.py        心跳/晋升/canary/keeper 接口（1255 行，改动见 §13.3）
│   ├── probe_runner.py      【新】探针运行器（S3）           ~90 行
│   ├── fitness.py           【新】fitness 记录与 schema（S5）  ~70 行
│   ├── budget.py            【新】预算执行（S7）              ~60 行
│   ├── pipeline.py          【新】候选处理流水线（S2+S4+S5） 150–250 行
│   ├── model_gateway.py     【新】模型网关（S7 的执行端）      ~80 行
│   ├── run-soil.sh          【新】credential adapter/operator wrapper（S7）
│   └── panel.py             【新】面板（P0-b 主体工程）       100–150 行
├── meristem/                全新种子（P0-a ~415 行）
│   ├── __init__.py          路径常量 + 只读助手              ~35
│   ├── task.py              议程读取 / 取一个任务 / 标记完成  ~50
│   ├── engine.py            一次 LLM 调用 → 整文件替换 → 应用 ~90
│   ├── narrative.py         写 seed/narrative.md + status 契约 ~50
│   ├── llm.py               经土壤 IPC 请求一次调用            ~60
│   └── loop.py              脊柱 + CLI 入口                   ~110
├── body/organs/             P0-a 只有一个
│   └── classifier/          点火用的分类器 organ
├── soil/                    【土壤私有 —— 种子不可读不可写】
│   ├── model-policy.toml    预算/配额/review 槽位/endpoint/retry
│   ├── panel-policy/        面板 prompt、评审清单、土壤版清单
│   ├── metric-registry.json Metric Registry（§17.2）
│   ├── report-facts.json    成绩事实（S8）
│   └── report_renderer.py   facts + narrative → REPORT.md
├── seed/                    【种子可见可写】
│   ├── constitution.md      种子宪法（重写，~60 行）
│   ├── model-interface.json 只读能力描述，**不含任何配额数字**
│   ├── agenda.md            议程
│   ├── narrative.md         叙事（S8 的种子半）
│   └── feedback.json        【新】土壤写、种子只读的事实投影（T5）
├── state/                   【土壤独占写入 —— `soil-*.jsonl` 前缀族，§8.1.5】全部清空重建
│   ├── soil-ledger.jsonl    唯一权威台账（§8.2）
│   └── soil-scoreboard.jsonl 历史 measurement 台账（§8.3）
└── tests/                   【重写】P0-a 只测脊柱
```

### 10.1 种子模块规格

#### `meristem/loop.py` —— 脊柱

```python
def run_cycle(task: str, cycle: int) -> Result:
    """取题 → 变更 → 提交到 worktree → 返回候选。
    不测量、不判决、不写分数——那三件事归土壤。"""

def main(argv) -> int:
    # choices: cycle | reflect | report | status | selftest
```

**验收**：给定一个 task 和一个可写的 worktree，产出一个 commit。
**种子不写任何台账**——`kind:"cycle"` 记录由土壤 supervisor 从退出码 + commit + stdout + 文件系统状态生成。

#### `meristem/engine.py` —— 变更生成

```python
def propose(task: str, *, config, extra: str = "") -> Mutation
def apply(mutation: Mutation, workdir: pathlib.Path) -> list[str]
def _validate_paths(paths: dict, label: str) -> None
```

**不变量**：`_validate_paths` 拒绝以下全部，任一命中即拒：

```python
# 目录前缀给出默认权威级，但 seed/ 内部并非全部可写——必须文件级白名单。
SEED_WRITABLE = ("seed/constitution.md", "seed/agenda.md", "seed/narrative.md",
                 "seed/probe-proposals/", "body/organs/", "tests/")
SEED_READONLY = ("seed/model-interface.json",   # S7：种子不得改模型接口
                 "seed/feedback.json")          # T5：投影由土壤写
# 其余一律拒绝：root/ · substrate/ · soil/ · state/ · vault
# 写入前 realpath 校验 + O_NOFOLLOW；拒绝 symlink / hardlink 指向受保护文件
# （否则 seed/narrative.md 可被链到 soil/report-facts.json，白名单被穿透）
```

外加：`..`、绝对路径、**任何以 `.` 开头的路径段**（v3.1 的 `.pytest_cache` 混入教训）。

> **白名单，不是黑名单。** 「列举式边界必然漏」是本节自己写的话——
> v3.1 用黑名单，于是 `body/` 可写不可见、`state/*.jsonl` 可写无闸、`.pytest_cache` 混进上下文。
> **目录结构是唯一能把边界做成非列举式的地方**：新增任何土壤资产，默认就在保护面内，不需要有人记得去加一行。

> **单点检查不是边界。** 纵深四层：engine 拒绝 + 土壤版评审清单 + **substrate 独立路径守卫**（不信任种子自己的检查）+ 文件系统属主。
> v3.1 的教训：`body/` 可写不可见、`state/*.jsonl` 可写无闸——**列举式边界必然漏**。

**上下文预算**（I10 的 prompt 面）：`build_context` 返回的 token 数必须 ≤ `PROMPT_BUDGET`，超出则**在模型调用前**拒绝并记 `kind:"prompt_over_budget"`。
> v3.1 的 prompt 面 48k–55k 且**无人管理**，P-067 让它可无界增长。

#### `meristem/task.py` —— 选题

```python
def take_task(agenda: Path, facts: ReadOnlyFacts) -> str | None
def done_tasks(facts: ReadOnlyFacts) -> set[str]
def parked_tasks(facts: ReadOnlyFacts) -> set[str]

# ReadOnlyFacts：土壤渲染的只读投影。种子无任何台账写入接口。
```

#### `meristem/narrative.py` —— 叙事（不写成绩）

```python
def write_narrative(seed_dir: Path) -> None   # 只写 seed/narrative.md
def print_status(repo: Path) -> int           # 逐字满足 status 契约
# 渲染 REPORT.md 属于 soil/report_renderer.py —— 种子不写 REPORT.md（S8）
```

**status 输出必须包含**（土壤解析）：
```
core pressure: 0.42
open agenda item: <task text>   或   open agenda item: (none)
```

### 10.2 土壤新增模块规格

#### `substrate/probe_runner.py`（S3）

```python
def catalogue(vault: Path) -> list[dict] | None
def run_probe(manifest: dict, tree: Path) -> ProbeRun | None
    # score = passed / len(checks) * 100
def run_all(tree: Path, vault: Path) -> list[ProbeRun] | None
```

> **三个 `| None` 是契约的一部分，不是可选的防御性写法。**
> `None` 的含义统一为「这一次测不出来」，对应 §10 pipeline 的
> `if before is None or after is None → Outcome.UNMEASURED` 出口。
>
> **任何一把尺读不出来或跑不出来，整轮返回 None，不返回一份少一把尺的清单**——
> 后者会让 before 与 after 覆盖不同的探针集合，两边根本不可比，而 `fitness.pair()`
> 对此毫无察觉：它逐 `probe_id` 配对，缺席的那把只是「没配上」，不会报错。
>
> **判定原则而非穷举**：只要 manifest 不可信（读不到 / 非法 JSON / 不是对象 /
> 含 `entrypoint` / 必需字段缺失或类型不对），就走 `None`。**不要在实现里写
> 「以下三种情形」这类穷举断言**——v5.9 落地时写过一次，独立审查当场找出第四种，
> 而那种情形当时抛的是未捕获异常。**崩溃比少一把尺更糟：它连退化成 UNMEASURED
> 都做不到。**

`ProbeRun` 字段：`probe_id, score, checks_passed, checks_total, detail`，
**外加 §4.1 Measurement 身份的全部四项** `probe_manifest_sha, tree_sha, runner_version, execution_policy_version`。

> **`tree_sha` 是 v5.9 补的（落地时由 A1 上报）**：本节前一版漏了它，而 §8.2 的 records schema
> 要求 `tree_before` / `tree_after` 两个字段——`pair(before, after, commit)` 只收**一个** commit，
> **那两个字段填不出来**。§4.1 的 Measurement 身份本来就含 `tree_sha`，是本节漏抄。
> 补上之后 `pair()` 直接从 `before.tree_sha` / `after.tree_sha` 取值，无需额外入参。

> 三个版本维度**必须由 runner 在测量时刻产出**，不能事后补。
> 它们是 Measurement 身份的一部分（§4.1）；不在 `ProbeRun` 里，`fitness.pair()` 就无从比对，
> §4.1 那条强制机制会退回成一句声明。

**organ 入口与输出 ABI（v5.9 补，落地时由 advisor 复审发现）：**

| | |
|---|---|
| 入口路径 | 固定为 `body/organs/<organ>/run.py`（§8.1.1 已禁止种子自撰 `entrypoint`，这是唯一合法路径） |
| stdin | `{"input": "<text>"}` 的 JSON 序列化，一次性写入并关闭 |
| stdout | 恰好一个字符串值的 JSON 对象；**key 名不作要求**（不同 organ 可用不同的语义 key） |
| 失败判定 | 非零退出码，或 stdout 不满足上一行的形状 → `unmeasured`，**不是** 0 分（见 §15.6） |

> **为什么这处约定要写进规格，而不是留在 `probe_runner.py` 与各 organ 各自的
> docstring 里各记一份「应保持一致」**：这是 seed（organ 的作者）与 soil
> （`probe_runner.py` 的作者）之间**唯一的接缝**。两侧各写一份注释靠人记得同步，
> 正是 §17.7 点名的语义漂移温床——一侧改了约定而另一侧的注释没跟着改，不会有
> 任何机制报错，只会在某次探针运行时把全部 check 悄悄记成 `unmeasured`。规格是
> 唯一权威来源，消除「两份注释，该信哪份」的问题。

**不变量**：种子代码中不得出现 vault 路径常量；本模块是唯一读 vault 的地方。
`probe_manifest_sha` 必须等于该 probe 冻结登记里的 `frozen_probe_manifest_sha`（C1，**CA-9**）。

#### `substrate/fitness.py`（S5）

```python
def pair(before: list[ProbeRun], after: list[ProbeRun], commit: str) -> list[dict]
    # 产出符合 8.2 records[] schema 的记录，status 由 before/after 计算，非由任何人声明。
    #
    # **配对前先比对版本维度**（§4.1）：逐 probe_id 检查
    #   before.probe_manifest_sha       == after.probe_manifest_sha
    #   before.runner_version           == after.runner_version
    #   before.execution_policy_version == after.execution_policy_version
    # 任一不等 → status = "unmeasured"，delta 置 None，**不得产出 improved**。
    # 不可比就是不可比；把它算成一次进步，就是 proved_better_by 换了个藏身处。
def has_regression(records: list[dict]) -> bool
    # 任一 record 的 status == "regressed" 即为真。§10 pipeline 调它。
def degenerate_probes(ledger: Path, window: int) -> list[str]   # I3
```

> **`write()` 已删除（v5.9，落地时由 A1 上报）。** 本节前一版声明 `write(records, ledger)`，
> 但 §10 的 pipeline 伪码**从不调用它**——写台账走 `ctx.ledger.append({...})`，因为
> §8.2 的六个强制字段只有 `SoilContext` 拿得到。`write()` 按其签名**填不出那六个字段**，
> 谁调它谁就产出一条 schema 违规事件。
>
> 反过来，pipeline 调的 `has_regression()` 上一版**没有被声明**。一个声明了没人调、
> 一个被调了没声明——**正是 CA-6 双向性要防的那对症状，也是 `advance()` 零调用点的同一种病。**
> 台账的唯一写入路径是 pipeline，这与 C4「唯一权威台账，唯一写入者」一致。

#### `substrate/budget.py`（S7）

```python
def check(ledger: Path, cycle: int) -> str | None
    # 返回违规描述或 None；一切计数皆滚动窗口（I1）
```

**不变量**：本模块的任何计数都不得是全时段累计。新增计数须在土壤评审清单勾选 I1。

#### `substrate/pipeline.py`（S2+S4+S5，**v5 最大的一块新实现**）

v3.1 把 deterministic + probes + review 做在 `meristem.loop cycle` 内部。v5 把它们全部搬到土壤，于是需要一条候选处理流水线。**约 150–250 行，这是本次重建最大的新代码。**

```python
def process_candidate(
        commit: str,
        task: Task,
        *,
        repo: Path,
        panel: Panel,                 # controlled tests may pass manual_prompt;
                                      # formal H1 passes autonomous_panel
        ctx: SoilContext,             # ledger / vault / scoreboard / policy
                                      # + generation / soil_cycle / calibration（§8.2 强制字段之源）
) -> Outcome:
    """候选处理流水线。无隐式全局；每个非晋升出口都写 promotion_outcome。

    **只处理产生 Change 的任务。** 特殊任务在进入本函数之前就分流：
      refusal_with_reason → TaskDecision pipeline（无 commit / 无测量 / 无 canary）
      no_measurement      → 仅测回归 + 面板显式批准该豁免 + 计数器；无 accepted_fitness
    未获批准的 no_measurement 归入 unfulfilled，不另设出口。
    """

    with ctx.promotion_lock:                                  # 单写者
        # ── C3 第一次祖先检查（测量之前）
        if not check_ancestry(repo, commit):
            return finalize_nonpromotion(ctx, Outcome.STALE, None,
                                         "candidate.parent != HEAD", quota=False)

        parent        = git(repo, "rev-parse", commit + "^")
        parent_tree   = materialize_readonly_tree(repo, parent)
        candidate_tree= materialize_readonly_tree(repo, commit)

        before = probe_runner.run_all(parent_tree,    ctx.vault)   # S2
        after  = probe_runner.run_all(candidate_tree, ctx.vault)
        if before is None or after is None:
            return finalize_nonpromotion(ctx, Outcome.UNMEASURED, None,
                                         "measurement failed", quota=False)

        observed = fitness.pair(before, after, commit)              # S5
        oid = ctx.ledger.append({"kind": "observed_fitness", "records": observed,
                                 "commit": commit, "source": None,
                                 # ── §8.2 的六个强制字段，全部由土壤在此处写入 ──
                                 "task_id":       task.task_id,
                                 "primary_probe": task.primary_probe,   # 已校验必为 internal
                                 "generation":    ctx.generation,       # 与 C1 eligible_after 同名同义
                                 "soil_cycle":    ctx.soil_cycle,
                                 "calibration":   ctx.calibration,
                                 "counts_as_progress": False,           # 此刻尚未晋升
                                 "candidate_state": "measured",
                                 "promotion_state": "pending"})

        if fitness.has_regression(observed):
            return finalize_nonpromotion(ctx, Outcome.REGRESSED, oid,
                                         "regressed vs parent", quota=True)

        if not task_evaluator.evaluate(task, observed, ctx):        # C6：兑现声明
            return finalize_nonpromotion(ctx, Outcome.UNFULFILLED, oid,
                                         "declared outcome not met", quota=True)

        # 面板只收 diff + 任务声明。**不传 observed**——评审员看见 +20 分会锚定
        # 向批准，判决就被测量污染了。fitness 的核验由 task_evaluator 在判决之外完成。
        verdict = panel(commit, git_diff(repo, parent, commit), task)          # S4
        if not verdict.passed:
            return finalize_nonpromotion(ctx, Outcome.REJECTED, oid,
                                         verdict.reason, quota=True)

        if not canary(repo, commit):
            return finalize_nonpromotion(ctx, Outcome.CANARY_REJECT, oid,
                                         "canary failed", quota=True)

        # ── C3 第二次祖先检查（merge 之前）：测量与面板之间 HEAD 可能移动
        if not check_ancestry(repo, commit):
            return finalize_nonpromotion(ctx, Outcome.STALE, oid,
                                         "HEAD moved during judgement", quota=False)

        # 晋升是三步非原子操作：merge / scoreboard / accepted_fitness。
        # 任一步之间崩溃 → 主线已含候选而事实不完整。必须有恢复语义。
        ctx.ledger.append({"kind": "promotion_intent", "commit": commit,
                           "parent": parent, "source": oid, "state": "pending"})
        merge_ff(repo, commit)
        ctx.scoreboard.write(after, commit)                         # S2
        ctx.ledger.append({"kind": "accepted_fitness", "source": oid,
                           "commit": commit, "records": observed,
                           # ── §8.2 六个强制字段：§1.2 谓词靠它们自包含求值 ──
                           "task_id":       task.task_id,
                           "primary_probe": task.primary_probe,
                           "generation":    ctx.generation,
                           "soil_cycle":    ctx.soil_cycle,
                           "calibration":   False,      # 校准强制回滚，永不到达此处（§12.0.1）
                           "counts_as_progress": True})
        ctx.ledger.append({"kind": "promotion_committed", "commit": commit})
        return Outcome.PROMOTED


def reconcile_on_start(repo: Path, ctx: SoilContext) -> None:
    """supervisor 启动时必跑：有 promotion_intent 而无 promotion_committed 的，
    核对 main 是否已含该 commit —— 已含则补写 scoreboard/accepted_fitness，
    未含则标记 abandoned。无法判定 → soil_recovery。"""


# ctx.promotion_lock 必须是**跨进程**文件锁（heartbeat / manual-cycle / keeper
# 是不同进程），不得用 threading.Lock。


def check_ancestry(repo: Path, commit: str) -> bool:
    """candidate.parent == HEAD。属 substrate/pipeline.py 自身，锁内调用两次。"""
    return git(repo, "rev-parse", commit + "^") == git(repo, "rev-parse", "HEAD")


def finalize_nonpromotion(ctx, outcome, source, why, *, quota) -> Outcome:
    """唯一的非晋升出口。任何没有 accepted_fitness 的候选，都不得被统计为 improved。"""
    ctx.ledger.append({"kind": "promotion_outcome", "outcome": outcome.name,
                       "source": source, "why": why,
                       "counts_as_progress": False,
                       "counts_against_task_quota": quota})
    return outcome
```

**失败路径**（每一条都必须写台账，且彼此互不混淆——P-016 的教训）：

> **本轮勘误**：本表的列头此前写作 `kind`。但 v5.5 的 T3 把非晋升出口统一进
> `finalize_nonpromotion()` 之后，**这些值住在 `outcome` 字段里，`kind` 恒为 `promotion_outcome`**。
> 表头没跟着改——照表实现就会写出规格里不存在的 `kind:"regressed"`，
> 而 §1.2 的判据与 **CA-6** 都是按 `kind` 取值的。**改了实现不改表，就是 T3 自己留下的洞。**

| 情形 | `kind` | `outcome` | 是否计入任务的拒绝额度 |
|---|---|---|---|
| 探针回归 | `promotion_outcome` | `REGRESSED` | 是 |
| **未兑现声明的目标** | `promotion_outcome` | **`UNFULFILLED`** | **是**（语义失败，不是机制故障） |
| 面板判拒 | `promotion_outcome` | `REJECTED` | 是 |
| canary 失败 | `promotion_outcome` | `CANARY_REJECT` | 是 |
| 测量失败（探针跑不起来） | `promotion_outcome` | `UNMEASURED` | **否**（机制故障） |
| 候选已过时（非 HEAD 后代） | `promotion_outcome` | `STALE` | **否**（环境故障） |

> **`outcome` 取 `Outcome` 枚举的 `.name`（大写），与 `finalize_nonpromotion()` 的实现一致；
> `status` 取 I5 的小写枚举。两者是不同的东西，不得互相套用。**

**验收**：**六种**情形各构造一例，断言 `kind`（恒为 `promotion_outcome`）、`outcome`、
`counts_as_progress`（恒为 `False`）与额度计入正确。
**外加一条负断言**：六种情形均**不得**产生 `accepted_fitness`——否则 §1.2 的判据会把失败数成点火。

**`unfulfilled` 的语义（必须写死，否则会出现两个同义不同名的出口）**：
语义失败而非机制故障 → **计入拒绝额度** · 写 failure_history · 任务回到 `open` · 三次后 `parked` ·
**不产生 `accepted_fitness`**。**未获面板批准的 `no_measurement` 归入 `unfulfilled`，不另设出口。**

---

## 11. 验证（分模块验收判据）

| 模块 | 验收判据 | 红绿验证 |
|---|---|---|
| `probe_runner` | 一个 5 检查探针，通过 3 个 → score 恰为 60.0 | 通过数改为 2 → 40.0 |
| `fitness` | before 40 / after 60 → `status == "improved"`；40/40 → `no_regression`；分数下降 → `regressed`；无 before → `baseline` | **I5 五种状态各一例**（含下一行的 `unmeasured`） |
| **`fitness` 版本维度** | before/after 的 `runner_version` 不等（其余全同、分数 40→60）→ `status == "unmeasured"`、`delta is None` | **三个维度各构造一例失配**；三维全同时同样数据 → `improved`。**这一行是红绿的关键**：不做它，§4.1 的强制机制就只是注释 |
| `fitness` I3 | 连续 N 次只出 0/100 的探针 → 出现在 `degenerate_probes()` | 出现 3 档则不在 |
| `budget` I1 | 全时段计数在窗口外的调用不计入 | 构造窗口外数据验证 |
| `engine` 路径 | `.pytest_cache/x.py`、`root/panic.py`、`../x` 全部拒绝 | 逐一断言 |
| `engine` prompt 面 | 超预算时**不发生模型调用** | mock llm 断言零调用 |
| `loop` 脊柱 | 产出 commit；**台账记录由土壤生成，种子不写** | 断言种子无台账写入路径 |
| `status` 契约 | 输出逐字匹配 supervisor 的两个正则 | 用 supervisor 的解析代码反向测 |
| I9 | `substrate/` 不 import `meristem` | 全仓 grep 断言 |

---

## 12. 实施路线

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0-a 点火（检验 H1）** | 受控 preflight 先验证土壤闭环；正式运行使用 soil-owned `autonomous_panel`，不依赖人工输入。种子 6 模块 + 土壤 5 新模块 + 点火 organ + **两把探针**（internal 5 明检查 + anchor 5 暗检查） | **三圈内 `ignition-status` 计数 ≥ 1**（判据定义见 §1.2，此处只引用不复述） |
| **P0-b 可信化** | **判决 adapter 可替换**：`process_candidate` 的测量与状态结构不变；但 panel 的土壤 prompt、清单、槽位、run identity、超时重试、投票持久化、不可用评审员语义**都是 P0-b 的主体工程**（约 100–150 行土壤代码） | 每替换一项后仍能产出 improved |
| **P0-c 自持化（检验 H2）** | keeper / breaker / 预算窗口上线 | 无人值守一夜，早上 **`ignition-status` 计数 > 0 且在增长**（同一判据，§1.2） |
| **P1 生长（检验 H3）** | 允许种子自撰新尺、长新 organ | **仅当 P0-c 持续产出 improved 后开放** |

### 12.0 里程碑按「谁在驾驶」切分

| 阶段 | 谁在驾驶 | 检验 | 判据 |
|---|---|---|---|
| **P0-a 点火** | **土壤给任务，soil-autonomous panel 自动判决**（manual_prompt 仅受控测试） | H1：给定分级尺，本档位模型能沿梯度爬 | 三圈内 `ignition-status` ≥ 1（§1.2） |
| **P0-b 自主** | **种子自己选题 + 真 panel 上岗**（两者必须同时） | H1'：自主选题下 H1 仍成立 | **种子自选**的任务上 `ignition-status` ≥ 1（§1.2） |
| **P0-c 稳定** | 无人 | H2：全部闸门在位时 H1 仍成立 | 无人值守一夜，`ignition-status` > 0 且在增长（§1.2） |

> **三个阶段用的是同一个判据、同一条命令。** 若各写各的口径，H1→H1'→H2 的对比就不成立——
> **对比的前提是两边量的是同一个东西**（这正是 §13.4 拒绝把 v3.1 当对照组的理由）。

> **为什么 P0-b 的两件事必须同时上**：自主选题若没有真 panel 把关，就是无约束漂移。

**「重复失败率下降」不进 P0-c 判据**——那是 H3 级的学习信号，
放进出生判据会让一个健康但记忆尚浅的系统无法出生。

### 12.0.1 `manual-cycle --calibration`：装置对照组

**要回答的问题**：三圈内没出现 `improved`，是**土壤坏了**还是**种子弱**？

**做法**：人工给定一个**确定能提升**的 mutation（比如直接把分类器缺的三个域的词补上一个域）。

- 测不出 `improved` → **土壤坏了**（测量、配对、候选树传递有问题）
- 测得出，而模型做不到 → **种子能力问题**（走 H1 否证条款：换档位或改尺）

**但校准本身是一次绕过 Verdict 的变更**——它明确不该过 panel（它就是来测量测量系统的）。
不写死，这就是下一个「实验者的动作污染正式记录」。

**约束（缺一不可）：**

- 走专用入口 `manual-cycle --calibration`
- **`observed_fitness` 封套**带 `"calibration": true`（标在封套上，不标在 `records[]` 里——§8.2）；
  **`accepted_fitness` 的 `calibration` 恒为 `False`**——校准强制回滚、永不 merge，**结构上产不出该事件**（§10）
- **永不进记分板、永不计入 `improved` 统计**
- **测量后强制回滚树**

**结构性保证优先于标记**：校准强制回滚、**永不 merge**，因此它根本走不到 §10 pipeline 里
写 `accepted_fitness` 的那一步——**结构上就产不出点火事件**。
封套上的 `"calibration": true` 与 §1.2 谓词里的 `calibration is not True` 是**纵深的第二层**：
万一日后有人给校准开出一条 merge 的路，判据仍然挡得住。
**CA-7 断言台账中不存在 `calibration: true` 的 `accepted_fitness`**（§17.8）。

> 校准是对**仪器**做的事，不是对**系统**做的事。
> **实验者也不得叙述成绩，哪怕是为了校准**——这是 S8 的精神延伸到实验者自己身上。

### 12.0.2 `manual-cycle` / `--preflight` / `--autonomous`：受控脚手架与正式自主结构共用骨架

```
python -m substrate.supervisor manual-cycle --preflight  # 测量、反馈、禁止晋升
python -m substrate.supervisor manual-cycle --autonomous # 正式 H1 soil-owned autonomous panel
python -m substrate.supervisor manual-cycle --calibration # §12.0.1 装置对照组
python -m substrate.supervisor ignition-status             # §1.2 判据的唯一求值点
```

三种模式共用同一条 `process_candidate` 测量、canary、ancestry、事务和恢复路径：

- `--preflight` 使用固定拒绝 panel，只验证土壤能测量且不会晋升；`PREFLIGHT_GATED` 不计语义 quota。
- `--autonomous` 使用 `soil-autonomous` deterministic panel，不调用 `input()`，正式 H1 才允许产生 autonomous promotion transaction。
- `manual_prompt` 只用于受控测试适配器，不能作为正式 H1 authority。

**y/n 落在 Verdict 位置，不落在 merge 位置（v5.8，外部审计第 4 项）：**

| 错的读法 | 对的读法 |
|---|---|
| `manual-cycle` 是一条特殊晋升路径，人敲 y 就 merge | **`manual_prompt` 只是一个 Panel adapter** |
| 会绕过 `promotion_intent` / scoreboard / `accepted_fitness` / `promotion_committed` | `process_candidate` 一行不改，**晋升事务链完整** |

> **为什么这条必须写死。** 若 y/n 直接执行 merge，`reconcile_on_start` 在整个 P0-a 期间都无事可做——
> 而**崩溃恢复要到 P0-c 无人值守时才第一次被真正需要**，那时它从未被跑过一次。
> **P0-a 不只是在测种子，也是在测土壤自己的事务链；绕过它就等于没测。**

`Verdict` 携带 `authority ∈ {manual, panel, soil-autonomous}`。`manual` 是受控测试适配器，
`soil-autonomous` 是正式 H1 的土壤权限；所有 authority 都必须继续走完整晋升事务链。

#### `ignition-status`：判据的唯一求值点（§1.2）

土壤扫 `state/soil-ledger.jsonl`，对每条事件求 `is_ignition_event(ev)`——
**只读台账，不查 task registry、不做多跳追溯**。这一点在 §8.2 让事件自带 Task 身份之后才成立，
它的价值是：`ignition-status` 可在**任何一份台账副本上离线重放**，包括崩溃恢复后与事后审计时。

输出形如：

```
ignition events: 2   (criterion §1.2)
  soil_cycle 3  commit abc123  task task-7f2a  probe-classify-basic  40.0 → 60.0
  soil_cycle 5  commit def456  task task-7f2a  probe-classify-basic  60.0 → 80.0
excluded: 4 kind≠accepted_fitness · 1 calibration · 1 primary_probe（anchor 上升）
```

**`excluded` 的归因规则（必须定死，否则同一事件会有两种报法）**：
对每个未通过的事件，**按 §1.2 四个合取项的书写顺序求值，报第一个不满足的那一项**——

```
kind → calibration → counts_as_progress → primary_probe
```

**不是「所有不满足的项」，也不是任意一项。** 归因顺序不定死，两次运行的 `excluded` 行就可能
不一样；而这一行是拿来做处置判断的（H1 否证 vs 修土壤），**读数不稳定的仪表比没有仪表更坏**。

> **`excluded` 行不是调试信息，是判据的一部分。**
> 只打印计数，读者无从判断「0 次」是种子没爬起来、还是判据把真实提升挡在门外了——
> 这两种情形对应**完全相反**的处置（H1 否证条款 vs 修土壤）。**把没数进去的东西也说出来**，
> 是 S8「不得叙述成绩」的正面形态：**不许只报好消息，也不许只报一个数。**

> **唯一的区别是判决位上坐着人。**
> 于是 P0-b 的「逐项替换」有了精确含义：**把 `manual_prompt` 换成真 panel，其余代码一行不动**。
> **拆脚手架 = 换一个函数指针。**

### 12.0.3 内核任务从哪来（砍掉 core pressure 之后的空缺）

v3.1 靠 `core_pressure` 逼种子外化——那条上限已被砍掉（正确），但**砍掉之后没有东西替它逼种子看向自己的内核**。若不补，v5 会永远停在 H1：一个分类器优化器。

**补法**：把天然指向内核缺陷的三类事实写进土壤渲染的 fitness 区段，种子读得到：

| 事实 | 指向的内核缺陷 |
|---|---|
| `degenerate_suspected` 探针列表（I3） | 尺子疑似退化，需要重写量尺或改进能力 |
| `prompt_over_budget` 记录 | 引擎的上下文构造有问题 |
| `fault` 统计（按 kind 分组） | 机制在某处反复失败 |

种子读到「**我的引擎连续 N 次 prompt 超预算**」，提出内核任务是自然的下一步——**这是信息，不是指令**。

**通道（T5，此前缺失）**：这些事实的权威副本在 `soil/report-facts.json`（种子不可读）。
土壤另写一份**只读投影** `seed/feedback.json` 供种子读取——
否则「把事实反馈给种子」在 v5 里没有任何实际通道。

#### 投影的新鲜度契约（v5.8，外部审计 P1 第 5 项）

投影是台账的派生物，**而派生物会滞后**：土壤渲染中途崩溃，就会出现
「台账已有新事件，而 facts / feedback / REPORT 仍是旧版」。这不造成安全问题，
但会造成**种子读到滞后事实、报告与台账不一致**——**且没有任何东西会提醒你**。

每个投影必须携带来源指纹：

```json
{"source_ledger_tail_hash":"…","source_ledger_offset":1234,
 "generated_at":"…","schema_version":1,
 "facts":{ … }}
```

**启动时校验**：投影的 `source_ledger_tail_hash` 若不等于当前 `soil-ledger` 的 tail hash，
**土壤先重建投影，再放行 seed cycle**——是阻塞，不是警告。由 **CA-12** 断言（§17.8）。

> **为什么必须阻塞而不是警告。** 种子的下一步选题就建立在这些事实之上（这正是本节存在的全部意义）。
> 让它在滞后的事实上选题，等于派它去修一个已经不存在的问题——
> **而它还会为此写下一条 narrative，把滞后固化成叙事**，下一拍再从那条叙事出发。

### 12.1 为什么 P0-a 的手工评审不是人工门

> **人工门是「系统在等它」的东西；实验者的检查是「实验在等它」的东西。**

P0-a 阶段还没有一个自主系统——有的是一台正在被测试的装置。而且它**不与自动闸门共存**：P0-b 的定义就是逐项拆除它。**不会与被它替代之物共存的东西，是脚手架不是门。**

### 12.2 点火任务（P0-a 的具体内容）

- **organ**：`body/organs/classifier/` —— 把失败原因文本分到命名类别，初始正确率**刻意做成 40%**（5 个 check 过 2 个）
- **探针**：**两把**
  - `internal`：5 个已知 check，提供梯度，score = passed/5×100。**P0-a 的 `primary_probe` 固定为它**（§8.1.4）
  - `anchor`：**5 个种子不可见的隐藏 check**（**同一 Probe schema，满足 I2**），**只做 anti-overfit**
  - 缺了 anchor，40→60 可能只是「模型把那五条 case 硬编码对了」，而不是 classifier 真的变强
  - **anchor 不设 I2 豁免**：一旦两类 probe 有两套 schema，边界必然再次漂移
- **probe 必须在种子产出第一个 candidate 之前冻结**（C1）
- **任务**：「提高 classifier 在探针上的得分」
- **验收**：按 §1.2 的**唯一判据定义**求值（`primary_probe` = internal，40 → 60 或更高）

#### anchor 的作用是非对称的（必须写死，否则「anti-overfit」只是个形容词）

| 方向 | 处置 | 是否新增机制 |
|---|---|---|
| **anchor 回归** | 候选被拒（`regressed`，计入拒绝额度） | **否**——`run_all` 本就跑全套探针、`has_regression` 对任一 probe 触发（§10）。此处只是**写明既有行为** |
| **anchor 上升** | **不加分、不计入 `improved`、不进出生判据** | **是**——本轮新增的约束 |
| **internal 持续上升而 anchor 长期不动** | 记 `overfit_suspected`，进 `seed/feedback.json` 与 fitness 区段 | **否**——比照 I3 的 `degenerate_suspected`，同样「疑似 + 供人看见」，**不自动拒绝** |

> **为什么 `overfit_suspected` 只能是「疑似」**：合法原因确实存在——改动本就只针对 internal 覆盖的那个域、
> anchor 早已饱和、样本太少。**理由与 I3 逐字同构（§5），所以不建新机制，复用既有处理方式。**
>
> **为什么「上升不加分」必须单独写死**：anchor 的 case 种子不可见，它上升是**泛化的证据**，看着比 internal
> 上升更值得奖励——正因如此，不写死就一定会有人把它算进判据。**一旦算进去，anchor 就从外部锚变成了第二把可挑的尺，
> 它存在的全部理由随之消失。**

**这个任务与自我修改无关**，正因如此它是干净的装置测试——量的是**尺子会不会动**，不是种子该往哪走。

---

## 13. 清盘清单

### 13.1 删除

```
meristem/            2999 行   全部
body/organs/         3619 行   全部 11 个 organ
tests/               4013 行   全部
control/  整个目录（其职责已拆入 seed/ 与 soil/）
state/*                        全部（已归档）
```

### 13.2 保留

```
root/                133 行   原样
substrate/supervisor.py 1255 行  改 3 处（见 13.3）
soil/model-policy.toml        土壤私有（由 v3.1 models.toml 拆出）
seed/model-interface.json     种子只读，不含配额数字
（control/models.toml 归档，不保留）
/RSI/run_meristem.sh          原样
/RSI/meristem-env             原样
/RSI/meristem-vault/          清空 internal/active，保留目录结构与 anchors
```

### 13.3 substrate 完整依赖清单

> **初稿说「只改三处」是错的。** 那六处 CLI 接缝只证明了 supervisor **如何启动**种子，
> 没有证明 **supervisor 内部的状态与晋升逻辑仍与新种子兼容**。这是两件事。

**A. 语法级（不改就报错）**

| 位置 | 现状 | 改为 |
|---|---|---|
| `:459-467` | `python -c "from meristem.gates import probes"` | 调 `substrate/probe_runner.py`（S3） |
| `:584` | canary 跑 `meristem.loop selftest` | 保留（新种子须实现） |
| `:585` | canary 跑 `unittest discover -s tests` | 指向新 tests/ |

**B. 语义级（不改不报错，但会静默失真）**

| 位置 | 现状 | 风险 |
|---|---|---|
| `:80-82` | 硬编码 `"meristem/gates/deterministic.py"` + 正则 | **常量不会提醒你去更新它**；受控路径判断悄悄失真 |
| `:379-383` | 镜像 `LIFECYCLE_STAGES`（6 段） | 新种子若换阶段命名，合法跃迁会被误判为跳级 |

**C. 状态与流程级（v5 最大的失配面）**

| 依赖 | 用途 | v5 处置 |
|---|---|---|
| `STATE_FILES`：`control/agenda.md`、`state/{proposals,mailbox,gaps,patterns,backlog}.md` | `_commit_state()` 每拍提交 | 清单须按 v5 的登记册重列 |
| `_auto_promote()` | 从 proposals 搬到 agenda | v5 的选题流程若不同则须重写 |
| **`promote_probes()` 的 `PROBE_STAGING`** | 读 `state/probe-proposals`（v3.1 路径） | **必改**：v5 §10.1 把提案面定在 `seed/probe-proposals/`，**CA-5 扫的正是后者**。两个路径对不上意味着 CA-5 断言的目录与冻结流程实际读的目录不是同一个 —— **一条扫着空处的断言**。且 `promote_probes()` 里没有 `entrypoint` 字段检查，所以 §8.1.1 的拒绝目前**只有 `probe_runner.catalogue()` 一道在跑**，不是纵深两层 |
| cap case 相关逻辑 | v3.1 的上限治理 | **v5 无 LOC 闸门，整段删除** |
| `guard_lifecycle()` | 六段生命周期守卫 | v5 两状态，重写或删除 |
| 失败聚合 / journal event kind | v3.1 的事件名 | v5 的 `kind` 集合须逐一对照 |
| `body/organs` 路径假设 | organ 概念 | v5 保留 organ 但 schema 不同 |
| `MERISTEM_MODEL_GATEWAY` 环境变量注入（v5.9 补，落地时由 advisor 复审发现） | supervisor 起 `python -m meristem.loop cycle` 子进程时，种子经 `meristem/llm.py` 的每次模型调用都靠它转发到网关 | **必须在起子进程前注入该变量。** 缺失不会报错、不会崩溃——`call_model` 只是 fail-closed 为 `refused`（`reason: gateway_not_injected`），把种子的每次调用静默判成拒绝；一个不存在的网关与一个永远拒绝的网关，在种子眼里长得完全一样 |

**D. 新增（v5 的核心新实现）**

| 模块 | 行数估算 |
|---|---|
| `substrate/pipeline.py` | 150–250 |
| `substrate/probe_runner.py` | ~90 |
| `substrate/fitness.py` | ~70 |
| `substrate/budget.py` | ~60 |
| `substrate/panel.py`（P0-b 主体工程：prompt/清单/槽位/run identity/超时重试/投票持久化/不可用评审员语义） | 100–150 |

> **结论：不是「保留 1255 行改三处」，是「保留骨架、重写判决回路、逐项对照状态语义」。**

### 13.4 归档（已完成并验证）

```
/RSI/meristem-v3-archive/   3.3M
  meristem-v3-full-history.bundle   "records a complete history"（--all）
  state/journal.jsonl      1821 行   scoreboard 265   decisions 97
  vault/internal/active    15 把
  control/  REPORT.md
```

**v3.1 是基线叙事，不是对照组。**

> 把 v3.1 称作对照组是不诚实的：**它的尺是二元的，结构上不可能产出 `improved`**。
> 两组用不同的量尺，比较无意义。

- **v3.1 的作用**：展示旧设计为何测不到进步（400 拍、37 次晋升、A 类 0 次）
- **v5 真正的对照**：**自身的消融**——H2 阶段每加一道闸门前后的 `improved` 速率对比

---

## 14. 与 v3.1 的差异总表

| 维度 | v3.1 | v5 |
|---|---|---|
| 土壤定义 | 否定式（不许碰的一切） | **肯定式（种子不得自撰、否则可伪造进步的一组东西）** |
| 出生 P0 | 完整闸门栈（2999 行） | **点火（~415 行，无闸门）** |
| 出生判据 | 闸门齐备 | **三圈内分数上升一次** |
| 探针 | 二元断言（实测 {0,80,100}） | **score = passed/K×100，K≥5** |
| 分数写入 | 内核逐拍抽样 | **土壤晋升时写全套** |
| fitness 断言 | 内核撰写（166 次假声明） | **土壤计算，种子不产出** |
| 预算 | 内核，全时段累计（致死锁） | **土壤，一切滚动窗口** |
| 体积闸门 | 内核 LOC ≤ 3000（硬闸） | **三个理解预算：闭包 / prompt 面 / 契约面** |
| organ 生命周期 | 六段（`advance()` 零调用） | **两状态 + `degenerate_suspected` 标记** |
| 操作员 | 系统之外，零闸门 | **系统之内，走同一条通道** |

---

---

## 15. 开工前的五个硬契约（第三轮审查产出）

> 前两轮审查修的是**结构**；这一轮修的是**契约的时机与归属**。
> 五条全部落成之后才可开工。**此后不再扩规格——剩下的问题只有开工才能暴露。**

### C1. Probe 冻结契约

**攻击**：同一个 candidate 里「先写尺、再写刚好通过这把尺的能力」。**不需要任意代码执行**，`improved` 就已失去实验意义。

| 规则 | |
|---|---|
| **probe 的创建/冻结与针对它的能力变更，不得在同一个 Change 内完成** | 必须隔至少一个独立 cycle |
| **新 probe 的第一次分数只能是 `baseline`** | 永不产生 `improved` |
| **任何修改 active probe 的 Change** | 不得获得能力 fitness credit；必须新建 `probe_id` 或退回 `draft` |

**冻结登记（土壤持有，缺一项则无法判定是否真的隔离过）：**

```json
{"probe_id":"…","status":"active","created_by":"seed",
 "proposed_commit":"…","frozen_at":"…","frozen_tree_sha":"…",
 "frozen_probe_manifest_sha":"…",
 "eligible_after":{"generation":"g0","soil_cycle":123}}
```

**同一 `probe_id` 的 `frozen_probe_manifest_sha` 不得变化**——否则种子可以保持 id 不变、
偷换 vault 内容。改 active probe 必须**新建 `probe_id`**。

**且它必须等于该 probe 每一次 Measurement 的 `probe_manifest_sha`**（§4.1 / §8.3）。
不断言这条相等，「冻结」就只是登记册上的一句话，实际跑的可以是另一份 manifest——**CA-9**（§17.8）。

**流程**：`种子提案 → 土壤校验 schema → 冻结（搭拍首 bookkeeping）→ 至少一个独立 cycle → 种子才可针对它提能力变更`

> **交互修正（契约 1 × 契约 3）**：冻结若单独移动 HEAD，所有在旧 HEAD 上开工的候选立刻变 stale。
> **冻结 commit 视为 bookkeeping，搭拍首提交的车**（同 P-083 的登记册轮转），不单独动 HEAD。

### C2. Accepted fitness 契约

**问题**：初稿在 panel 之前就 `fitness.write()`。一个被面板拒掉的候选，台账里已留着 `status: improved`。

**两个概念必须分开：**

| | 含义 | 用途 |
|---|---|---|
| `observed_fitness` | 候选树相对**父树**的行为变化 | 反馈给种子（它需要知道自己的改动有没有效果） |
| `accepted_fitness` | 通过 panel + canary + 真正进入 main | **统计、报告、H1/H2 判据只数这个** |

**两阶段事件，不是一个字段：**

```
observed_fitness   测量后立刻写，promotion_state: pending
       ↓
accepted_fitness   仅在 merge 成功后追加，counts_as_progress: true
promotion_outcome  任何未晋升的结局，counts_as_progress: false
```

> **报告、pressure、H1/H2 判据只统计 `accepted_fitness`。**
> 只在文字上区分 observed 与 accepted 不够——**必须是两条不同 kind 的记录**，
> 否则报告从台账里 `status == improved` 一扫，被拒的候选照样污染统计。

> **交互修正（契约 2 × P0-a 判据）**：P0-a 是手工判决，「promoted」= 实验者敲了 y——
> 但**仍须走完整条 `process_candidate`，仍写 `accepted_fitness`**。
> 判据本身见 §1.2（唯一定义点）：它数的是 `accepted`，不是 `observed`；
> 否则 P0-a 与 P0-c 统计的不是同一种东西，H1→H2 的对比失效。

**两个判据，地位不同，不可混：**

| 判据 | 内容 | 性质 |
|---|---|---|
| **P0-a ignition** | `ignition-status` ≥ 1（谓词定义见 **§1.2**，此处不复述） | **通过即进 P0-b** |
| **P0-a gradient evidence** | ≥2 个独立 candidate 产生多步爬升（40→60→80 或等价），每一步各自满足 §1.2 谓词 | **记录性判据**，P0-b 期间持续观察 |

> 一次性 40→100 不等于沿梯度爬坡——它可能是一次整体重写。
> 但**不把 gradient evidence 做成硬门槛**：要求三圈里出两次独立提升，对免费档模型可能把 H1 误判为否证。

### C3. Candidate 祖先契约

```python
current_head    = resolve("HEAD")
candidate_parent = resolve(commit + "^")
if candidate_parent != current_head:
    # 经唯一非晋升出口，不自写台账（T3）：kind = "promotion_outcome"，outcome = "STALE"
    return finalize_nonpromotion(ctx, Outcome.STALE, source, "candidate.parent != HEAD",
                                 quota=False)
```

**测量前查一次，merge 前再查一次**——测量与 panel 之间 HEAD 可能移动。
更强的形态是**单写者晋升锁**；最低要求是查两次。

> 这是 P-074（cycle 384 弄死一个 2/2 全票候选）的推广。**初稿只取了 `commit^`，没有断言它等于 HEAD。**

### C4. 事件归属契约

> **种子完全不写台账。** 土壤从退出码 + commit + stdout + 文件系统状态生成 cycle 记录。

不是双文件。三条理由全部来自本实验：

1. 种子可写的事件流会与 `seed/narrative.md` **给同一个东西两个家**，是语义漂移的温床
2. 共享写入面永远被逐渐扩权——**v3.1 的 journal** 起初也「只是记录」，后来 `proved_better_by` 就住在里面
3. **种子要传给土壤的一切都已有更硬的载体**：Task 声明是文件、拒绝是 `TaskDecision`、叙事是 narrative 文件。**台账里没有任何一种记录必须由种子亲手写。**

**唯一权威台账 `state/soil-ledger.jsonl`，唯一写入者：土壤。**

### C5. 受保护土壤面契约

见 §10.1 的 `PROTECTED_SEED_PATHS`。**纵深四层，不依赖单点检查。**

> **交互修正（契约 5 × `soil_recovery`）**：recovery 的作用域初稿写「仅限 `substrate/`」，
> 而 model-policy 在 `soil/`。**本次实验的 `campaign_calls` 死锁正是 config 层的故障**——
> 按字面授权，recovery 够不着它。**作用域改为 `substrate/` + `soil/`。**

---

## 15.6 C6. 不可信执行契约（本轮最严重的新洞）

**上一轮禁掉了种子写 `check.py`，但 `probe_runner` 要执行 `body/organs/classifier/` —— 那同样是种子可变区的 Python。**
同一条穿透路径，藏在「能力执行」这个合理名义底下，比 `check.py` 更隐蔽。它能读 `SENSENOVA_API_KEY`、
写 soil-ledger、**读 vault 里的隐藏 cases**、联网、fork、卡死 runner、伪造输出。
`canary(commit)` 执行候选代码，同理。

> **任何种子拥有的代码，都不得直接运行在土壤权威进程内。**

**分两档，档位边界写死：**

| 档 | 内容 | 何时必须有 | 理由 |
|---|---|---|---|
| **最小完整性隔离** | subprocess · stdin/stdout 严格 JSON ABI · **无 secrets** · **vault 不可读** · **ledger 不可写** · **worker 网络策略由实验配置显式声明** · **墙钟超时** · 非法输出 → `unmeasured` | **P0-a** | P0-a 可选择保留网络口做能力实验，但不得把该运行宣称为“禁网完整隔离” |
| **资源强化** | 只读挂载 · CPU/内存上限 · 进程数限制 · seccomp / namespace · **禁网执行** · 更强 filesystem policy | **P0-b** | 工程量大，防的是自主运行下的资源滥用与外传 |

> **两档边界经第四轮审查重切，现按实验决策修订。** P0-a 的最小完整性隔离仍必须保护
> soil 权威状态、vault、凭据和输出 ABI；网络是否关闭不再由这张表隐含决定，而由每次实验的
> execution policy 显式声明。保留网络口的实验必须把它记录为已接受风险，不得宣称禁网闭环。
>
> **「不继承环境变量」≠「读不到 secrets」，「subprocess」≠「隔离」**：同 UID 下可读
> `/proc/<pid>/environ`、可用绝对路径读 vault、可直接联网。当前实验通过 soil/worker UID、
> 过滤 filesystem、credential 不注入 worker、Unix socket ABI 和 soil-side recovery 保护权威边界；
> 禁网与资源围栏留在 P0-b 的强化执行策略。

#### 执行身份模型（必须写明，不得只写「文件系统属主」）

| 主体 | 身份 | 权限 |
|---|---|---|
| soil supervisor | `soil` UID | 全部土壤资产 |
| organ / rubric worker | **`worker` UID**，无附加组 | 只能读写自己的隔离工作副本 |
| vault | `soil` 属主，`0500` | worker 不可读 |
| **`state/soil-*.jsonl`（整族，§8.1.5）** | `soil` 属主，`0600` | worker 不可读不可写；只经土壤进程追加 |

> **属主授予的是前缀族，不是文件名。** 上一版这一行写的是 `soil-ledger` 一个文件——
> 那样新增 `state/soil-scoreboard.jsonl` 就落在保护面之外，而**没有任何东西会提醒你**。
> 按族授予之后，「新增一个土壤台账」这个动作默认就是安全的。

**若实验环境无法真正拆 UID**，必须在此明写：**用什么可验证的替代方案、哪些隔离只是 best effort、
哪些攻击不在实验范围内**。否则「文件系统属主」只是设计愿望，不是实现契约——**这正是本项目
「声明未断言」这一家族病在安全层的形态**。

#### Anchor rubric 同样受执行契约约束

> 「作者是人」≠「执行过程可无限信任」。人写的 anchor rubric 同样可能因 bug 误写 ledger、
> 卡死 supervisor、泄露其他 anchor。anchor rubric 执行时：独立 worker · 只能读它自己的 rubric ·
> 不可读其他 anchor · 不可写 ledger · **网络策略按 execution policy 执行（本轮保留网络口时须记录风险）** · 超时。

**「非法输出 → `unmeasured` 而非 0 分」为什么属于 P0-a 档**：
一个 crash 的 organ 若记 0 分，**后续把它修好会读成一次 `improved`——凭空制造出点火判据要的那个事件**。

**验收**：两档各有判据。接口形状档——构造一个尝试读环境变量的 organ，断言它读不到且记 `unmeasured`；
P0-a 的网络状态必须从 execution policy 读取并进入实验记录；资源强化档——构造一个死循环 organ、一个联网 organ、一个写 ledger 的 organ，三者全部被拦。

---

## 16. 权威矩阵（规格的完整性验收工具）

> **读 / 写 / 执行三权分开，矩阵才完整**——anchor rubric 土壤可写，但**执行**它的是 `probe_runner` 而不是 panel。

| 数据 / 能力 | Seed 读 | Seed 写 | Soil 读 | Soil 写 | Soil 执行 | Root 写 | 进报告 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| active probe cases | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | 摘要 |
| internal probe **提案文件**（`seed/`） | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ |
| internal probe **冻结登记**（土壤） | 摘要 | ✗ | ✓ | ✓ | — | ✗ | ✓ |
| vault **frozen manifest** | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ |
| anchor rubric | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ |
| fitness | 只读摘要 | ✗ | ✓ | ✓ | — | ✗ | ✓ |
| soil model policy | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ |
| model interface | ✓ | ✗ | ✓ | ✓ | — | ✗ | ✗ |
| task 提案 | ✓ | ✓ | ✓ | ✗ | — | ✗ | 方向 |
| TaskDecision | ✓ | ✓ | ✓ | 裁决 | — | ✗ | ✓ |
| promotion verdict | 摘要 | ✗ | ✓ | ✓ | ✓ | ✗ | ✓ |
| **observed fitness → 面板** | — | — | — | — | **✗ 不传**（锚定风险） | — | — |
| soil-ledger（`state/soil-ledger.jsonl`） | ✗ | ✗ | ✓ | ✓ | — | ✗ | ✓ |
| **scoreboard（`state/soil-scoreboard.jsonl`）** | **摘要** | **✗** | **✓** | **✓** | **—** | **✗** | **✓** |
| seed narrative | ✓ | ✓ | ✓ | ✗ | — | ✗ | ✓ |
| **`seed/feedback.json`** | ✓ | ✗ | ✓ | ✓ | — | ✗ | ✓ |
| report facts（soil/ 权威副本） | **✗** | ✗ | ✓ | ✓ | ✓ | ✗ | ✓ |
| recovery manifest | ✗ | ✗ | ✓ | ✗ | ✗ | ✓ | ✓ |

**用法**：新增任何数据或能力，必须在此表添一行。**填不出某一格，说明归属没想清楚，不得开工。**

---

## 17. 补充实体与度量

### 17.1 `TaskDecision`（第四个一等实体）

**拒绝不产生 Change**：没有候选 commit、没有 before/after、退出码语义不明、面板批准的是「一条决定」而不是「一个变更」。**不要硬塞进 Change 状态机。**

```json
{"task_id":"…","decision":"refuse","reason":"…",
 "expected":"refusal_with_reason","decided_by":"seed",
 "judged_by":"soil-panel","status":"accepted"}
```

```
proposed ──seed 拒绝并说明理由──> decided ──panel──> accepted / overruled
```

`accepted` → task 计 done，**不计入 improved**，理由进 failure_history 供后续任务参考。

### 17.2 Metric Registry（`cost_reduction` 的落地）

`cost_reduction` 若只写「`prompt_tokens_per_success` 下降」，它会变回一句语义声明。**必须回答**：谁计算、数据从哪来、分母是什么、窗口多长、种子能否自定义、schema 是否土壤固定。

| 字段 | 值 |
|---|---|
| 定义者 | **土壤**（种子只能从注册表里选，不能自定义） |
| 计算者 | 土壤 |
| 数据源 | `soil-ledger.jsonl` |
| 窗口 | 滚动 N 个 cycle（遵守 I1：一切预算皆流量） |

**P0 注册表**（每一项含成功阈值与最小样本，否则「什么叫成功」仍是语义声明）：

| metric | 成功判据 | 最小样本 | 窗口 |
|---|---|---|---|
| `prompt_tokens_per_success` | 窗口均值下降 ≥ 10% | 5 次成功 | 12 cycle |
| `calls_per_promotion` | 同上 | 3 次晋升 | 12 cycle |
| `faults_per_cycle` | 窗口均值下降 ≥ 20% | 12 cycle | 12 cycle |

### 17.3 `no_measurement` 的护栏（防逃生舱）

若完全由种子声明，**任何难以证明的任务都会变成 `no_measurement`**。

- 必须**经面板明确批准**
- **不计入 `improved`，不计入 H1/H2 成功**
- **具体阈值（土壤策略，种子不可改）**：

```toml
max_consecutive_no_measurement = 2
max_no_measurement_ratio       = 0.25
window_cycles                  = 12
```

- 使用率进 fitness 区段供种子看见；连续超阈值 → 进入 review

> **没有 N 就没有机制。** 「不得连续超过 N 次」而不写 N，是又一句声明未断言。

### 17.4 I10 的具体数据结构（每个候选必须产出）

```json
{"closure_budget":{"files":12,"tokens":28000,"fits":true},
 "prompt_budget":{"tokens":42000,"fits":true},
 "contract_budget":{"changed_contracts":2,"review_surface":5,"fits":true}}
```

> v3.1 最大的毛病之一是「声明过多、断言过少」。**三个预算若只有自然语言描述而无数据结构，最后必然只实现其中一个。**

---

## 17.5 规格自验收（开工前必跑）

| 断言 | 命令 | 机械化后 |
|---|---|---|
| 全文无 `journal` 残留（v3.1 历史引用除外） | `grep -n 'journal' docs/MERISTEM-V5-SPEC.md`，人工核对每处均为历史引用 | **SA-1** |
| 权威矩阵每一行的七格都已填 | 人工核对 §16 | **SA-3** |
| 每个新增数据/能力在矩阵中有行 | 新增时强制 | **SA-3**，另见 §17.8.3 的已知缺口 |

> **本节自 v5.7 起是 §17.8 的人工前身。** 开工前仍需跑一次（彼时 CI 尚未接入）；
> **CI 接入之后权威在 §17.8**，本节只作为「开工第 0 步之前」的一次性核对保留。

> **留一个旧词就是留一个语义漂移的种子。** v3.1 的 `campaign` 一词教过这课——
> 它在 `cycle_calls` 邻位意味着护栏，在 `calls()` 实现里意味着寿命，**同一个词两个意思，代价是循环死锁**。

---

## 17.6 第四轮审查待办（**全部已落进正文，v5.5**）

> 上一版只写了判定、未写进对应小节，并明标了这一点。**不写进正文就等于没有**——
> 本版已全部落地，条目留作追溯，不删除。

| # | 待办 | 落点 |
|---|---|---|
| ~~T1~~ | ~~pipeline 上下文~~ **已完成** | — |
| ~~T2~~ | ~~二次 ancestry + 锁~~ **已完成** | — |
| ~~T3~~ | ~~统一非晋升出口~~ **已完成** | — |
| ~~T4~~ | ~~UNFULFILLED 入状态机~~ **已完成** | — |
| ~~T5~~ | ~~feedback 投影~~ **已完成** | — |
| ~~T6~~ | ~~§13.1 清盘清单旧路径~~ **已完成** | — |

---

## 17.7 一致性自检必须机械化（本文档的元教训）

四轮审查，**每一轮对方最大的发现都是本文档的内部矛盾**：改了 §8 不改 §10、改了目录不改清盘清单、
加了 S8 不改「七个模块」、禁了种子写 REPORT 而 API 仍写 REPORT。

**根因不是疏忽，是流程**：用「记得」代替机械扫描。

**规则**：任何改名 / 移动 / 新增之后，必须跑一遍全量引用扫描——

**受管名字分两类，检查方式不同：**

```bash
SPEC=docs/MERISTEM-V5-SPEC.md

# ① 现役名字：出现处必须彼此一致
for w in scoreboard 记分板 primary_probe probe_manifest_sha ignition-status \
         authority-matrix overfit_suspected accepted_fitness observed_fitness \
         calibration degenerate soil-ledger \
         task_id soil_cycle generation authority SECURITY_ASSURANCE \
         source_ledger_tail_hash counts_as_progress; do
    echo "== $w"; grep -n "$w" "$SPEC"; done

# ② 已退役名字：除下列三类豁免外，命中数必须为 0
#    (a) §18 勘误行
#    (b) 显式的「本轮勘误」注记
#    (c) **前版历史引用**——「初稿把…」「v5.7 的 X 是笔误」这类叙述
#        本文档是一份讲「为什么不再这样做」的文档,谈论旧名字是它的正当职能。
#        与 SA-1 对 `journal` 的处理**同一依据、同一纪律**：白名单逐条人工核准,
#        新增一条要说明它为什么是历史叙述而不是漏改。
for w in journal control/ report.py probe.json "四 新模块" \
         vault_manifest_sha frozen_vault_manifest_sha '"kind":"fitness"' \
         'panel=None' 'is_ignition_event(ev, task)' 'ev.get('; do
    echo "== RETIRED $w"; grep -n "$w" "$SPEC"; done
```

> **区分现役与退役，是本轮才补上的。** 上一版只有一张混合词表，于是「改名」与「保持一致」
> 用同一种检查——**而这两者的正确结果相反**：现役名字命中越多越好（说明交叉引用完整），
> 退役名字命中一次就是一个 bug。**同一个 grep 读出两种相反的结论，等于没读。**

**每引入一个新名字，就往 ① 加一行；每退役一个名字，就把它从 ① 移到 ②。**
自验收节（§17.5）不是查一个词，是查**每一次改名**。机械化形态见 **SA-4**（§17.8）。

### 纸面迭代终止条款

**本规格自 v5.6-final 起停止正文迭代。** 停笔依据是收敛曲线：本轮机械扫描抓到的陈旧文本（5 处）
已多于外部审查抓到的新洞（1.5 个）——**缺陷密度已低于审查流程自身的注入率**（「尚未落进正文」
那句话就是上一轮改动引入的）。继续打磨只会引入更多陈旧文本。

**此后任何新发现走「开工后以代码评审修」**，正文不改，只允许在 §18 变更记录追加勘误行。

#### v5.7 对本条款的处置（不是推翻，是收尾 + 换执行机制）

**v5.6-final 宣布冻结的时候，自己还挂着 5 项未落的交接项**——那些不是条款所说的「新发现」，
是宣布冻结的那一轮自己的欠账。**带着欠账的冻结不是冻结，是又一句「声明了没断言」。**

v5.7 只做两件事：

1. **关闭那 5 项**（scoreboard 归属 · Fitness 身份 · `primary_probe` · 出生判据 · 断言集）
2. **把冻结从纸面承诺换成机制**——§17.8 的 SA/CA 断言集随代码一起跑

**条款本身一字不改，自 v5.7-frozen 起继续生效**：此后的新发现仍走代码评审 + §18 勘误行。
唯一的区别是——**上一版靠「记得遵守」，这一版靠 CI 在你不记得的时候报错。**

> 这正是本文档反复对种子讲的那句话，终于用到了它自己身上：**「全部由机制强制，不靠提示词」**（§5 抬头）。
> **一份要求别人机制化的规格，自己却靠自觉冻结——那是这个项目的家族病最后的藏身处。**

#### v5.8：冻结第二次被打开，以及这件事说明了什么

v5.7 冻结之后，**外部独立审计判定了一项 P0**：`ignition-status` 只扫台账，
而 `accepted_fitness` 不带 Task 身份——**出生判据不可自包含求值、不可重放**。

**冻结条款照常成立，因为它挡的是打磨，不挡 P0。** 条款原文说的是「新发现走**代码评审**修」，
而这一项的性质决定了它留不到代码评审：**判据是 P0-a 的唯一出口；它若不可重放，
写出来的代码就没有可对照的判据。** 纸上是这一项唯一修得动的地方。

**判别标准，写死以免下次靠感觉：**

| 该开正文 | 该走 §18 勘误行 |
|---|---|
| 判据 / schema / 权威归属**不可实现或不可验证** | 措辞、举例、交叉引用、可读性 |
| **实现者照抄会写出错的东西** | 实现者照抄能得到对的东西，只是读着别扭 |

> **两次打开冻结，两次都是同一个形态：宣布冻结时自己还带着债。**
> v5.6 带着 5 项交接项，v5.7 带着这项 P0。
> **真正的结论不是「不该冻结」，而是「冻结不该由作者自己宣布」**——
> 这与 S4「判决归土壤」、S8「种子不得叙述成绩」是同一条原则：**作者不该给自己的规格判合格。**
> **v5.8 的冻结由外部审计裁定，不由本文档自称。**

---

## 17.8 一致性断言集（§17.5 与 §16 的机械化落地）

> **本节是 v5.7 唯一的新增机制，也是「纸面迭代终止条款」得以成立的前提。**
> §17.5 的自验收是**开工前人工跑一次**；§17.7 喊了「一致性自检必须机械化」，却只给出一个
> 一次性 shell 循环。两者都会随时间失效——**因为它们不随代码一起跑**。本节把它们转成 CI 断言。

### 17.8.0 前置：规格必须进入仓库

断言的权威源之一是**本文档**。它此前住在 `/RSI/MERISTEM-V5-SPEC.md`——**一个不受版本控制的位置**：
CI 无处附着，改动无从追溯，误删无从恢复。

**P0-a 开工第 0 步**：本文档迁入 `Meristem/docs/MERISTEM-V5-SPEC.md`，随代码一同版本化。
**迁入后 `/RSI/` 下不得保留副本**——同一个东西两个家，正是 C4 裁定过的那种漂移温床。

### 17.8.1 权威矩阵的机器可读化（现状 → 迁移 → 目标）

| 阶段 | 权威源 | 状态 |
|---|---|---|
| **现状**（截至本版） | **§16 的 Markdown 表格** | **唯一权威**。`soil/authority-matrix.json` **尚不存在** |
| **迁移**（开工第 0 步，与 §17.8.0 同批） | 逐行转录为 `soil/authority-matrix.json` | 转录后人工核对一次行数与逐格内容 |
| **目标**（转录完成后） | `soil/authority-matrix.json` | §16 表格降为**渲染产物**，由 SA-2 断言二者一致 |

> **为什么权威要从 Markdown 移到 JSON**：CA-1 / CA-2 要拿矩阵去比对真实文件权限与
> `SEED_WRITABLE` 白名单。**权威源若是 Markdown 表格，断言就得解析 Markdown**——脆，而且
> 下一次表格微调就会静默失效。
>
> **这与 REPORT.md 是同一条模式，不是新概念**：S8 已经裁定 `REPORT.md = renderer(facts, narrative)`。
> 矩阵同理：**§16 表格 = renderer(authority-matrix.json)**。复用规格已有的处理方式。

### 17.8.2 断言集

**与 §11 的划界**：§11 是模块单测（**一个模块的行为对不对**）；本节只收一致性与权威断言
（**多个地方说的是不是同一件事**）。两者不重叠，也不互相替代。

**SA-x：规格侧**（权威源 = 本文档；`docs/` 变更时跑）

| ID | 断言 | 防的是什么 |
|---|---|---|
| **SA-1** | 全文无 `journal` 一词，白名单中的 v3.1 历史引用除外 | §17.5 原条目；`campaign` 式语义漂移 |
| **SA-2** | §16 表格与 `soil/authority-matrix.json` 逐格一致 | 矩阵有两个家 |
| **SA-3** | §16 每行七格全部非空（无空格、无缺列） | §17.5 原条目：「**填不出某一格 = 归属没想清楚**」 |
| **SA-4** | §17.7 扫描词表中的每个受管名字，全文出现处均已登记 | 改名漏改交叉引用——**五轮审查每一轮的最大发现** |
| **SA-5** | §1.2 的 `is_ignition_event` 代码块与 `substrate/` 中的实现逐字一致 | 判据长出第二个求值点 |

**CA-x：代码侧**（权威源 = P0-a 仓库；每次 push 跑）

| ID | 断言 | 权威源 | 防的是什么 |
|---|---|---|---|
| **CA-1** | 矩阵中 `Seed 写 = ✗` 的资产，路径不得在 `SEED_WRITABLE`；`= ✓` 的必须在 | matrix + §10.1 | 白名单与矩阵各说各话 |
| **CA-2** | 矩阵中 `Soil 写 = ✓` 的资产，实际属主为 `soil`、模式不含 group/other 写位 | matrix + 文件系统 | §15.6 自陈的风险：「只是设计愿望，不是实现契约」 |
| **CA-3** | 种子代码（`meristem/`、`body/`）中不出现 vault / soil-ledger / scoreboard 路径常量 | 全仓 grep | S2 / S3 穿透 |
| **CA-4** | `substrate/` 不 import `meristem` | 全仓 grep | I9（已存在，纳入本集统一管理） |
| **CA-5** | `seed/probe-proposals/*.json` 不含 `entrypoint` 字段 | §8.1.1 | 种子在土壤内执行任意代码 |
| **CA-6a** | **实际台账**出现的 `kind` 集合 ⊆ 规格声明集合 | §8.2 + §10 | 实现冒出规格里没有的 kind |
| **CA-6b** | **测试 fixture** `tests/fixtures/soil-ledger-all-kinds.jsonl` 覆盖规格声明的每一个 kind | 同上 | 规格声明了、却从未被实现的 kind |
| **CA-7** | 台账中不存在 `calibration: true` 的 `accepted_fitness`；**且**每条 fitness 类事件都带齐 §8.2 的六个强制字段 | §8.2 + §12.0.1 | 用装置对照组的读数宣布装置活着；缺键让断言空真 |
| **CA-8** | `state/` 下每个文件都匹配 `^state/soil-[a-z0-9-]+\.jsonl$`；**且**无 symlink、无指向 `seed/`/`soil/` 的 hardlink | §8.1.5 | 新增台账落在保护面外；路径族被文件系统语义绕过 |
| **CA-9** | 每条 Measurement 的 `probe_manifest_sha` == 该 probe 冻结登记的 `frozen_probe_manifest_sha` | §4.1 + C1 | 保持 `probe_id` 不变、偷换 vault 内容 |
| **CA-10** | **一次晋升的事件链完整且逐字段对应**：`accepted_fitness.source == promotion_intent.source` · `accepted_fitness.commit == promotion_committed.commit` · `== scoreboard.commit` · `parent` 一致 | §10 | **数量相等但张冠李戴**，见下 |
| **CA-11** | 正式 `soil-autonomous` authority 的 `promotion_intent` 必须包含完整 authority/source/commit/parent 字段；manual/panel 对等性由受控 pipeline parity test 验证 | §12.0.2 | 防止正式 autonomous authority 绕过完整晋升事务链 |
| **CA-12** | 每个投影（`soil/report-facts.json`、`seed/feedback.json`）的 `source_ledger_tail_hash` 等于当前 ledger tail；不等则**先重建投影，再放行 seed cycle** | §12.0.3 | 种子读到滞后事实；报告与台账不一致 |

**CA-6 为什么必须拆成两条（v5.8，外部审计第 2 项）。**
v5.7 的 CA-6 要求「规格声明的每个 kind 都至少被写过一次」——**在刚初始化的空仓库里这天然为假**：
`accepted_fitness` / `promotion_outcome` / `stale` 在第一次 CI 运行时都还没有真实事件。
危险的不是「CI 红一阵子」，而是**有人为了让它变绿，往正式 ledger 里塞测试事件**——
那会永久污染实验历史，**而实验历史正是这整份规格要保护的东西**。

拆开之后：**覆盖度由 fixture 承担（CA-6b），真实台账只需满足包含关系（CA-6a）。**
`tests/fixtures/soil-ledger-all-kinds.jsonl` **只喂 CA-6b，永不进入 `state/soil-ledger.jsonl`。**
（这也是 CA-8 禁止 `state/` 出现非族文件的又一个理由：fixture 放错地方就会变成台账。）

> **fixture 的归属**：`tests/` 在 `SEED_WRITABLE` 内（§10.1），**种子可以改这个 fixture**。
> **这不是洞**——fixture 不喂任何权威判据，种子篡改它最多让自己的 CI 变红。
> 之所以写明，是因为 §17.5 要求新增任何资产都过一遍归属这道题：**填得出，才算想清楚了。**

**CA-10 为什么必须查对应关系而不是数数（v5.8，外部审计第 6 项）。**
v5.7 只要求「每条 `accepted_fitness` 都有对应的 `promotion_committed`，反之亦然」——
**候选 A 的 `accepted_fitness` 配上候选 B 的 `promotion_committed`，数量照样相等，断言照样通过。**
晋升是三步非原子操作，要防的恰恰是**中途崩溃后事件错配**，而按数量核对对这种错配完全无感。
**逐字段一一对应才是这条断言原本要说的话。**

### 17.8.3 断言集自身的失效模式（写明，否则它就是下一份「声明了没断言」）

| 失效 | 处置 |
|---|---|
| CA-2 在无法真正拆 UID 的环境下不可断言 | 降级 best-effort；CI 必须打印 `SKIPPED (no UID separation)` **并置 `SECURITY_ASSURANCE=BEST_EFFORT`**（见下）——§15.6 已要求「必须在此明写」，**跳过必须可见；静默跳过等于没有** |
| 断言集本身无人维护 | 新增数据/能力时，「§16 加行」与「本节加断言」是同一个动作；SA-3 保证前者，**无人保证后者**——**留作已知缺口，不假装闭合** |
| CI 未接入 | 与 §17.8.0 同批完成；未接入之前，**本节等同于未生效**，不得据此声称一致性已被保证 |

#### `SECURITY_ASSURANCE`：CI 全绿 ≠ 隔离已被证明（v5.8，外部审计第 3 项）

CA-2 是全套断言里**唯一直接检验执行隔离是否真实生效**的一条。它一旦被跳过，
其余断言全绿也只说明「代码逻辑自洽」，**完全没说明 soil / worker 的 UID 隔离成立**。

| 取值 | 条件 | 允许的叙述 |
|---|---|---|
| `SECURITY_ASSURANCE=FULL` | CA-2 实跑且通过 | 可以说「执行隔离已被验证」 |
| `SECURITY_ASSURANCE=BEST_EFFORT` | CA-2 被跳过 | **只能说「代码测试通过」**；不得声称隔离已被证明 |

该取值必须**同时写入 `soil-ledger` 与 `soil/report-facts.json`**，
从而进入报告渲染链（S8）——而不是只活在一次 CI 的终端输出里。
**终端输出会滚走，台账不会。**

> **`BEST_EFFORT` 下，P0-a 不得被标记为 fully verified。**
> 这条是 S8「不得叙述成绩」用在**实验装置自己**身上：把「CI 全绿」讲成「隔离已被证明」，
> 与 v3.1 把 `score > 0` 讲成 `proved_better_by`——**是同一个渲染层的谎言，只换了题材。**

> **本节不假装自己完备。** 它只把审查里**已经发生过**的失配转成断言，
> 不承诺挡住尚未发生的那一类。**「规格能穷尽风险」这个假设本身，正是这份文档多轮返工的根因。**

---

## 18. 变更记录

> **按版本降序。** 上一版此表的顺序是 v5.0 / v5.1 / v5.5 / v5.4 / v5.3 / v5.2——
> 一份读不出时间线的变更记录，本身就是本文档那个家族病的又一处症状。本轮一并修正。

| 版本 | 来源 | 主要变更 |
|---|---|---|
| **v5.21** | **Candidate syntax preflight 与 syntax feedback 闭环** | ① soil 在 measurement 前对 candidate tree 的 Python 源码做无副作用内存 compile · ② 语法错误写 `candidate_preflight/failure_reason=syntax_failure`，不跑 probe、不写 fitness · ③ `syntax_failure` 归类为 model/strategy semantic failure，不计 mechanism failure · ④ bounded projection 与 reflection 输出 syntax-specific compile-safe next strategy · ⑤ 新增 preflight、reflection、task-state 回归测试；H1 继续冻结 |
| **v5.20** | **模型通信完整性与 worker recovery 根因修复** | ① 独立审计确认 tests/22 并非 socket/provider/parser 截断：`returned_paths_hash` 只含 classifier，实际 tests/22 来自 `_recover_worker_changes()` 对未 materialize 的 writable `tests/` 目录错误推断删除 · ② recovery 仅允许在 `_WORKER_COPY_DIRS` 已 materialize 的目录推断 deletion · ③ provider telemetry 增加 soil-only `finish_reason`/prompt/completion token，cycle 33 实测 `finish_reason=stop`、2700/1723 tokens、parse=ok · ④ 新增 recovery 回归测试，H1 继续冻结 · ⑤ server request 改为 newline framing，防止 Unix stream partial recv |
| **v5.19** | **综合 Learning Soil 优化与重测** | ① cycle-start 改为 task-scoped projection，避免跨 task feedback 污染 · ② Task 增加 `forbidden_paths`/`required_target_paths`，candidate measurement 前由 soil 硬校验 · ③ engine/supervisor 增加无敏感 prompt/response/path-set hash、token/length/parse telemetry，并与 attempt/cycle 关联 · ④ 新 task cycle 31/32 真实验证：feedback=true、reflection attempts=1、tests 违规被 `UNMEASURED` 阻断，未污染 fitness，H1 继续冻结 |
| **v5.18** | **Learning Runway strategy evidence correction** | ① 发现 path-family-only fingerprint 将三种真实不同 classifier strategy 错误合并 · ② strategy fingerprint 改为 soil-owned bounded diff shape：family/file count/add-delete totals/patch hash，仅存 hash 不存 mutation body · ③ projection 对历史 candidate 从 git object 重建 shape，纠正 strategy memory 与 reflection diagnosis · ④ 三轮真实 strategy 重新识别为不同 fingerprint；probe 仍无改善，H1 继续冻结 |
| **v5.17** | **独立审计修复** | ① promotion `why` 改为 closed vocabulary，禁止自由文本进入 worker · ② projection 优先用 `attempt_id` 关联 cycle/observed，旧历史仅兼容读取 · ③ reconcile accepted/promotion committed 继承原 observed/intent attempt · ④ 未识别 worker 失败归类 `worker_error` · ⑤ reflection 增加 source attempt IDs 与 ledger hash，engine 对 stale reflection fail closed · ⑥ learning-status 从 ledger 统计 promotion 与 attribution completeness |
| **v5.16** | **MoA soil-learning review + owner decision** | ① 保留现有 worker/gateway/hidden-probe/promotion 防伪边界 · ② 新增 soil-owned `attempt_id` 贯穿 cycle/measurement/promotion 事件，分离机制故障与 task 失败 · ③ 新增 task state projection，避免 semantic failure 无限重试 · ④ 新增 strategy fingerprint/memory、diagnostic review 分类与显式 reflection projection，均无 promotion 权限 · ⑤ 新增只读 `learning-status` 与 Learning Runway gate；阶段 5/H1 在 full test、calibration、attempt linkage、task transition、strategy dedupe、diagnostic/reflection 和真实 runway 通过前继续冻结 |
| **v5.15** | **owner 决策 + anchor A4 contract 复核** | ① §15.6 修订为：P0-a 的 worker 网络由 execution policy 显式声明；本实验保留网络口并记录为已接受风险，不得宣称禁网闭环；禁网与资源围栏归入 P0-b 强化策略 · ② anchor A4 保留原 hidden case，但语义从“固定顺序命中”收紧为**多信号根因判定**：明确 under-budget/okay 的类别不得获选，必须选择真正阻塞 gate 的类别 · ③ 当前 classifier 的 first-match-wins 明确标为 baseline 限制，尚未实现 A4 contract · ④ anchor vault 内容未改，具体 cases 仍不可写入仓库规格 |
| **v5.14** | **S7 / C1 / anchor 三条并行落地 + 服务器实测** | ① **S7 的密钥边界在当前 IPC 形状下结构上不成立（架构级，需裁定）。** §8.1.3 与 `meristem/llm.py` 的 docstring 都声明「种子不持有 API key」，但 `llm.py` 是用 `subprocess.run(raw.split(), ...)` **不传 `env=`** 启动网关的 —— 网关继承的正是种子那份已被裁剪的环境，**密钥根本送不到网关**。也不能靠文件权限补救：网关是种子的子进程、同一 UID，种子读得到的它才读得到，反之亦然。**「网关是种子的子进程」这个形状，托不住一个种子不该看见的秘密。** 当前实现只能 fail closed（`no_credentials`），这不是开发环境的权宜，而是这个形状唯一能诚实做到的事。裁定属 P0-b：要么由土壤持有网关进程、种子经土壤拥有的通道说话，要么明写 S7 只是接口边界而非密钥边界 · ② `meristem/llm.py` 的 `_GATEWAY_TIMEOUT_SECONDS = 30` 与 `soil/model-policy.toml` 的 per-slot `timeout = 900` 冲突：一次合法的长推理会以 `gateway_unavailable` 回到种子，把「模型太慢」与「策略拒绝」混成同一个观测 · ③ **拍号推进不得挂在拍号所守的那道闸后面**（服务器实测的死锁）：`_next_soil_cycle` 原本数 `observed_fitness`，而那种事件只在 `validate_task()` 通过后才写，C1 的 `eligible_after` 又要求拍号先前进 —— 计数器永远停在原地。与 v3.1 `campaign_calls` 同族；I1 的「一切计数皆滚动窗口」防的是同一类，但这条更基本。改为「台账最大拍号 + 1」，且 `manual-cycle` 在任何校验之前先写一条 `kind:"cycle"` · ④ **anchor 接入被测集合**（`catalogue()` 现扫 `internal/active/` + `anchors/`）——§12.2 的「anchor 回归即拒」要成立，anchor 必须真的在 `run_all` 量的那个「全套」里。**同一改动必须同时做的**：`validate_task()` 原先靠「anchor 在清单里找不到」来拒绝它作 primary，anchor 一进集合那种写法就**静默失效**，已改为按来源目录判别 · ⑤ 登记 `soil/frozen-probe-registry.json` 这个路径（此前只由 CA-9 的测试代码发明，规格未命名）· ⑥ `build_context` 现在把种子的可写/只读面与宪法放进 prompt：此前种子被要求整文件替换却不知道自己能写哪些文件，每拍栽在路径违规上 —— **那测的是「能不能猜中白名单」，不是 H1 要问的「能不能沿梯度爬」** |
| **v5.13** | **§13.3 的处置真正执行 + 独立 code review** | ① **v3.1 从 substrate 整块删除，不是加闸。** `supervisor.py` **1588 → 331 行**（删 promote/canary/heartbeat/rollback/run · journal/proposals/agenda 状态面 · cap case · lifecycle 守卫 · probe staging），`run_meristem.sh` 删除，`MERISTEM_ALLOW_LEGACY` 后门删除。**上一版给这些入口加默认拒绝是错的方向**：一个加了锁的第二入口仍然是第二入口，而那个后门存在的唯一理由是一个已在 §13 判死的系统。§13.3 本就写着「整段删除」/「重写或删除」/「保留骨架、重写判决回路」。P0-c 需要 keeper/breaker/rollback 阶梯时，**照 v5 语义重新设计、从 git 历史取材**，而不是解冻 v3.1 的实现 · ② **panic 闩接入 v5**：v3.1 删除后闩会变成零调用点的安全开关（`advance()` 就是这么活过 400 拍的），现由 `manual-cycle` 启动即检查；`ignition-status` 只读不受闩影响 —— **停机时最需要看的就是判据** · ③ **P0（独立 review 实测）**：`_isolated_organ()` 的 `shutil.copytree` 默认解引用符号链接，而 `body/organs/` 在种子可写面上 —— 种子放一个指向 vault 的链接，**复制在土壤进程里、降权之前完成读取**，vault 被搬进 worker 的工作副本。**为隔离而加的那一步正是绕过隔离的路。** 修法：拒绝含任何链接的 organ 目录（`unmeasured`），`symlinks=True` 作纵深 · ④ 「`worker` 无附加组」此前只在**创建账户那一刻**成立，现由运行时与部署脚本逐次核实；超时预算扩展到覆盖准备阶段。**两项未闭合已登记**：土壤实际以 root 而非 `soil` UID 运行（§15.6 身份模型未真正建立）；`SECURITY_ASSURANCE=FULL` 不观测本轮三条隔离属性 |
| **v5.12** | **服务器部署 + 实机验证（`hermes-media`）** | **§15.6 的档位矛盾就此裁定，方式是把更严的那一边实现掉。** v5.11 登记过：档位表把「禁网」列在 **P0-a**，而同节验收判据散文把联网 organ 测试归入 **P0-b**。本轮在服务器上实现了完整的最小完整性隔离（`worker` UID + 无附加组 + 私有网络命名空间 + organ 独立工作副本，配合 vault `soil:0500` 与 `state/soil-*.jsonl` `soil:0600`），**三条要求全部由操作系统强制并有实机通过的断言** —— 因此**按档位表裁定：禁网属 P0-a**，散文那一处以本行为准。**裁决的依据不是文本推敲，是它已经做出来了**：私有网络命名空间与降权是同一条命令拿到的，没有理由把它推迟到 P0-b。② 顺带：`execution_policy_version` 由 `"1"` 升至 `"2"` —— 执行策略变了，隔离前后的分数**不可直接比较**，而让这种不可比显式发生正是 §4.1 那三个维度存在的全部理由。③ 隔离级别可被读出（`probe_runner.isolation_status()` → `enforced` / `best_effort`），**不再只活在注释里**；`best_effort` 下不得声称满足 §15.6 的 P0-a 档 |
| **v5.11** | **三份独立审查（土壤层 / 波次 1 / 对抗性安全）** | **本轮几乎全是实现缺陷，只有一处是规格自身的矛盾，记在这里。** ① **§15.6 内部不一致**：档位表把 **禁网** 列在「最小完整性隔离 / P0-a」行，而同节末尾的验收判据散文把「构造一个联网 organ」归入**资源围栏档（P0-b）**的三件套。同一份文档对同一项要求给出两个档位，**照哪一边实现都能自称合规** —— 而这正是 §17.5 点名的那类漂移，SA-1..SA-5 全都抓不到它（它们扫名字，不扫档位归属）。**本轮不擅自裁决该项归属**（那是实质性改动，须经独立复审），只把矛盾登记在案：在裁决之前，任何一次 P0-a 运行都不得声称已满足 §15.6 的 P0-a 档。② 顺带记录：`substrate/pipeline.py` 对 §10.2 的偏离实为**三处**而非其模块 docstring 自称的两处，第三处是 `task_evaluator.evaluate(task, observed, ctx)` 的 `ctx` 参数在实现中不需要、且 §10 的土壤模块清单里从来没有 `task_evaluator.py` 这个模块 —— 它只在那一行调用里存在过 |
| **v5.10** | **P0-a 波次 2 落地（pipeline + 台账 + manual-cycle）** | **又是四处只有被实现才会暴露的缺陷，外加一处正文自相矛盾。** ① 新增土壤模块 **`substrate/soil_state.py`**（Ledger / Scoreboard / PromotionLock / SoilContext）—— §10.2 的伪代码通篇使用 `ctx.ledger.append(...)` / `ctx.scoreboard.write(...)` / `with ctx.promotion_lock`，而 §10 的土壤模块清单里**没有任何模块拥有这三样东西**；与 v5.9 抓到的 `model_gateway`（「IPC 只有一端」）同一形状，这里是「上下文只有读取方」 · ② 台账封套补 **`event_id`** —— §10.2 写 `oid = ctx.ledger.append({...})` 随后 `"source": oid`，即 append 必须返回一个**能在台账里被重新找到**的标识，否则 **CA-10** 的 `accepted_fitness.source == promotion_intent.source` 没有东西可解析；§8.2 的封套示例只画了「一条事件长什么样」，没画「两条事件如何互指」。取内容哈希而非自增计数：自增需要全局单写者，而 `kind:"cycle"` 写在晋升锁之外 · ③ `promotion_intent` 补 **`verdict_authority`** —— **CA-11** 要断言「manual accept 与 panel accept 逐字相同，仅 `verdict.authority` 不同」，而从 §8.2 到 §10 **没有任何一条事件携带 authority**：照字面实现两条序列会*完全*相同，CA-11 于是恒真，**这是空真的第三个实例**（前两个是 `.get()` 兜底与 CA-7 缺键）。已知未闭合：拒绝路径尚不带 authority，故 CA-11 只覆盖 accept 一侧 · ④ `Outcome` 补 **`CALIBRATION` / `ABANDONED` / `SOIL_RECOVERY`** —— §10 失败路径表列的是 `process_candidate` **判决路径**的六个出口，而 §12.0.1 的校准（不是失败，记成 `UNFULFILLED` 会污染拒绝额度）与 §10.2 `reconcile_on_start` 自述的「未含则标记 abandoned…无法判定 → soil_recovery」各自需要表里没有的值。**扩的是 `outcome` 不是 `kind`**：CA-6a 只约束 `kind` 的取值域 · ⑤ **§12.0.2 与 §10.2 直接冲突**：前者写「`manual_prompt` 把 fitness 打给实验者」，后者的签名与理由写「面板只收 diff + 任务声明，**不传 observed**——评审员看见 +20 分会锚定向批准」。**按 §10.2 实现**：它是签名的定义处且带着不变量的理由，§12.0.2 那句是描述性散文。**顺带修一处 C-65 的现存实例**：`substrate/supervisor.py` 的 `VAULT` 曾以 `REPO.parent / "meristem-vault"` 为缺省，在 worktree 里会落到 `.claude/worktrees/`——改为只读 `MERISTEM_VAULT`，读不到即拒绝运行 |
| **v5.9** | **P0-a 落地（四个实现子智能体上报，主会话裁决）** | **规格只有被实现才会暴露的四处缺陷。** ① `ProbeRun` 补 `tree_sha`——§8.2 要求每条 record 带 `tree_before`/`tree_after`，而 §10.2 没给树字段、`pair()` 只收一个 commit，**那两个字段填不出来**；§4.1 的 Measurement 身份本就含它，是 §10.2 漏抄 · ② `fitness.write()` 删除、`has_regression()` 补声明——前者 pipeline 从不调用且填不出 §8.2 的六个强制字段（谁调谁产出违规事件），后者被 pipeline 调用却从未声明；**一个声明了没人调、一个被调了没声明，正是 CA-6 双向性要防的那对症状** · ③ 新增土壤模块 `substrate/model_gateway.py`（~80 行）——`llm.py` 要「经土壤 IPC 请求一次调用」，而 §10 的土壤清单里**没有任何模块会应答**，IPC 只有一端；网关读 `soil/model-policy.toml`、引用 `budget.check()` 的判定、只回 `allowed/refused/deferred`（**不与 budget.py 合并**：预算判定藏进调用执行，正是 S7 那次死锁的成因） · ④ §17.7 退役名豁免补第三类「前版历史引用」——与 SA-1 对 `journal` 的既有处理同一依据，逐条人工核准。**另修一处陈旧名字**：C4 的论据里写 `report-narrative.md`，v5 叫 `seed/narrative.md`；同段裸写的 `journal` 改为「v3.1 的 journal」，否则读者会以为 v5 也有一个。**另加 2 处 advisor 复审勘误**：⑤ organ 入口路径与输出 ABI 由 §10.2 写死——此前这份约定只分别记在 `probe_runner.py` 与各 organ 自己的 docstring 里，「两处各写一份应保持一致」正是 §17.7 点名的语义漂移温床，防的是一侧改约定而另一侧注释没跟上、无机制报错、只在某次探针运行时把 check 悄悄记成 `unmeasured` · ⑥ §13.3 表 C 补一行：supervisor 起 `python -m meristem.loop cycle` 子进程必须注入 `MERISTEM_MODEL_GATEWAY`，防的是一个不存在的网关与一个永远拒绝的网关在种子眼里长得一样——缺失时 `call_model` fail-closed 为 `refused`（`gateway_not_injected`）并在 stderr 打印 `GATEWAY_NOT_INJECTED`，把联调时最难查的哑故障变成可 grep 的一行 |
| **v5.8** | **外部独立审计（裁定 APPROVED WITH BOOTSTRAP FIXES）** | **P0（唯一）：`accepted_fitness` 缺 Task 身份，`ignition-status` 只扫台账因而无从解析事件属于哪个 Task——§1.2 判据名义唯一、实际不可自包含求值、不可重放。** 修法：封套携带 `task_id` / `primary_probe` / `generation` / `soil_cycle`（六个强制字段成表，缺键即 schema 违例），谓词签名收紧为 **`is_ignition_event(ev)` —— 单条台账行的纯函数**，不接 task、不查注册表、不做多跳追溯。**另六项**：② CA-6 拆 CA-6a（真实台账 ⊆ 规格集合）+ CA-6b（fixture 覆盖），防「为了 CI 变绿往正式 ledger 塞测试事件」· ③ CA-2 跳过时置 `SECURITY_ASSURANCE=BEST_EFFORT` 并写入台账与 report-facts，**`BEST_EFFORT` 下 P0-a 不得标记 fully verified** · ④ `manual-cycle` 明确为 **Panel adapter**（y/n 落在 Verdict 位置而非 merge 位置），新增 **CA-11** 断言 manual 与 panel 事件序列逐字相同 · ⑤ CA-10 由「数量相等」改为**事务链逐字段一一对应**（source/commit/parent），防 A 的 accepted 配 B 的 committed · ⑥ 投影加来源指纹并在启动时阻塞校验，新增 **CA-12** · ⑦ `state/` 前缀族收紧为 `^state/soil-[a-z0-9-]+\.jsonl$` + 禁 symlink/hardlink + 临时与备份文件不得落在 `state/`。**顺带修正 v5.7 自己的两处**：`.get()` 兜底在 `calibration` 与 `counts_as_progress` 上**方向相反**，改为强制字段 + 缺键抛错；§12.0.2 的 `panel=None` 与 §10 签名注释矛盾，改回 `manual_prompt` |
| **v5.7** | **第五轮交接项收尾 + 顾问** | **5 项交接债全部关闭**：① `state/soil-*.jsonl` **前缀族**属主取代单文件名保护，scoreboard 定名 `state/soil-scoreboard.jsonl` 并补进权威矩阵 · ② Fitness 身份补三个版本维度，`pair()` 失配即 `unmeasured` · ③ `primary_probe` 必为 internal，**anchor 非对称**（回归即拒 / 上升不加分 / `overfit_suspected`）· ④ §1.2 成为出生判据的**全文唯一定义点**，`ignition-status` 为唯一求值点 · ⑤ **§17.8 SA/CA 断言集**——§17.5 与 §16 的机械化落地，并把 §17.7 冻结条款的执行机制从「记得遵守」换成 CI。**顺带勘误（6 处，均由本轮机械扫描抓到，非外部审查）**：删除从不存在的 `kind:"fitness"` · `probe_manifest_sha` 三名统一 · S2 与 §16 关于「种子读记分板」的直接矛盾 · §10 失败路径表列头 `kind` 拆为 `kind` + `outcome`（v5.5 的 T3 统一出口后未同步，照表实现会写出规格里没有的 kind）· C3 伪代码 `kind:"stale"` 改走 `finalize_nonpromotion()`（同一处病的第二个实例）· 补回本表缺失的 v5.6 行并改为降序。**另加 2 处 advisor 复审勘误**：§10 pipeline 两处 `append` 补显式 `calibration` 键——缺键会让 **CA-7 空真恒过**（一条永远绿、也永远不检查任何东西的断言），**而这个洞正是本轮新增文本自己引入的** · `ignition-status` 的 `excluded` 归因顺序定死为「第一个不满足的合取项」 |
| **v5.6** | **第五轮外部独立审查** | **三个 P0**：`seed/` 目录前缀白名单穿透 S7 与 T5（改为文件级白名单）· 晋升三步非原子缺崩溃恢复（`promotion_intent` + `reconcile_on_start`）· 特殊 Task 未从 Change pipeline 分流；另修权威矩阵读权限错误。**本轮同时列出 5 项未落交接项——由 v5.7 关闭**（当时仅改了 header 版本号，未记入本表） |
| v5.5 | 第四轮落地 | T1–T6 全部写进正文：pipeline 显式上下文 · merge 前二次 ancestry + `promotion_lock` · `finalize_nonpromotion()` 唯一非晋升出口 · `unfulfilled` 语义与额度 · `seed/feedback.json` 事实投影 |
| v5.4 | 外部独立审查 ×4 | **隔离两档重切（最小完整性隔离提前到 P0-a）· 执行身份模型 · anchor rubric 同受执行契约 · Measurement 身份加三个版本维度 · `eligible_after` 带 namespace · 六项待办明标（T1–T6）· 一致性自检机械化** |
| v5.3 | 外部独立审查 ×3 + 顾问 | **C6 不可信执行契约（两档）· 一次杀死 `journal` 一词 · observed/accepted 两阶段事件 · anchor ≥5 满足 I2 · 目录重构为 `seed`/`soil`/`root` 三权威级（黑名单→白名单）· REPORT 三文件 · freeze 登记 + manifest hash · recovery 作用域含 `soil/` 与绝对禁区 · `no_measurement` 与 metric 的具体阈值 · gradient evidence 与 ignition 分开** |
| v5.2 | 外部独立审查 ×2 + 顾问 | **Probe 二分（anchor/internal）、cases 入 vault、Task 声明 schema、bootstrap recovery、model policy 拆分、substrate 完整依赖清单、五个硬契约、权威矩阵、`TaskDecision`、Metric Registry** |
| v5.1 | 顾问深审 | S8 自省渲染、`pipeline.py`、`manual-cycle`、H1/H2/H3 与否证条款、点火 organ 构造规格 |
| v5.0 | 本体论反推 | S1–S7、脊柱 + 四支撑、三个理解预算取代 LOC 上限 |

---

## 附录 A：点火 organ 的构造规格

**它是实验的对照品，不是随手写的示例。构造方式决定 P0-a 到底在测什么。**

**陷阱**：若「坏」是显眼的（比如一个被注释掉的正确分支），P0-a 测的是「**模型会不会读 diff**」，不是「**会不会沿梯度爬**」。

**规格**：

- 缺陷必须**分布在多个 check 上**，且互相独立
- 具体形态：`classifier` 的关键词表**缺了三个域的词**（每个域对应 1–2 个 check）
- 一次修复自然只提升一到两个 check → **40 → 60 → 80 是三次真实的爬坡，不是一次贴补丁**
- 缺陷不得有单一的「总开关」式修法

**验收**：把 organ 交给一个不知情的读者，问「一眼能看出该怎么修吗」——能，则构造失败，重做。

---

*依据：cycle 1–400 全量回放（两轮独立子代理复核）· 依赖清单（第三轮子代理，逐行 grep）·
`MERISTEM.md` v3.1 设计文档 · 运维坑清单 C-1~C-64。*
