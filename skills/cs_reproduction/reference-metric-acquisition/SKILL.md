---
name: reference-metric-acquisition
description: 论文复现 Workflow 的参考结果获取与结构化研究工作：复用已经固定的官方代码来源，形成与当前复现条件匹配、可追溯且独立于 actual metrics 的 reference facts。
---

# 参考结果获取与结构化

只消费上一研究工作已经形成的代码身份 Artifact 与当前复现研究目标，不重新选择 repository/revision，也不读取 actual metrics。

本节点按正式 composition 依次完成：读取并复核已固定官方来源、由 LLM 判断实验条件与指标语义、由代码确定性核验引用和冲突并形成 `reference_metric_*` ScientificState facts。来源没有明确支持 comparison rule 或 tolerance 时保持未知，不补造。
