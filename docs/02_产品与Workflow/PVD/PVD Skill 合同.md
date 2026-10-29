# PVD Skill / capability 合同

> 状态：已确认
> 更新时间：2026-09-16
> 职责：定义 PVD 407b 科研能力边界与 OpenAIR 迁移 Authority。

## 1. Authority

PVD 领域科研行为以 Standalone `main@407b0445f1381b13ef318c28f4d47594fe8cc07a` 为唯一迁移 Authority。

OpenAIR 现存同名或旧版 Skill 只有在逐 capability 证明与 407b 科研合同一致后才能成为正式实现。版本号相同也不能代替语义核验。

## 2. 八个科研 capability

| capability | 407b 核心职责 |
|---|---|
| Research Request Understanding | 一次完成请求科研理解、动态参数 scope、pending admission、unknown/no-guess、已有实验事实 |
| Evidence Acquisition Design | CALL_02 检索科研语义 + CALL_03 EvidenceSchema + shortage-triggered supplemental |
| Literature Evidence Acquisition & Closure | retrieval + optional CALL_04 + progressive reading + parallel CALL_06 + CALL_07/deterministic closure |
| Evidence Comparability & Scientific-Use Authorization | CALL_08 → parallel CALL_09 → CALL_10；CALL_10 为 `allowed_uses` Authority |
| Candidate Direction Analysis | exact dynamic scope 的方向判断；首轮与反馈轮复用；不产生 concrete value |
| Parameter Feasibility & Experimental Admission | feasible/conditional/insufficient/infeasible + recommendation gate；unavailable 不冒充 infeasible |
| Experiment Recommendation / Portfolio Design | CALL_13 primary + optional CALL_14；dynamic scope；hard-boundary validation |
| Experimental Result Diagnosis | CALL_17；使用正式 feedback facts 和 comparability allowed_uses，形成 diagnosis/downstream readiness |

## 3. 不升级为科研 capability 的内部机制

- feedback facts admission（CALL_16）；
- BO deterministic candidate generation；
- next-round continuation；
- cache / batch / early-stop / checkpoint / deterministic join；
- CALL ID 本身。

## 4. 当前迁移状态

- `pvd_research_request_understanding`：已按 407b 正确迁入 OpenAIR。
- 其余七个 capability：必须按 407b 逐项迁移；OpenAIR 当前旧实现不能因为已注册或同名而自动视为 Authority。
- legacy Skill ID 可以暂时保留稳定身份，但科研行为必须以 407b public contract 为准。

## 5. LLM 与代码边界

LLM 负责科研语义增量；代码负责稳定 ID、state、provenance、hard constraints、确定性 join、Schema 校验与 finalization。

禁止：
- 用固定四参数代替动态 scope；
- 用 default/guess 填 unknown；
- 把内部 CALL 拆成多个科研 work；
- 为接旧实现增加 mapper/shim/双入口；
- 复制 Standalone Provider runtime、Resume、DB 或 Artifact 生命周期。


## 当前迁移执行边界

- 当前只有 `pvd_research_request_understanding` 已完成 407b → OpenAIR 正式迁移并进入可执行 composition。
- Capability 2～8 只有设计 Authority，旧 OpenAIR PVD 科研 Skill 与旧 Binding 已退出正式执行路径；在各 capability 按 407b 独立迁移完成前保持不可执行。
- 407b 仍认可的 deterministic 辅助工具可以保留在 Registry，但不得自动成为任何未迁 capability 的科研 Authority。
- 不通过兼容层、旧 Skill 回挂或临时 translation shim 恢复未迁能力。
