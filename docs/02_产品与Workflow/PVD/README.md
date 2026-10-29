# OpenAIR PVD｜长期设计文档

本目录以 **PVD 407b 的八个科研 capability** 作为领域 Authority；OpenAIR 只提供统一平台骨架。

## 长期 Authority

```text
研究请求理解
→ Evidence Acquisition Design
→ Literature Evidence Acquisition & Closure
→ Comparability
→ Candidate Direction
→ Feasibility
→ Recommendation / Portfolio
→ 用户真实实验

真实反馈 → Diagnosis → 复用 Direction → optional BO → Feasibility → Recommendation
```

独立 baseline 科研节点已取消。

参数推荐不固定为四参数；当前 AlN / AlSc 四参数仅是演示案例。BO 在 407b 中仍是四参数受限的可选工具，不拥有全链参数 Authority。

## 文档职责

| 文档 | Authority |
|---|---|
| OpenAIR PVD MVP｜产品合同 | 用户得到什么、动态参数产品边界、BO 边界 |
| OpenAIR PVD Workflow 设计说明 | 八 capability 结构、首轮与反馈轮关系 |
| OpenAIR PVD 研究产物与呈现设计说明 | 各 capability 对用户呈现什么 |
| PVD Skill 合同 | 407b capability / Skill 科研职责边界 |
| PVD Workflow 节点交接合同 | capability 之间的正式 handoff |
| PVD 科研状态事实合同 | 跨轮次长期科研事实 |

## 迁移规则

每个 capability 独立迁移、验收到下一正式 handoff。OpenAIR 旧 Skill 不因“已经注册”而自动成为 Authority。


## 当前正式执行边界

当前 PVD v2 的八项正式能力已经完成迁移与 shared wiring 收口：

1. `pvd:research_goal_equipment_constraints`：研究请求理解与当前项目上下文；
2. `pvd:research_retrieval_experiment_design`：Evidence 获取设计；core retrieval 后仅在真实 `evidence_shortage_facts` 存在时进入 supplemental；
3. `pvd:literature_experiment_record_verification`：文献证据获取与闭环，并从真实 coverage gap 确定性形成 shortage facts；
4. `pvd:evidence_applicability_comparability_analysis`：证据适用性、可比性与 `allowed_uses` 授权；
5. `pvd:process_factor_candidate_experiment_analysis`：工艺因素与候选方向分析，不生成最终具体参数值；
6. `pvd:parameter_experiment_plan_feasibility_validation`：对方向进行可行性与实验准入，保留 blocked / defer / conditional 边界；
7. `pvd:round_experiment_plan_parameter_recommendation`：在可行性边界内生成具体参数值、实验组与优先级；
8. `pvd:experimental_feedback_round_analysis`：针对显式 `experiment_ref` 分析真实执行与测量反馈，不直接生成下一轮 concrete recommendation。

shared Binding 直接传递正式 Artifact，不设置 translation mapper、shadow schema 或双轨合同。推荐、计划、实际执行和实际测量保持事实层级分离。局部证据或补充检索失败时保留已有可信结果，并明确缺口与影响。
