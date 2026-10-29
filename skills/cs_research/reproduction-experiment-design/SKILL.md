---
name: reproduction-experiment-design
description: 结合正式复现基线、可追溯执行事实与当前项目边界，形成可执行、可核验且不把计划冒充执行事实的实验方案。
---

# 复现实验方案设计

输入中的 `research_goal` 是已确认 ResearchDesign.goal 冻结的目标与项目边界；`reproduction_baseline_and_criteria` 是正式基线；`execution_plan_facts` 是代码已核验来源、身份和结构的执行事实。把目标逐项映射到实现、数据、配置、协议、重复规则、结果来源和指标提取方式。

- `source_required_facts` 只记录 `execution_plan_facts.execution_plan_provenance` 中的官方来源要求；`system_selected_details` 只记录其中的系统选择。
- `project_constraints` 记录研究目标中已经确认的时间、算力、费用、权限和风险边界；未明确的边界保持未知。
- `intentional_deviations` 单独记录相对官方设定的有意偏离及其授权依据；不得混入系统选择。
- 执行字段只能逐项采用 `execution_plan_facts` 中的正式值。缺失、`plan_status=incomplete` 或没有该输入时，令 `status=blocked` 并保留对应未知，不得从基线数值或模型记忆补齐。
- 代码、revision、资源、环境或入口不明确时，省略对应可选字段或保留空数组，并令 `status=blocked`；不得用 `null` 冒充可执行输入。
- 没有资源绑定时省略 `execution_resource_binding_plan`；不要输出会被下游误认为一条实际绑定的空占位对象。
- 不声称仓库已获取、环境已建立、程序已运行或指标已测得。
- 不根据参考结果设计能够“凑出”该结果的参数，不补造 tolerance。
- 只有目标映射、正式执行输入和当前项目授权边界都足以支持真实运行时才输出 `status=ready`。
