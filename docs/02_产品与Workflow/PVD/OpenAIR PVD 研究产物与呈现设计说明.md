# OpenAIR PVD 研究产物与呈现设计说明

> 状态：已确认
> 更新时间：2026-09-16
> 职责：定义八个 PVD capability 的主要研究产物及用户可见重点。

| capability | 主要研究产物 | 重点呈现 |
|---|---|---|
| 研究请求理解 | canonical research context | 研究目标、材料/系统、正式动态参数、pending 参数、unknown、已有实验 |
| Evidence Acquisition Design | 检索设计 + EvidenceSchema | 查什么、为什么查、什么证据算够 |
| Literature Evidence Closure | EvidencePacket + ResearchRecords | 来源、实验条件、结果、覆盖与证据缺口 |
| Comparability | comparability + `allowed_uses` | 相似条件、关键差异、可支持范围、迁移限制 |
| Candidate Direction | parameter directions | 每个正式参数的方向、依据、不确定性；不显示伪 concrete value |
| Feasibility | feasibility + recommendation gate | 可执行性、必要条件、边界、风险、缺失信息 |
| Recommendation / Portfolio | primary + optional exploratory sets | 每组完整动态参数、优先级、依据、验证目的、风险与下一步 |
| Experimental Result Diagnosis | diagnosis + downstream readiness | 实际执行、真实测量、异常解释、下一轮是否具备继续条件 |

## 呈现原则

- unknown 显示为未知，不补默认值；
- 建议、计划、实际执行、真实测量必须区分；
- BO 状态单独显示，不能把其四参数限制投射到全链；
- 每个正式参数都可追溯到 formal scope 和证据/约束依据；
- feedback diagnosis 不覆盖 ScientificState 中的真实事实。
