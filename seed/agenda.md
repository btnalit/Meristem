# 议程
#
# P0-a 阶段**由人给任务**（§12.0：「人给任务，人做判决」）。
# 种子在 P0-b 才开始自己选题 —— 那时这份文件的属主语义会变，但格式不变。
#
# 取题规则（meristem.task.take_task）：首条非空、非 `#` 注释、且未 done/parked 的行。
# 可选的 "- " / "* " 列表标记会被剥掉。
#
# **注释只认 `#`。** Markdown 的 `>` 引用块**会被当成任务**——
# 这份文件长得像 Markdown，但解析它的不是 Markdown 解析器。
# 本文件初版用 `>` 写导语，结果首条「任务」变成了那句导语，
# 被 manual-cycle 的身份核对当场拦下（那正是它存在的理由）。
#
# 任务身份 = 行文本的 sha256 前 16 位（meristem.task.task_id）。
# 改动下面这一行的任何一个字，task_id 就变了，
# 而 soil/p0a-task.json 里的声明必须同步 —— 土壤在 manual-cycle 启动时核对两者，
# 不一致直接拒绝运行。**一个任务只能有一个身份。**

Improve classifier root-cause classification for contract-budget and protected-path failures on probe-classify-basic using a bounded falsifiable alternative hypothesis after prior no-gain attempts; modify only body/organs/classifier/ and do not modify tests, probes, constitution, or agenda.
