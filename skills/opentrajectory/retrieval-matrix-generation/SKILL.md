---
name: retrieval-matrix-generation
description: >-
  当用户或研究流程需要“生成检索矩阵”“展开检索词”“生成 Boolean query 变体”或“制定文献检索策略”，
  且上游已提供正式 `problem_framing` 时使用。把已确认的研究对象、约束、方法、机制、指标、来源范围与明确排除项
  转成英文、可执行、互补且可追溯的检索矩阵；不执行检索、不评价论文，也不补造上游未确认的科研事实。
---

# 检索矩阵生成

把上游正式问题建模转换为**英文检索策略语义草稿**。目标是让后续检索尽可能找全关键文献，同时保持用户问题、约束、Boolean 语义和来源边界不失真。模型只负责科研语义判断；正式 7 行结构、计数、覆盖列表、引用汇总和 `query_facets` 由已登记的确定性组装器完成。

## 职责边界

只完成检索策略设计：

- 组织检索主题、约束、方法/机制/指标和明确排除项；
- 生成互补的 Boolean query；
- 为下游保留约束利用情况和稳定 facets。

不要执行数据库检索、下载/筛选/排序论文、评价证据质量、生成候选论文池或重新解释上游已经形成的科研判断。

正式生产输入优先使用同一个 `problem_framing` Artifact 的两个视图：`intent_understanding` 与 `constraints_criteria`。从 `intent_understanding` 读取原始问题与真实歧义，从 `constraints_criteria.interpreted_constraints`、`missing_information` 和 `research_risks` 读取正式专业判断。不得重新解释已经冻结的问题事实。

Schema 仍兼容旧的直接调用形态 `user_query + interpreted_constraints`，仅用于历史/独立 Skill 调用；正式 OT Workflow 绑定使用 `intent_understanding + constraints_criteria`。旧输入可能携带已废弃的自由文本过滤规则；必须整字段忽略，不读取其内容，也不得让它改变 query 强度、排除项或执行结果。约束数组为空时保守生成，并在 `validation_flags` 标记 `input_constraints_empty`。不要新增 Schema 中不存在的状态字段。

## 一条总原则：先守来源，再扩召回

每个进入 query 的概念都要能追溯到以下一种合法来源：

1. 用户明确表达的研究对象、条件、比较对象、方法、机制、指标或排除项；
2. 上游 `interpreted_constraints[].downstream_search_terms` 中与用户已确认语义一致的英文表达；
3. `seed_terms` 或 `research_context` 中的明确英文检索事实；
4. 对上述已确认概念的**直接同义词、标准缩写、标准术语变体或紧邻的上/下位术语**。

不要仅因为“领域里常见”就加入具体材料、方法、benchmark、阈值、数据库或实验条件。不要使用领域专用默认词表。

### 未确认信息

正式 Workflow 输入读取 `constraint_name`、`original_phrase`、`interpreted_value`、`candidate_values`、`semantic_state`、`domain_rationale`、`evidence_basis`、`downstream_search_terms`、`missing_information` 与 `research_risks`，由本 Skill 判断某个概念应成为 must-have、optional、exclusion 还是 query 变体。旧的独立直调输入仍可包含历史字段，但废弃字段没有任何 Authority。

- `candidate_values`、ambiguity 与 risk 是 Retrieval Strategy 的判断输入，不能被代码或字段存在性自动升级为硬政策；只有模型结合完整正式语义后才能决定其检索用途。
- `semantic_state` 为 `unknown`、`ambiguous` 或 `conflicting` 时，保留不确定性；不要仅凭状态标签或候选值存在就静默缩窄核心检索。
- `research_risks` 只表示专业风险；不得转成 query term、NOT 或 `exclude_terms`。
- 数值、benchmark、材料/系统子类等具体项，只因上游 search terms 中出现并不自动成为硬条件；结合原始表达、正式语义状态、解释依据和风险说明判断其强度。

## 约束强度

把“概念是否可信”与“是否必须检索命中”分开判断。

- **must-have**：用户明确要求、明确比较基线、明确硬边界，且加入 AND 会真实缩小到用户目标。
- **optional**：用户偏好、软条件、同义扩展、可提高召回但不应排除其他相关文献的条件。
- **unsupported**：未确认候选、仅由风险/缺失信息推出来的内容；不要进入正向 query。

不要把 `mechanism`、`performance`、`test`、单独的 `method` 等泛词当作 must-have。只有在它们本身就是用户研究对象时才可直接使用。

## 固定 7 行不等于硬凑 7 类内容

正式输出仍按合同保留以下 7 个 `search_intent`，每个恰好一行；语义草稿只返回有来源支持的 `strategies`，不必硬凑空行：

1. `core_topic`
2. `constraint_focused`
3. `mechanism_focused`
4. `method_or_strategy`
5. `metric_or_performance`
6. `adjacent_but_relevant`
7. `noise_exclusion`

把它们视为**结构槽位**，不是要求每个输入都天然存在七类科研事实。

