---
name: variable-identification
description: 将待验证假设、方法想法或实验目标拆成自变量、因变量、控制变量、对照条件、测量方式和约束；用于验证方案设计时必须输出变量表、混杂因素、最小可验证版本和人工确认门，不启动实验或训练。
---

# 变量识别与验证设计骨架 (L3)

## Input

接受以下输入：

- 待验证假设、研究问题、方法改动、材料体系、模型方案或实验想法。
- 已知条件：样本、数据、材料、设备、代码、指标、资源、时间、伦理或安全约束。
- 用户已有的对照、baseline、测量指标、失败记录或预期结果。

如果假设或目标不清楚，先输出变量澄清问题，不直接设计完整实验。

## Steps

1. 抽取待验证命题，写成“改变 X 是否影响 Y，在条件 C 下如何衡量”的形式。
2. 识别变量：
   - independent_variable：可操纵因素、取值范围、单位、水平数。
   - dependent_variable：测量结果、指标、采样频率、成功标准。
   - control_variable：必须保持一致的条件、批次、样本、环境、模型版本。
   - confounder：可能干扰解释的因素和控制办法。
3. 设计最小可验证版本：最少样本/运行、必要对照、可接受失败标准。
4. 标注对照和 baseline 需求；如果需要后续 baseline_selection 或 ablation_design，明确输入。
5. 标注不能自动执行的内容：实验启动、训练任务、平台资源、下载数据、伦理审批。

## Output

输出结构必须包含：

- `variable_table`：变量 ID、类型、名称、取值/范围、测量方式、关联假设。
- `controls_and_baselines`：必要对照、候选 baseline、公平比较条件。
- `confounders`：混杂因素、风险、控制方式。
- `minimal_validation_plan`：最小可验证步骤、成功标准、停止条件。
- `confirmation_gates`：需要用户确认的资源、许可、安全、伦理和成本。

## Scenario Artifact Contract v2

For validation design, produce a design-review artifact, not an experiment
result. Include these sections:

1. `testable_hypothesis`: one sentence in the form "Under condition C,
   changing X is expected to affect Y through/measured by Z".
2. `variable_table`: `variable_id`, variable type, name, operational
   definition, level/range, measurement method, unit, timing, acceptable range
   or threshold when known, and linked hypothesis.
3. `controls_and_baselines`: control condition, baseline method/material/model,
   fairness condition, what is held constant, and why each is necessary.
4. `confounders`: `confounder_id`, risk, how it could change interpretation,
   control strategy, and residual risk.
5. `ablation_or_comparison_matrix`: comparison ID, changed factor, held-constant
   factors, expected interpretation, and failure mode.
6. `analysis_plan`: metric, statistical/comparison method, exclusion rule,
   success criteria, failure criteria, decision threshold, and uncertainty
   reporting.
7. `stop_conditions`: conditions that should stop, narrow, or redesign the
   validation before spending resources.
8. `confirmation_gates`: resources, safety, ethics, data license, cost,
   platform, and owner confirmations.
9. `execution_status`: always `not_started` unless an external executor has
   actually run and returned records.
10. `missing_inputs_or_assumptions`: missing details that materially affect
   validity, such as additive identity, electrode chemistry, cell format,
   concentration range, sample count, measurement equipment, dataset/corpus,
   baseline implementation, cost, safety, or license constraints.
11. `provisional_parameters`: any concrete value, threshold, sample size,
   p-value, concentration level, C-rate, temperature, prompt set size, dataset
   size, or characterization method that was not supplied by the user. Mark
   each item with `requires_confirmation: true` and explain why it is only a
   design placeholder.

If the hypothesis is too vague, output `clarification_questions` and
`minimum_viable_validation_inputs` instead of a full design. Still provide a
minimal design skeleton when possible: `bounded_hypothesis_placeholder`,
`variable_slots`, `candidate_controls`, `cannot_execute_until`, and
`next_user_actions`. Mark each unknown explicitly rather than inventing a
protocol. Do not claim that the validation has been run or that the hypothesis
is supported.

Avoid generic experiment advice. A validation artifact is useful only if a
researcher can review feasibility: list missing dataset/material access,
baseline fairness risks, sample/prompt-set readiness, resource/safety/license
gates, and the conditions that would make the design unable to answer the
hypothesis.

Do not silently invent an executable protocol. If you include default values
such as `n >= 3`, `p < 0.05`, additive concentration levels, C-rate, cycle
count, XPS/FTIR/cryo-TEM, dataset size, or prompt-set size without those values
being present in the input, label them as provisional design assumptions, not
as confirmed requirements.

When the user asks to run an experiment but has not provided executable
materials, credentials, data, safety approval, or a concrete protocol, fail
closed as `design_only`. A high-value refusal should still be useful: provide
the minimum validation skeleton, the exact inputs required before execution,
and the next 1-3 user actions. Do not reduce the answer to only a generic
clarification checklist.

For retrieval, GraphRAG, LLM, benchmark, or hallucination-reduction validation,
include dataset-readiness sections when the user has no dataset yet:
`minimum_viable_dataset_or_prompt_set`, `evaluation_asset_plan`,
`annotation_rubric`, and `baseline_fairness_checks`. These should specify the
source corpus snapshot, prompt/topic sampling frame, hallucination label schema,
judge or annotator owner, agreement or adjudication plan, baseline-vs-treatment
matching rules, and what must be confirmed before any run. Do not imply that a
missing dataset can be skipped.

## Fail Closed

- 不自动启动实验、训练、仿真、外部平台或资源消耗。
- 不把无法测量的变量写成已可验证方案。
- 缺少关键变量或指标时，输出澄清问题和最小补充输入。
