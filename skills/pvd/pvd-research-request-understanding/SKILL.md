---
name: pvd-research-request-understanding
description: >-
  Understands one PVD research request as one scientific semantic task and emits canonical structured context, dynamic recommendation parameter scope, run preferences, and query-derived experiment facts without retrieval or recommendation.
---

# PVD 研究请求理解

把当前 `query` 作为唯一业务输入，一次完成 PVD 科研语义理解。最终消息只有本说明和 `scientificInput.query`，没有另附 response schema。只返回一个 JSON 对象，承载下列科研判断；稳定身份与机器字段由已声明 finalizer 恢复。没有对应事实时相关数组为空，不编造 quote，也不填默认授权。

- `research_facts`：数组。每项含 `kind`（objective / process_method / current_problem / baseline / existing_result / judgment_criterion）、`text`，以及 query 中的逐字 `quote`。
- `target_metrics`：数组。每项含 `name`、`direction`（maximize / minimize / target / unknown）和 `quote`。方向不明时用 unknown。
- `materials`：数组。每项含 `role`、`value` 和 `quote`。
- `system_facts`：数组。每项含 `field`、`value`、`unit`、`unit_identity`、`unit_dimension` 和 `quote`。单位不可靠时后三个单位字段为 null。
- `parameter_mentions`：数组，字段含义见下文动态参数身份。
- `existing_experiments`：数组，字段含义见下文历史实验规则。
- `unknowns`：数组。每项含 `field` 与 `reason`。没有关键未知时为空数组。
- `initial_experiment_set_count`：用户明确要求的组数；未明确时为 null。

模型负责：
- 研究目标、目标指标/方向、工艺方法、材料与当前系统事实；
- 动态参数身份：`active` 或 `conditional`；参数 `key/label/value_type/unit/unit_identity/unit_dimension`；
- 当前 AlN / AlSc 演示中的四个核心参数稳定身份固定为 `power`、`n2_flow`、`ar_flow`、`deposition_time`；这些身份在 `parameter_mentions`、observations、constraints 和 `existing_experiments.parameters` 中必须一致，即使该参数不在当前 active scope 中，也不得改名为 `ar_flow_rate` 等同义 key。
- 其他动态参数使用一个简洁、稳定的 `snake_case` key，并在同一科研语义输出中始终复用；`parameter_key` 必须同时保留用户明确的物理对象和属性，单位、量纲相同或 Schema 中存在另一个字段均不构成同一身份，不得将一个物理对象的属性改写为另一个对象的属性；参数身份由完整科研语义决定，不根据关键词、regex、字符串 alias 或表格列位置猜测。
- `value_type` 仅可为 continuous / integer / categorical / boolean；categorical 的 `allowed_values` 只能来自用户明确候选，其他类型必须为空数组；
- 数值语义区分当前/观察事实与用户明确 hard constraint；不得把常识或设备经验写成边界；
- 条件参数的 condition；它仍是待准入参数，不属于正式 active scope；
- 同一 query 中计划实验与已完成物理实验的区别；只有真实测量才进入 measurements；
- 只列会影响后续科研判断的关键 unknown。基础系统字段缺失由程序表达，不需要把所有缺失都列成 unknown。

证据规则：
- 每个 `quote` 必须是 query 中逐字连续子串；不得改写或补造来源。
- 系统事实字段使用清晰稳定的语义名。五个核心身份字段为 target_material、substrate、target_substrate_distance、power_supply_temperature、target_angle。不要把 substrate_temperature 当成 power_supply_temperature。
- `unit_identity` / `unit_dimension` 只有可靠时填写；例如 `length:mm` + `length`。不可靠时填 null，不猜。
- 参数 condition/constraint 单条不可靠时仍保留其他可靠事实，不为凑结构修值、交换上下界或补默认。
- 未提供温度、压力、靶基距、设备上限时保持缺失，不生成任何数值。

历史实验规则：
- `completed_physical_execution` 只用于已真实执行；`planned_or_future` 只用于计划。
- 已执行参数和 measurement 必须来自 query 的明确事实；planned 不得转成 actual。
- 同一语义输出已经包含已有实验，不启动第二个历史解析任务。

参数数量偏好：用户明确要求 N 组时输出 N；未明确时 `initial_experiment_set_count=null`，由后续正式产品策略决定默认值。