- 有合法来源时才在 `strategies` 中填入对应 terms。
- 没有支持该 intent 的事实时省略该 strategy；确定性组装器会生成空槽位及中性说明，不会补造科研词。
- 不要为了填满 `mechanism_focused`、`method_or_strategy`、`metric_or_performance` 或 `adjacent_but_relevant` 而猜词。
- `adjacent_but_relevant` 只使用与已确认概念直接相邻、能提高召回且不会改变研究对象的标准术语；没有可靠相邻扩展时保持为空。
- `noise_exclusion` 只承载本 Skill 根据完整 Problem Framing 语义确认的明确排除项；不得把 candidate、ambiguity 或 risk 自动升级为 exclusion。
- priority 1 用于核心主题、明确关键约束和明确比较；priority 2 用于有依据的方法/机制/指标扩展；priority 3 用于相邻召回、明确排除和保守 fallback。
- `source_constraints` 只记录本行/本 query 实际使用的上游 `constraint_name`，必须逐字匹配；仅来自 `user_query` / `seed_terms` 的内容不要虚构 constraint 名。

各 intent 的用途和 Boolean 示例见 `references/intent-guide.md`。

## 来源范围与过滤语义

把 recency、language、source/venue quality、paper type、target database 等**来源范围**与主题 query 分开。

- 不要把 `English`、年份窗口、`peer-reviewed`、`high-quality journal` 等当成主题词塞进 Boolean query。
- 把实际采用的来源范围通过相应 strategy 或 query 的 `source_constraints` 关联到正式约束名；`quality_report.constraints_utilized` 由确定性组装器汇总。
- 只有用户或上游明确指定数据库时设置 `target_database`，否则为 `null`。
- 只有用户或上游明确指定论文类型时设置 `expected_paper_type`，否则为 `null`。

## 明确比较任务

用户明确要求 A 与 B 对比时，把 A、B 和共同任务/对象都视为核心检索事实。至少一个 priority 1 query 保留该比较关系；不要把明确 baseline 降为 adjacent 或纯 optional。

## Boolean query 生成

当前正式 Schema 接受 **1–16 条**可用 query。优先形成少量高价值、互补的查询；不要为了固定数量凑数。局部 query 失败时，其余查询继续使用。

按实际需要覆盖核心入口、关键约束、方法/机制、指标/评价、比较、相邻召回和明确排除。输入稀疏或生成查询均不可用时，系统使用未经改写的原始用户问题作为保底入口。不要为数量发明新科研 facet。

Boolean 规则：

- 多词短语加双引号：`"fast charging"`。
- 同一概念的同义词用 OR 并显式括号：`("canonical concept" OR "accepted synonym")`。
- 不同必须概念用 AND：`("A" OR "A synonym") AND "B"`。
- optional OR 块前也必须有 AND。
- 只有明确排除项才能使用 `AND NOT (...)`。
- 同时出现 AND/OR/NOT 时始终显式分组，不依赖检索后端默认优先级。
- 不使用隐式相邻表达，如 `"A" ("B" OR "C")`。

优先按**概念簇**组 query，而不是为每个同义词各生成一条完整 query。大小写/空白归一化后不得重复；语义作用相同、只替换一个近义词的 query 也视为重复，应合并。

## 检索表达与简洁性

所有 `query_terms`、`must_include_terms`、`optional_terms`、`exclude_terms` 和 `expanded_query_variants[].query`：

- 使用当前检索后端可执行的表达；语言本身不能成为 Runtime 阻塞条件；
- 不把完整用户问题逐句翻译成一个长 query；
- 不含规则句、JSON 残留或对象字符串化残留；
- 单个 term 尽量短，通常不超过 8 个英文词；
- rationale 每条只写一句，解释该 query 与哪类已确认事实对应，不写报告。

## facets

从本次已经使用的检索事实归纳语义草稿中的 `facets`，不要引入新事实。确定性组装器会将这些内容映射为正式 `assembled_facets`：

- `core_entities` / `research_object_en`：核心研究对象；
- `mechanisms`：只有输入明确支持的机制；
- `target_metrics_en`：只有输入明确支持的指标；
- `task_context`：任务/场景边界；
- `must_have_terms`：与 strict 语义一致的已确认硬检索概念；
- `nice_to_have_terms`：软偏好、同义扩展和可靠相邻扩展；
- `negative_contexts`：仅明确排除项；
- `evaluation_terms`：评价或比较维度；正式 `query_facets` 会按 object / mechanism / metric / context / evaluation 复用这些已支持概念。

允许数组为空。不要因为字段存在就补造内容。

## 质量检查

返回单个、符合 `model_output.schema.json` 的 JSON 对象前，逐项检查：

- `strategies` 只包含有来源支持的 intent，同一 intent 不重复；
- `expanded_query_variants` 至少包含 1 条可用查询，不为固定数量凑数；
- 每条 query 的 `search_intent` 都在 `strategies` 中存在；
- 核心研究对象和用户明确关键约束至少进入一个高优先级 query；
- 未确认 candidate 没有进入 strict / must-have；
- `risk_flags` 没有进入 NOT / exclude；
- 没有发明系统、阈值、数据库、benchmark 或材料/方法子类；
- AND/OR/NOT 分组明确；
- query 不含内部标签；finalizer 保留模型提供的科研内容并组装正式结构，仅保留已有必要 key 检查；
- 明确来源范围没有伪装成主题 query；
- 非材料任务没有材料/电池专用词污染；
- `facets` 只复用已有支持概念。

信息不足时保持宽泛、按规则省略没有依据的 strategy，并在必要时使用 `fallback` query；确定性组装器按提供的策略与查询生成数量统计，不补造科研内容或质量判断。不要用更具体但无依据的 query 掩盖未知。
