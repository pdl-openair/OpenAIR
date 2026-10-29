---
name: new-task-planning
description: 引导用户澄清研究意图，判定 skill pack（sdr / ope / opentrajectory 等）并编排对应工作流（含 stage→substage→skill 链路），输出供用户确认。
---

# New Task Planning (Workflow Orchestration)

## Goal
在任务创建阶段完成：意图澄清、skill pack 判定、研究类型判断（M/D/C/H）、工作流编排（从对应 pack 的配置表实例化 stage→substage→skill 调用序列），输出供用户确认。

## Must Do
1. 先 Read `schemas/packs.yaml` 了解系统支持哪些 skill pack 及其适用场景。
2. 通过提问澄清用户研究目标、约束、输入数据、预期产出与评估标准。
3. 根据用户描述的研究范式匹配 pack.when_to_use。**必须遍历 `schemas/packs.yaml` 中的全部 pack**，不要只看下面列出的几个示例分支——新 pack 可能随时新增。当前已知触发规则（与 packs.yaml 的 when_to_use 保持一致，packs.yaml 是 single source of truth）：
   - `materials`：用户要做"多目标性能约束下的新晶体发现/制备/表征"，或提及 MP 检索 / MatterSim 演化 / Flux 生长 / Auto-XRD / 带隙 / 形成能 / ehull / 剥离能 / 二维材料 / 新晶体生成 等材料发现关键词 → 选 `materials`。**严禁把明显的材料任务落到 `sdr`**——即便用户没显式说"用 materials pack"，只要意图匹配 when_to_use 就必须选 `materials`。
   - `opentrajectory`：用户明确要求 OpenTrajectory，或需要从科研资料中生成可追溯证据包、机制链、科研线索、待验证假说 → 选 `opentrajectory`
   - `ope`：用户明确提及 OPE / Off-Policy Evaluation / 离线策略评估 / 强化学习评估 → 选 `ope`
   - `sdr`：仅当任务确实不匹配任何其他 pack 的 when_to_use 时（通用科研、未指定特定范式）才作为默认选 `sdr`
   - 若任务上下文（system prompt / 已有 task 字段 / 用户首条消息中显式指定的 pack）已存在非默认 pack 提示，且与用户意图一致，则尊重该值
   - **判定要点**：必须先读 `schemas/packs.yaml` 把每个 pack 的 `when_to_use` 与用户意图逐条对比，再下结论。不准凭"用户没说关键词就默认 sdr"偷懒——尤其材料任务误判为 sdr 是已知高发 bug。
4. 判断研究类型（M/D/C/H）。
5. 根据 chosen pack 读取配置：
   - `schemas/{chosen_pack}/stage-substage-map.yaml`（所有可能的 substage）
   - `schemas/{chosen_pack}/substage-skill-map.yaml`（每个 substage 可用的 L3 skill）
   - `schemas/{chosen_pack}/research_type_overrides.yaml`（该研究类型的必选/可选 substage）
6. **substages 必须逐字来自 chosen pack 的 `stage-substage-map.yaml` 的 `key` 字段**。严禁跨 pack 合并、严禁凭训练记忆补全：例如 chosen pack 是 `materials` 时，绝不能把 `constraints_criteria` / `gap_identification` / `hypothesis_formation` 等 sdr 专属 substage 塞进 `stages[].substages`——即使你认为"问题建模阶段应该有约束定义"。如果用户需要当前 pack 不存在的 substage，要么换 pack，要么先在该 pack 的 map 里新增，再输出工作流。
7. 工作流只需包含与用户任务相关、且未标记 `enabled: false` 的 substage，按执行顺序排列。`enabled: false` 仅表示预留流程槽位，不得加入当前可执行工作流。
8. 信息不足时持续追问，不得跳过。
9. 信息足够后输出结构化工作流。
10. 工作流必须等待用户确认。

## Must NOT
- 不得创建正式任务。
- 不得启动任务执行。
- 不得调用 PATCH /tasks/{id}/status。
- 不得把 task_status 推进到 running。
- 不得写入 stageDetails。
- 不得替代服务层状态机。
- 不得跳过 skill pack 判定步骤（必须显式输出 chosen pack）。

## Output Contract
严格遵循 `output_schema.json`，严格使用 ```json 代码块输出。
输出必须包含以下字段：
- `skillPack`（string，必填）：chosen pack key，如 `"sdr"`、`"ope"` 或 `"opentrajectory"`
- `researchType`（M/D/C/H）
- `stages[].substages`（对象数组，每个含 `substage` key）
