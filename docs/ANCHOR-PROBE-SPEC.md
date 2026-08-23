# Anchor Probe 规格说明 —— `probe-classify-basic` 的隐藏对照

> **本文件不含、也永远不会含任何 anchor 的 `input` 或 `expect`。**
> 那些 case 只存在于 vault（`/RSI/meristem-vault`，仓库之外），由人撰写、人维护，种子永不可读。
> 本文件只回答三个问题：需要几个 check、各自覆盖哪个能力域、为什么这样切分能检出「把 internal
> 的 5 条 case 硬编码对了」。除此之外的任何具体化尝试都是对 §8.1.2 可见性边界的违反。

## 1. Schema 与数量约束

- anchor 与 internal 共享**同一 Probe schema**（§8.1.1），无 `entrypoint` 字段，`cmp` 只用
  `equals`/`contains`/`regex`（regex 只引用土壤预置命名正则）。
- **≥5 个 check，score = passed/total×100**（I2）。**anchor 不设 I2 豁免**——本文档的 check 数
  与 internal 一致取 **5**，避免「两类 probe 两套形状」这个边界一旦松动就会持续漂移的问题
  （§12.2 已写死这条）。
- `capability` 与 `organ` 字段与 `probe-classify-basic` 相同：两把尺量的是**同一个能力**
  （`classifier` organ 把失败原因文本分到命名类别），只是取样点不同。

## 2. 五个 check 覆盖的能力域（结构性描述，非具体 case）

internal 的 5 个 check 各自是**一条固定字符串 → 一个固定类别**的正例断言（见
`seed/probe-proposals/probe-classify-basic.json`）。anchor 的 5 个 check 刻意选在 internal
**结构上覆盖不到**的坐标上取样，两者互补：

| # | 能力域 | 与 internal 的关系 | internal 为什么测不到这一点 |
|---|---|---|---|
| A1 | **同义改写鲁棒性**（budget 类别，任选 closure/prompt/contract 之一） | 同一目标类别，不同表面措辞 | internal 每个类别只锚定一条固定措辞；只要 organ 对*那一条字符串*返回正确类别就能过，不要求覆盖该类别的其他自然表达 |
| A2 | **同义改写鲁棒性**（path/regression 类别，任选 protected-path/probe-regressed 之一） | 同上，取样在 internal 未覆盖的另一组类别 | 同上 |
| A3 | **负例 / 默认路径正确性** —— 输入不属于任何已命名类别，正确输出应为分类器的「无匹配」信号 | internal 的 5 条全部是正例，没有一条断言「不应该匹配任何类别」 | 只在正例上加词条也能让 internal 满分；负例是完全独立的坐标，internal 结构上无法产生这类约束 |
| A4 | **多信号并存时的判定行为** —— 输入同时包含一个以上类别的触发线索，断言分类器的选择不是「哪个词表先长出来就选哪个」的偶然产物 | internal 每条输入只携带单一类别的线索 | internal 的固定字符串从未测试过词表增长顺序/优先级这件事 |
| A5 | **跨措辞的回归探测** —— 复用 internal 已覆盖的某个类别，但用与 A1/A2 不同的另一种表面措辞再取一次样 | 同一类别的第三个取样点，独立于 internal 的固定串与 A1/A2 的取样点 | 检验「修好某个类别」这件事本身是否真的具备泛化能力，而不是每次只多适配一条新字符串 |

**行数与顺序仅为结构说明**：实际 anchor 文件中 5 个 check 具体分到哪些类别、用什么措辞、
顺序如何排布，由撰写 anchor 的人自行决定，不在本文件的约束范围内——本文件只保证「结构上与
internal 互补」这条性质成立，不锁定实现细节，以免这份规格本身变成可被逆向工程的第二份线索。

## 3. 为什么这个切分能检出「硬编码 internal 的 5 条 case」

能让 internal 从 40 分升到 100 分的两类候选变更：

1. **真正扩充了 organ 的分类能力**（例如把某个类别遗漏的关键词补全，或改进了匹配逻辑本身），
   这类变更对「同一类别的另一种测法」同样成立——因为它改的是能力，不是对某个固定输入的特判。
2. **只让 organ 在 internal 那 5 条固定字符串上返回正确答案**，无论手法是逐字特判
   （`if text == "<internal 的某条 input>": return "<expect>"`）还是把关键词写得刚好只覆盖
   那条字符串的字面片段——这类变更**不改变**分类器对任何其他表面形式、任何负例、任何多信号
   输入的行为。

anchor 的 5 个坐标（A1–A5）恰好都落在「internal 固定串之外」：换一种措辞（A1/A2/A5）、
断言负例（A3）、断言多信号下的选择顺序（A4）。**第 2 类变更在这 5 个坐标上的期望表现与
修改前完全一致**（因为它从未触及这些坐标），所以 anchor 分数不动；**第 1 类变更**如果真的
改进了底层匹配能力，则大概率也覆盖了 A1/A2/A5 这类同域改写，anchor 分数随之上升。

两类变更在 internal 上同样能拿到 100 分，**只有在 anchor 上才分得开**——这正是
§12.2 把 anchor 的作用写成「只做 anti-overfit、不进出生判据」的原因：internal 持续上升而
anchor 长期不动，记 `overfit_suspected`（供人看见，不自动拒绝），而不是被当作真实能力提升
去计分。

## 4. 与 internal 的边界重申（不在本文件展开，仅引用）

- anchor 的创建/冻结与 internal 一样受 C1 约束：不得与针对同一 organ 的能力变更落在同一个
  Change 里；第一次分数只能是 `baseline`。
- anchor 回归 → 候选拒绝；anchor 上升 → 不加分、不进 `improved`；internal 升而 anchor 不动 →
  `overfit_suspected`。三条处置规则已在 §12.2 写死，本文件不重复其判据逻辑，只说明 A1–A5
  这五个取样点为什么足以支撑这三条规则的判定基础。
