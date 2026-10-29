---
name: problem-framing-generation
description: 把用户科研问题一次冻结为研究意图与约束判断两个同源视图，供 OT 后续检索与证据工作直接使用。
---

# 研究问题建模生成

## 任务

读取 `user_query`，一次完成 OpenTrajectory 的正式问题建模。只返回 `output.schema.json` 允许的 JSON。

输出中的 `intent_understanding` 与 `constraints_criteria` 是**同一个问题建模 Artifact 的两个视图**。不要把它们当成两个连续任务，也不要重复推理同一事实。

## 先确定用户究竟在研究什么

在 `intent_understanding` 中保留：

- `original_user_query`：用户原话；
- `research_object`：核心研究对象或研究问题；
- `target_metrics`：用户明确关注的指标/结果；
- `boundary_conditions`：用户明确给出的研究边界和条件；
- `comparison_baseline`：仅在用户明确比较对象时填写；
- `output_intent`：根据用户明确需求选择；无法可靠判断时为 `null`；
- `requested_source_scope`：仅记录用户明确的来源/时间/语言/文献类型范围；
- `ambiguities`：会改变后续研究方向、检索边界或判断标准的真实歧义。

不要把未说出的材料、方法、阈值、benchmark、数据库或实验条件补进来。

## 同时解释会影响研究判断的约束

在 `constraints_criteria.interpreted_constraints` 中，只解释用户实际表达的条件。需要区分：

- 明确硬约束；
- 软偏好；
- 显式未知；
- 显式不限；
- 方向/含义存在歧义的条件；
- 彼此冲突的条件。

每条约束用 `semantic_state` 明确专业语义状态：

- `confirmed`：可从用户表达可靠确定；
- `unknown`：用户明确未知，或当前缺少形成该项判断所需的信息；
- `unrestricted`：用户明确表示不限；
- `ambiguous`：存在多个会改变研究方向或判断标准的合理解释；
- `conflicting`：与另一条明确条件不能同时成立。

规则：

- `original_phrase` 尽量保留用户原表达；
- `interpreted_value` 只写可从用户表达可靠得到的语义；无法确定时为 `null`；
- `candidate_values` 只能承载解释歧义所需的候选，不得把候选升级成用户事实；
- 未知保持未知，显式不限保持不限，冲突同时保留，不静默选边；
- `evidence_basis` 说明判断直接依据的用户表达，不引用不存在的外部证据；
- 不为约束或整体问题输出 `confidence`、`coverage_level` 等模型自评分数。

## 明确保留缺失信息与科研风险

`constraints_criteria.missing_information` 只记录真实缺失、且会影响后续科研判断的信息。每项必须说明缺什么、为什么需要，以及影响哪些正式约束。不要把待研究获取的结果本身误报成用户输入缺失。

`constraints_criteria.research_risks` 记录当前问题界定中真实存在且尚未消解的误检索、误筛选、错误比较或错误判断风险。每项必须说明风险、下游影响，以及关联的正式约束。

这两个数组是供检索设计与 EvidenceSchema 设计直接消费的正式专业字段，不是质量报告。不得新增 `quality_report`、`meta` 或类似汇总对象。

模型不输出 `needs_manual_review`、`ready`、gate 结论或其他工作流路由状态。系统代码只根据正式专业字段确定 gate 和路由；不得用模型自评替代该判断。

`downstream_search_terms` 只允许给出**直接来自正式约束语义**的简短检索表达；它不是完整检索策略。完整 query 组合、must-have / optional / exclusion、证据问题拆分、优先级和召回/精度权衡由后续 `retrieval_strategy_design` 独立决定。

不要输出自由文本过滤规则。约束强度、排除关系和不确定性只通过 `original_user_query`、`boundary_conditions`、`interpreted_constraints`、`original_phrase`、`interpreted_value`、`candidate_values`、`semantic_state`、`domain_rationale`、`evidence_basis` 与 `research_risks` 保真传递，不另造第二套执行 Authority。

## 跨场景要求

同一合同必须适用于材料科研和 CS/AI 科研，不得使用材料领域默认词表或领域专用假设污染其他场景。

特别注意：

- 数值出现但方向未知时，不猜“上限/下限/截止值”；
- 用户说“未知”与“不限”是两种不同语义；
- 用户给出互相冲突的条件时，保留双方并标记冲突；
- 用户明确比较 A/B 时，比较关系属于问题定义，不能丢失；
- 软偏好不能升级成硬过滤条件。

## 结束前检查

1. 研究对象、目标、输出意图和约束互相一致；
2. 两个视图没有把同一不确定信息一边写成未知、一边写成确定事实；
3. 没有补造用户未提供的科研事实；
4. 歧义、未知、显式不限和冲突均被保真；
5. 真实缺失信息与科研风险进入了各自的正式字段；
6. 没有输出模型自评、人工复核声明或其他路由状态；
7. 没有提前生成完整检索矩阵、最终研究报告或检索结论。
