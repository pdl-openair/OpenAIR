---
name: reproduction-record-assembly
description: 将已形成的 CS 复现正式记录做确定性来源链核验并组装为单一、紧凑、可审计的 reproduction record；不重新执行任何上游科研或运行职责。
---

# 完整复现记录确定性组装

## Goal

把已经由上游正式能力形成的复现事实组装成一个稳定的 `openair-reproduction-record/v1`，让下游能够明确区分：

1. 本次记录组装是否成功；
2. 当前复现凭据是否完整；
3. 在完整正式 comparison 存在时，实验是否满足上游预定义 reference standard。

本 Skill 完全确定性，不调用 LLM。

## Input Contract

输入固定包含六个槽位：

- `code_identity`：`repository-revision-acquisition` 正式成功输出，可为 `null` 表示当前正式记录缺失；
- `resource_identities[]`：`0..N` 个 `resource-identity-acquisition` 正式成功输出；空数组合法；
- `environment_record`：`openair-environment-record/v1`，可为 `null`；
- `execution_record`：`openair-execution-record/v1`，可为 `null`；
- `actual_metrics`：`openair-actual-metrics/v1`，可为 `null`；
- `reference_comparison`：`openair-reference-comparison/v1`，可为 `null`。

`null` 表示正式上游结构化记录当前不存在，因此可以形成 `incomplete` record。已提供但结构非法、身份冲突或来源链冲突则失败关闭，不按“缺失”处理。来源证据的读取形式尚未支持或 `repository_url` 缺失是可隔离的证据缺口：接收其有界原始材料，不认定为正式有效证据，不因此废弃已核验的代码与其他成果。

## Deterministic Source-chain Checks

至少执行：

- code identity 必须是成功正式输出，`resolved_commit_sha == verified_head`；
- code identity 的可选 `source_evidence` 保留正式来源字段、读取形式与论文身份；正式字段约束引用仓库获取合同，不另设证据 authority。若证据声明仓库，必须与 code identity 一致；未知字段、非法类型或无法核验的 Git 身份失败关闭；
- 未支持的非空读取形式（如 `fulltext`）或缺少证据 `repository_url` 时，不猜测具体格式、不拿代码仓库地址冒充来源声明：原样保留到 `code_identity.source_evidence_review.received`，标记 `status=unverified` 与明确 `issues`；此时不得输出已核实的 `source_evidence`。缺口进入 `missing_components` 的 `source_evidence` 项，其余可信投影继续组装；
- 每个 resource identity 必须是成功正式输出，materialized identity 的 commit 必须等于 resolved commit；
- 正式 Resource 存在时，逐份检查 execution 中的计划资源、受控交付和可重新读取核验的实际消费证据；直接路径交付需核对实际 argv，归档展开交付需重新核对原始归档 SHA256、持久化交付清单及实际读取文件是否属于该清单；任何 delivery 本身都不能把状态提升为 `verified`；
- Resource 已交付但 execution 缺少实际消费证据时记为 `missing_evidence`；消费证据确认实际使用另一资源时记为 `mismatch`，不交给 LLM 推断；
- environment record 必须为 `openair-environment-record/v1`，并按该版本正式算法重新核对 fingerprint；
- execution record 必须为 `openair-execution-record/v1`；
- environment 与 execution 同时存在时，环境路径和 fingerprint 必须一致；
- actual metrics 中的 execution-record path 是 assembly 的正式 provenance 上下文；不要求 canonical execution record 本体重复该路径字段；
- actual metrics 声明 execution SHA256 时，必须按其 provenance path 读取当前受控 workspace 中真实 execution record 文件核对 SHA256；若同时提供 execution record，其内容也必须与磁盘正式记录一致；
- actual metrics 声明 result source SHA256 时，必须从上述磁盘 execution record 的完整 stdout/stderr，或受控 workspace 中声明的正式结果文件重新读取真实来源字节并独立计算 SHA256；来源不可用、不完整或摘要不一致均失败关闭；
- 成功 actual metric 必须保留与 result source 一致的 `source_trace`；
- comparison 存在时，其 actual 投影必须与当前 actual metrics 的同名事实完全一致。

不做 fuzzy match、自动修复、科研解释或 comparison 重算。

## Output Contract

输出 `openair-reproduction-record/v1`：

- `state = succeeded | failed`：只表示组装与确定性核验本身是否成功；
- `record_status = complete | incomplete`：表示正式复现凭据是否完整；
- `reproduction_result = meets_standard | does_not_meet_standard | indeterminate`：只有完整正式 comparison 存在且整条 record 完整时，才投影上游 comparison 的最终结果；
- `missing_components[]`：明确当前仍缺的正式组件或完整结果；
- `resource_usage_verification`：独立表达 `not_applicable | verified | missing_evidence | mismatch`，并保留逐 Resource 的确定性检查结果；
- 紧凑的 code / resource / environment / execution / actual metrics / comparison 投影。

`reference_comparison=null` 时必须得到 `state=succeeded`、`record_status=incomplete`、`reproduction_result=indeterminate`，并包含 `missing_components=[..., "reference_comparison"]` 中对应缺项。Resource 使用证据缺失时同样保持已经发生的 execution，但不得形成可信完整记录；Resource mismatch 作为已确认事实保留，`record_status` 保持 `incomplete`、`reproduction_result=indeterminate`。

来源证据待核实同样得到 `succeeded/incomplete/indeterminate`，保留代码 commit、资源、环境、执行、实际指标及 comparison 已有事实；即使 comparison 已给出达标判断，也不将缺口包装为完整正式复现结论。`source_evidence_review` 仅为本次原始材料与未核实状态的诊断投影，不产生新的来源事实；后续系统须依原始来源补证，不向用户索要系统能够自行恢复的事实。本能力不重新读取网络来源，不声称已完成自动补证或 Workflow 恢复。

## Must NOT

- 不重新解析 Git revision；
- 不下载资源；
- 不建环境；
- 不执行实验；
- 不重新提取 actual metrics；
- 不重新执行 reference comparison；
- 不推断或补造 reference；
- 不复制 stdout/stderr 或完整环境 package 清单到最终 record；
- 不创建 Artifact catalog、对象存储、版本、lineage 或 checkpoint/file acquisition 平台；
- 不修改 shared runtime、worker、Workflow、Binding 或 Artifact 平台。

未来 Runtime / Artifact 正式集成应由 OpenAIR 现有 Artifact/provenance 平台承接；本 Skill 当前只定义并实现 CS-S 自身的稳定 reproduction record。
