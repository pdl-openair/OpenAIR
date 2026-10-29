# Solution Design -- Experiment Design

<!-- Complete experimental design including environment, algorithms, metrics -->

## 用户价值

把已确认问题转化为有对照、可复现、可验收的 OPE 实验设计。

## Input
- `question_formulation`

## Output
- `experiment_design`

## Acceptance
- `variables_controls_and_baselines_explicit`
- `run_matrix_and_metrics_align_to_research_question`
- `statistical_and_uncertainty_plan_source_grounded`
- `reproducibility_requirements_explicit`
- `resource_and_decision_gates_explicit`
- `provisional_values_marked_when_unconfirmed`
- `design_not_recorded_as_execution`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`evidence_review`、`available_data_resource_facts`、`existing_implementation_facts` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

关键信息不足时输出 provisional / needs_input；不得声称实验已经运行。

## 来源与重入边界

每项设计选择链接 research question、hypothesis/evidence 或约束；问题、数据/资源事实或验证目标变化时重入。

## Environment & Data

<!-- Datasets, simulators, data collection strategy -->

## Algorithms & Baselines

<!-- Algorithms to compare, baseline methods -->

## Evaluation Metrics

<!-- Primary and secondary metrics, statistical tests -->

## Experimental Matrix

| # | Algorithm | Environment | Metric | Expected Outcome |
|---|-----------|-------------|--------|-----------------|
| <!-- fill --> | | | | |

## Reproducibility

<!-- Seeds, hardware, version requirements -->
