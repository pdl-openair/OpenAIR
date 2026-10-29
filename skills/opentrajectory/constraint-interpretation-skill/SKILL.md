---
name: constraint-interpretation-skill
description: >-
  将 StructuredRequirement 中的原始约束解读为结构化 ConstraintInterpretation，
  区分明确约束、软约束、歧义、明确不限与缺失信息，并输出可追溯的正式约束语义，
  供 retrieval-matrix-generation 使用；在需求结构化完成、进入检索前，或用户请求解析约束时使用。
---

# 约束解读

把用户已经表达的约束解释清楚，并把不确定性原样保留下来。只解释当前约束，不扩写研究方案，不替用户补阈值、材料体系、方法偏好或检索策略。

## 输入

必填：`user_query`。

可选：`research_domain`、`material_system`、`research_context`、`task_schema`、`raw_constraints`、`available_documents_summary`。

优先从以下来源收集候选约束：

1. `raw_constraints[].phrase`；兼容旧字段 `original_phrase`；
2. `user_query` 中明确表达的限制、要求、偏好、排除、范围、不限或未知；
3. `research_context.boundary_conditions[]`；
4. `task_schema.required_conditions[]`。

把 `research_domain`、`material_system`、研究目标、指标、方法列表等作为解释上下文。除非用户或正式上游明确把它们表达为限制、要求或偏好，不要仅因它们出现在上下文里就新建约束。

不要把完整 `user_query` 兜底成一条伪约束。无法识别约束时返回空 `interpreted_constraints`，并在 `quality_report` 说明缺口。

## 核心判断：先判断约束状态，再填字段

对每个候选短语，先判断它属于下面哪一种。不要用 confidence 代替这个判断。

### 1. 明确约束

用户已经明确给出限制、范围、必需条件、排除条件或确定的数值方向，例如 `≤4.2V`、`至少 500 cycles`、`必须使用 DFT`。

- 保留用户原始表达；
- 语义明确时填写 `interpreted_value`；
- `needs_user_confirmation: false`；
- 保留用户表达的约束强度与方向，交由 Retrieval Strategy Skill 形成检索策略；
- 明确出现的数值、单位、材料或方法不需要因“未经外部验证”而降为模糊约束，但可用风险短码标记其来源仅为用户输入。

### 2. 软约束 / 偏好

用户表达“优先、希望、最好、关注、尽量”等偏好，或明确说某个数值只是偏好而非淘汰条件。

- 保留其偏好语义；
- 不因出现具体数值就升级为硬检索政策；
- 语义本身清楚时，confidence 可以较高；confidence 表示“解释是否可靠”，不是“约束有多硬”。

### 3. 歧义

短语存在多个合理解释，或数值虽明确但方向、字段、适用对象不明，例如只给一个数值和单位，但无法确认它表示上限、下限还是某种截止值。

- 不替用户选择某一种解释；
- `interpreted_value` 只保留各解释共有且确定的部分，否则填 `null`；
- 不生成或暗示硬检索政策；
- 歧义会实质改变检索/过滤时，设 `needs_user_confirmation: true`；
- 用 `risk_flags` 标记歧义类型，并在 `domain_rationale` 简短说明。

### 4. 明确不限

用户明确说“不限、无偏好、任何体系都可以、不要按某维度排除”。这不是缺失信息。

- 保留为 `boundary_condition` 或其他最贴切类型；
- `interpreted_value` 可写成 `unrestricted` 或等价英文含义；
- 不生成正向检索词来暗示某一选项；
- 通过正式语义字段保留“不限”事实，不生成过滤指令；
- `needs_user_confirmation: false`。

### 5. 缺失 / 明确未知

用户没有提供某项信息，或明确说“未知、不确定、没有测、现在不能提供”。这不是约束候选值。

- 不从领域常识补值；
- 不为该缺失项新建正向约束；
- 写入 `quality_report.missing_inputs`；
- 必要时用 `quality_report.risk_flags` 说明影响。

## 禁止补造候选值

`candidate_values` 不是领域知识猜值区。

只在用户或正式上游已经明确给出多个候选值时填写。否则保持 `[]`。尤其不要因为“快充、高温、低温、稳定、轻量、近期”等定性词，自行加入倍率、温度、材料子类、年份、benchmark 或其他具体值。

完全等价的单位表达可以用于理解，并在确有检索价值时作为等价 `downstream_search_terms`；`interpreted_value` 仍保留用户原始值语义，不得借单位换算引入新的阈值或方向。

## 冲突约束

如果用户同时给出互相冲突的明确约束：

- 保留双方，不静默选择、不平均、不覆盖；
- 标记 `constraint_conflict_detected`；
- 冲突未解决前只保留双方语义，不替下游决定检索强度；
- 冲突会改变候选集时，设 `needs_user_confirmation: true`，并令 `quality_report.needs_manual_review: true`。

