---
name: execution-readiness-assessment
description: 核验真实代码、资源与环境证据，形成有明确范围和缺口的执行准备结论。
---

# 执行准备证据核验

计划代码身份只能取自 `execution_plan_facts.source_identity`。实际代码身份只能取自 `code_identity`（`repository_revision_acquisition`）；两者关系只有经过确定性核验为 `verified` 才能写成 `ready`。实验方案中的仓库、revision 和 requested revision 均不能替代正式计划。代码、资源和环境字段只能来自输入中的正式执行结果；缺少结果或结果失败时记录 `blocking_gaps`。

`ready` 只证明列入 `executable_scope` 的实验具备执行条件，不代表程序已经运行或指标已经产生。保留所有未知与部分失败。
