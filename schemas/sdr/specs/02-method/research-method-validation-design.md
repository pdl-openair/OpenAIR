# SDR 研究方法与验证设计

## 用户价值

形成通用研究方法、数据、变量、基线、分析、复现和验证协议。

## Input
- `research_problem_framing`

## Output
- `research_method_validation_design`

## Acceptance
- `method_decision_aligns_to_research_question`
- `data_variables_controls_and_baselines_explicit`
- `analysis_and_uncertainty_plan_explicit`
- `reproducibility_requirements_explicit`
- `ablation_only_when_applicable`
- `unconfirmed_threshold_sample_resource_values_marked_provisional`
- `design_and_real_acquisition_execution_separated`
- `stop_and_confirmation_gates_explicit`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`evidence_review`、`available_data_resource_facts`、`candidate_method` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

必要设计输入不足时返回 needs_input / provisional；不得把计划写成已下载、已安装或已运行。

## 来源与重入边界

方法、数据、基线、变量和阈值链接问题、证据或明确假设；研究问题、约束、数据/资源事实或方法变化时重入。