## confidence

confidence 衡量“当前解释与用户真实表达一致的把握”，不表示约束优先级、硬度或外部事实真实性。

- `0.85–0.92`：短语的语义角色、对象、方向均明确；明确数值+单位+方向通常属于此区间；
- `0.70–0.84`：核心含义明确，但范围、字段或适用对象仍有轻微不确定；
- `0.55–0.69`：定性含义可解释，但边界或操作化方式不清；
- `0.30–0.54`：存在多个合理解释或关键信息明显不足。

同一 query 中不要机械给所有约束相同分数。软约束如果语义明确，也可以有较高 confidence；歧义约束即使包含数字，也不能因“有数字”自动高 confidence。

## constraint_type

选择能表达科研语义的最具体类型，避免把所有内容塞进 `condition`：

- `numerical_range`：数值范围/边界且方向明确；
- `condition_threshold`：有数值或阈值线索，但字段/方向不完整；
- `experimental_condition`：实验或运行工况；
- `material_system`：用户明确限定的材料/对象体系；
- `material_property`：物性要求；
- `method`：用户明确要求或偏好的方法；
- `mechanism`：用户明确要求关注的机制；
- `source_preference`：来源、时间、证据形式偏好；
- `boundary_condition`：适用范围、排除范围、明确不限；
- `required_condition`：明确必须满足的非数值条件；
- `qualitative_condition`：确实无法归入以上类型的定性约束。

`constraint_type_hint` 只是上游提示，不是 Authority；与用户原意冲突时，以用户原文和正式上下文为准。

## downstream_search_terms

输出约束本体的紧凑检索表达：

- 优先使用检索后端效果更好的表达，但语言本身不能成为系统阻塞条件；
- 不写解释句；
- 不扩展到机制、结果、性能指标或材料子类，除非这些本身就是用户约束；
- 不把未确认候选值放入检索词；
- 对明确不限维度通常返回 `[]`；
- 对方向不明的数字，只保留中性的数值/字段表达，不擅自加入 `upper`、`lower`、`minimum` 等方向词。

每项保持紧凑，通常不超过 6 个词条。

## risk_flags 与 rationale

`risk_flags` 只写紧凑、机器可读的 snake_case 短码，不写解释句、括号或阈值说明；解释放在 `domain_rationale` 或 `missing_inputs`。

`domain_rationale` 只写一句对当前解释真正有用的理由，避免报告化复述。

## 最多 6 条时的取舍

`output.schema.json` 最多允许 6 条 `interpreted_constraints`。超过时按用户决策价值排序：

1. 明确硬约束、明确排除、明确数值边界与冲突双方；
2. 会显著改变检索范围的软约束或边界；
3. 明确不限；
4. 来源偏好和较低影响约束。

不要为了凑数量把研究目标、指标、方法列表或缺失信息转成伪约束。

## quality_report

- `confidence`：对整次解释可靠性的总体判断，重点反映高影响约束的解释质量；
- `coverage_level`：按“用户真实约束是否被处理”判断，不按约束条数机械计算；
  - `none`：没有可解释的约束；
  - `partial`：仍有重要约束未覆盖、冲突未处理或关键歧义明显；
  - `moderate`：主要约束已处理，但仍存在不会阻止安全下游执行的缺口；
  - `complete`：所有已提供的关键约束、明确不限和明确未知均被正确处理，无重要遗漏；
- `missing_inputs`：只记录确实缺失或用户明确未知、且可能影响下游的输入；
- `risk_flags`：汇总最重要的跨约束风险，不要求机械复制每条 constraint risk；
- `needs_manual_review`：只在未决冲突/歧义会阻断安全下游执行时为 `true`。普通提示、软偏好、明确不限或非阻断缺口不自动触发人工复核。

## 输出

返回严格匹配 `output.schema.json` 的单个 JSON 对象，所有字段必须存在，不增减。

`interpreted_constraints[]` 每项必须包含：

`constraint_name`, `original_phrase`, `constraint_type`, `interpreted_value`, `candidate_values`, `domain_rationale`, `confidence`, `needs_user_confirmation`, `downstream_search_terms`, `evidence_basis`, `risk_flags`。

不要输出自由文本过滤规则或任何替代字段。must-have、optional、exclusion 与 query 组合由 Retrieval Strategy Skill 根据完整约束语义判断；确定性代码不得从自然语言重新推断这些科研决策。

如需查看字段语义和边界示例，读取 `references/field-semantics.md`。

## 失败边界

- `user_query` 为空且无其他约束来源：返回空约束，并在 `missing_inputs` 写入 `user_query` 与 `constraint_sources`；
- 有旧字段 `raw_constraints[].original_phrase` 时按兼容路径读取；
- 无法确定解释时保留未知和歧义，不编造一个“可执行”答案；
- 始终返回合法 JSON；结构错误不能用科研猜测掩盖。
