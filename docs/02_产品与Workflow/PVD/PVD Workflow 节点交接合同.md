# PVD Workflow capability 交接合同

> 状态：已确认
> 更新时间：2026-09-16
> 职责：定义八个正式 PVD capability 之间交什么结果、共享事实从哪里读取，以及缺失时能否继续。

## 1. 正式信息来源

只有三类：

| 类型 | Authority |
|---|---|
| capability Artifact | 上游科研 capability 的正式结果 |
| ScientificState | 实验系统、真实执行、真实测量、实验历史等长期科研事实 |
| 当前 ResearchTask / ResearchDesign | 当前目标、正式参数范围和用户明确约束 |

交接层只做身份解析、正式字段选择和合同校验，不重新解释科研语义，不造 mapper/shim，也不猜未知值。

## 2. 首轮交接

```text
研究请求理解
→ Evidence Acquisition Design
→ Literature Evidence Acquisition & Closure
→ Comparability
→ Candidate Direction
→ Feasibility
→ Recommendation / Portfolio
```

| capability | 主要正式输入 | 正式输出 | 下一消费者 |
|---|---|---|---|
| 研究请求理解 | 用户当前请求 | `structured_context`、`recommendation_parameter_set`、`run_preferences`；已有真实实验可确定性写 ScientificState | Evidence Acquisition Design；后续所有需要当前实验体系的能力 |
| Evidence Acquisition Design | M1 canonical facts + formal/pending parameter identities | 检索设计、EvidenceSchema、coverage target | Literature Evidence Closure |
| Literature Evidence Closure | 检索设计 + EvidenceSchema | EvidencePacket、ResearchRecords、source provenance、coverage/gaps | Comparability |
| Comparability | M1 context + ResearchRecords/Evidence | comparability、transfer limits、`allowed_uses` | Direction、Diagnosis、Recommendation |
| Candidate Direction | formal scope + comparability + ScientificState + optional diagnosis | 每个正式参数的方向与不确定性 | Feasibility；可选 BO |
| Feasibility | M1 context + direction + optional BO candidate + hard boundaries | feasibility status、conditions、risks、recommendation gate | Recommendation |
| Recommendation / Portfolio | formal scope + records + comparability + direction + feasibility + ScientificState | primary recommendation + optional exploratory portfolio | 用户；正式推荐可进入 ScientificState |

## 3. 反馈轮

反馈事实准入不是独立科研 capability。只有完整、合法、属于当前 formal scope 的真实执行与测量才写入 ScientificState。

之后：

```text
ScientificState
→ Experimental Result Diagnosis
→ Candidate Direction
→ optional BO
→ Feasibility
→ Recommendation / Portfolio
```

Diagnosis 只使用 comparability `allowed_uses` 允许的详细证据；不建立第二套实验历史。

## 4. 交接判定

### 可交接
正式上游结果存在、身份明确、结构与科研语义满足下游合同。

### 带条件交接
下游合同允许 unknown / insufficient / unavailable 时，原样保留这些状态继续。

### 不可交接
- 必需 Artifact 不存在；
- source identity 不明确；
- public contract 科研语义不兼容；
- 需要文本猜测、兼容层或领域特判才能接通；
- Feasibility 明确 block；
- feedback 事实不完整却试图写入真实历史。

## 5. Authority 规则

- formal parameter scope 只来自 `recommendation_parameter_set`；
- pending parameter 不自动 promotion；
- Synthesis 才能签发 comparability `allowed_uses`；
- Direction 不给具体值；
- Feasibility 不改方向和值；
- Recommendation 才给最终 concrete values；
- ScientificState 是跨轮次真实实验事实 Authority。
