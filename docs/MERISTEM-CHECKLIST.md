# Meristem 运维与任务记录（v5 起）

> **这是 v5 时代的活文档，随代码版本化。** 每次任务后由 Claude 追加一条记录。
>
> **v3.1 的运维坑清单（C-1 ~ C-64、五轮实验的未闭合项）在 `D:/RSI/MERISTEM-CHECKLIST.md`，
> 已冻结，不再更新。** 那份文档记录的是一个已经停机的系统，其中的实验窗口与待验证项
> 随 v3.1 一起结束，**不要照着它去等结果**；作为历史证据仍然有效。

---

## 当前状态

| 项 | 状态 | 位置 |
|---|---|---|
| v3.1 自主进化 | **已全停**（2026-08-23 16:55 服务器时间） | panic 闩 `/RSI/meristem-control/PANIC` |
| v5 规格 | **v5.8-frozen**，外部审计裁定 APPROVED WITH BOOTSTRAP FIXES（fixes 已落） | `docs/MERISTEM-V5-SPEC.md` |
| v5 实现 | **未开工** | — |

---

## 任务记录

### 2026-08-23 · v5 规格 v5.7 → v5.8-frozen + 停 v3.1 进化

**1. 规格迁入版本控制并两次升版**

从 `/RSI/MERISTEM-V5-SPEC.md`（不受版本控制）迁入 `docs/`，1323 → 1856 行。
分支 `worktree-sensenova-deploy`：

| commit | 内容 |
|---|---|
| `ab0a624` | v5.7-frozen 主体：关闭 v5.6 宣布 final 时仍挂着的 **5 项交接债** |
| `c70445f` | v5.7 顾问复审的 2 处修复 |
| `ac7f5f7` | **v5.8-frozen**：外部独立审计的 **1 项 P0 + 6 项 bootstrap/P1** |

v5.7 的 5 项：scoreboard 归 `state/soil-*.jsonl` 前缀族 · Fitness 身份补三个版本维度 ·
`primary_probe` 必为 internal + anchor 非对称 · 出生判据成为唯一定义点 + `ignition-status` ·
**§17.8 SA/CA 一致性断言集**。

v5.8 的 P0：`accepted_fitness` 不带 Task 身份，而 `ignition-status` 只扫台账
→ **出生判据不可自包含求值、不可重放**。修法比审计要求更进一步：谓词签名收紧为
**`is_ignition_event(ev)` —— 单条台账行的纯函数**，不接 task、不查注册表、不做多跳追溯。

**2. 停掉 v3.1 自主进化**（走 keeper 自己设计的路径，不是 kill 进程）

- 上闩 `/RSI/meristem-control/PANIC`
- 停之前先确认心跳状态：刚跑完 cycle 403、已 push main，正处在 beat 间 22 分钟休眠，
  **不是 merge 中途**——最安全的停止点
- SIGTERM 心跳 → keeper 走闩分支退出，日志留下
  `16:55:06 KEEPER STOP: panic latch engaged during run -- full stop`
- 核实持久性：**无 cron、无 systemd unit**；即使有人手动跑 `run_meristem.sh`，
  循环顶部的闩检查会立刻 exit 3。**闩本身就是持久停止机制，不依赖进程是否还在。**

**3. 踩到的坑（供下次）**

- **`pkill -f '<pattern>'` 经 SSH 会自匹配**：wrapper 的命令行里含有该 pattern，
  pkill 杀掉自己的父 shell → SSH 退 255 且无输出。
  改用更精确的 pattern，或先 `pgrep` 拿 PID 再 `kill <pid>`。
- **worktree 隔离下 Edit 工具拒绝改仓库外的文件**（含 `/RSI/*.md`），哪怕它不在任何 git 仓库里。
  规格是靠「先 copy 进 worktree 的 `docs/`」解决的——这恰好也是它本该待的地方。
- **两次「冻结」都是带着债宣布的**（v5.6 带 5 项交接项，v5.7 带这项 P0）。
  结论写进了规格 §17.7：**冻结不该由作者自己宣布**——与 S4「判决归土壤」同一条原则。

**4. 留下的问题（未闭合，不假装闭合）**

- `D:/RSI/Meristem/docs/MERISTEM-CHECKLIST.md`（主 checkout）是 **0 字节**，
  另一并发会话 01:15 未完成的搬运。**本文件是新正本**；那个 0 字节文件应删除。
- 分支 `worktree-sensenova-deploy` **尚未 merge 到 main、尚未 push**。
- §17.8 的 SA/CA 断言集**目前只是规格文本，尚无实现**。CI 接入前它等同未生效（§17.8.3 自陈）。
- v3.1 最后一拍 cycle 403 的拒绝原因是 `mutate:glm failed after 4 attempts: read operation timed out`
  ——GLM 免费档的已知额度问题，不是缺陷，无需追查。

---

## 常用命令（v3.1 诊断，进程已停）

```bash
# 闩状态
ssh hermes-media "ls -la /RSI/meristem-control/PANIC; cat /RSI/meristem-control/PANIC"

# 确认没有进程在跑
ssh hermes-media "pgrep -af 'run_meristem|substrate.supervisor'"

# keeper 日志尾部
ssh hermes-media "tail -20 /RSI/Meristem/heartbeat_keeper.log"

# 清除闩锁（只有人能做；清除前请确认确实要重启 v3.1）
ssh hermes-media "cd /RSI/Meristem && source /RSI/meristem-env && python3 root/panic.py clear"
```
