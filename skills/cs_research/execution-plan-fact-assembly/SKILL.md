---
name: execution-plan-fact-assembly
description: 确定性核验来源引用并组装可交给既有复现后半链的正式执行计划事实。
---

# 复现执行方案确定性组装

本 Skill 不做科研语义猜测。它只：

- 核验模型声明的每条官方来源 excerpt 在固定文档中唯一且逐字存在；
- 生成稳定 `execution_plan_id`；
- 将多份资源分别组装成现有 `resource_identity_acquisition` 可消费的请求；
- 将正式 `reference_metric_records` 机械投影成实际指标提取请求，模型不能改写 metric identity；
- 将来源未指定且纯属执行工程选择的 Python 小版本、仓库根目录、超时等标记为 `system_selection`；
- 将执行记录路径、资源正式记录、运行后文件 SHA256 等尚未产生的事实标记为 `runtime_pending`；
- 将无法可信确定的必要事实保留在 `execution_plan_unresolved`，使 `plan_status=incomplete`。

任何单条无效资源或引用只影响该条候选，不能覆盖其他已经核验的正式事实。
