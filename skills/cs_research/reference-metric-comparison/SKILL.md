---
name: reference-metric-comparison
description: 在上游已经明确提供 structured actual metrics、reference metrics 和逐指标 comparison rule 后，按精确 metric identity 确定性比较实际结果是否满足预定义标准，并保留逐项计算依据。
---

# 参考指标确定性比较

## Product Role

本 Skill 服务于 CS 论文复现中的“真实结果 → 预定义标准判定”阶段：

`structured actual metrics + explicit reference metrics + explicit comparison rules → deterministic comparison result`

它只回答：上游已经明确参考结果和比较规则之后，本次真实实验的 actual metrics 是否满足这些预定义规则。

## Input Authority

上游必须显式提供：

- `actual_metrics`：`openair-actual-metrics/v1` 的结构化实际指标结果；
- `reference_metrics[]`：每个待比较指标的精确 `metric_name`、`reference_value`、`value_type`、`unit`；
- 每个 reference metric 的 `comparison_rule`。

指标对应只使用**完全相同的 `metric_name`**。大小写、空格和标点都属于身份的一部分。

禁止 fuzzy match、同义词推断、相似名称回退和 LLM 语义匹配。

## Minimal Comparison Rules

当前只支持论文复现中最小必要规则：

- `exact`：actual 与 reference 完全相等；
- `absolute_tolerance`：`abs(actual - reference) <= tolerance`；
- `relative_tolerance`：`abs(actual - reference) <= abs(reference) * tolerance`；
- `actual_gte_reference`：`actual >= reference`；
- `actual_lte_reference`：`actual <= reference`。

`tolerance` 必须由上游显式提供、为有限非负数。`0.05` 表示相对容差 5%。reference 为 0 时，relative tolerance 的允许差值也是 0，不使用除法或特殊猜测。

`exact` 支持 number / integer / string / boolean；其余规则只支持 number / integer。

## Deterministic Semantics

1. actual 与 reference 的 `metric_name` 必须精确一致且唯一。
2. reference 中重复 identity、actual 中重复 identity 都显式失败，不选择任意一个。
3. actual 与 reference 的声明 `value_type` 必须完全一致。
4. unit 必须完全一致：两边同时为 `null`，或使用完全相同的非空字符串；不自动换算、不规范化单位。
5. actual metric 必须是成功提取且具有类型匹配值与 `source_trace` 的可信值；缺失、上游提取失败、值非法或 provenance 缺失时只让该项比较失败，其他可信指标继续比较。
6. 数值比较使用十进制定点语义执行，避免二进制浮点误差改变边界结果。
7. 单项失败不抹掉其他已完成比较；但任一单项无法比较时，顶层 `comparison_status=incomplete`、`state=failed`、`overall_result=indeterminate`。
8. 所有指标都完成比较时，顶层 `state=succeeded`；全部满足规则则 `overall_result=meets_standard`，任一不满足则 `overall_result=does_not_meet_standard`。
9. “系统完成比较”和“实验是否达标”分开表达；实验未达标不是比较执行失败。

## Output

每个 comparison result 至少保留：

- `metric_name`；
- actual value / type / unit / extraction status / source trace；
- reference value / type / unit；
- 规范化后的 comparison rule；
- `comparison_status`；
- `meets_rule`；
- `calculation_trace`，包括真实操作数、比较运算，以及数值规则下的 delta / tolerance / allowed delta。

顶层同时返回：

- requested reference metric 数量；
- 成功完成 comparison 的数量；
- comparison 是否完整；
- 整体是否满足预定义标准。

## Failure Semantics

至少显式区分：

- actual metric 缺失；
- actual metric identity 重复 / 歧义；
- reference metric identity 重复 / 歧义；
- actual metric 上游提取失败；
- actual/reference value type 冲突；
- unit 冲突；
- actual/reference 值与声明类型不符；
- 成功 actual metric 缺少可信 `source_trace`；
- 非法 comparison rule（包括非对象输入）；
- 非法 / 缺失 tolerance；
- 对不支持的值类型使用数值规则。

## Responsibility Boundary

本 Skill 不负责：

- 阅读论文、README 或网页寻找 reference；
- 决定应该比较哪些指标；
- 自行决定 tolerance；
- baseline 方法选择；
- 统计显著性或科研解释；
- 完整复现凭据聚合；
- Artifact / checkpoint 平台；
- Workflow、Binding、worker、runtime registry。

`baseline-comparison` 继续负责选择和配置实验对比基线方法；本 Skill 不接管其职责。

生产实现不得包含任何论文、benchmark、模型或案例专有判断。

## CS v2 Comparability Gate

CS 论文复现链使用 `actual_metric_records` 和 `reproduction_comparability_assessment`。有效比较的决定性条件是：可比性 `status=comparable`、八类条件均为 `compatible` 且各自带有真实证据、并且 `condition_mismatches` 为空。只有这些条件成立时才执行确定性规则计算；任一关键条件为 `unknown` 或 `mismatch`、缺少证据，或 assessment 为 `indeterminate` / `not_comparable` 时，不计算阈值结果，顶层保持 `indeterminate`。

顶层 `unknowns`、记录 `status=incomplete`、非空 `partial_failures` 不能单独否定全部实测指标，也不按未知文本判断科学重要性。这些未知和局部失败留在上游记录里，比较时逐个读取所请求的合法实测项。

`reference_values_used` 必须为 `false`，且 `execution_record_ref` 必须是可追溯的非空文本。缺少这两项全局测量来源时，不能把结果当作本次实测比较。

逐项仍独立检查身份、值类型、单位、提取状态、`source_trace`、比较规则和重复身份。成功项按既有公式计算；失败项保留既有逐项失败记录。同一身份歧义不选取任意一条，也不丢掉其他身份的有效记录。任一请求指标无法比较时，顶层仍是 `comparison_status=incomplete`、`overall_result=indeterminate`。

全部已声明请求指标都合法完成且规则满足时，`meets_standard` 只覆盖该已声明比较范围，不能据此声称论文整体复现通过。

通用 SDR 的 `actual_metrics` 入口、公式和状态/计数语义保持不变。
