# PVD Evidence Acquisition Design

## 用户价值

把研究请求理解形成的正式科研事实、动态参数范围和待评估参数，转成可执行的检索设计与 Evidence 目标体系。

## Input

- `structured_context`
- `recommendation_parameter_set`
- `parameter_admission_judgments`

正式参数集合是主链参数 Authority；`pending_evidence` 参数只进入证据覆盖，不因此升格为正式推荐参数。

## Capability

- Evidence Acquisition Design
- 内部可包含核心检索语义设计、EvidenceSchema 设计和真实 shortage 触发的 supplemental 检索设计。
- 这些内部步骤共同属于一个 capability，不拆成多个正式科研 work。

## Output

- 检索设计
- EvidenceSchema
- Evidence coverage 目标
- pending parameter evidence identities（如存在）

## Acceptance

- 正式参数与 pending 参数均能被正确覆盖；
- pending 参数不自动进入 `recommendation_parameter_set`；
- 只在真实 shortage 时触发 supplemental；
- LLM 负责科研检索语义和 Evidence 目标，代码负责稳定身份、确定性 join 与合同校验；
- 不形成候选方向、可行性结论或具体推荐值。
