# PVD 科研状态事实合同

> 状态：已确认
> 更新时间：2026-09-26
> 职责：定义 PVD 研究中需要跨轮次长期保存、继续读取和追溯的正式科研事实。

## 1. 合同边界

本文定义 PVD 跨轮次正式科研事实在 ScientificState 中的唯一表示方式。

本文负责：

- 实验体系事实如何长期保存和引用；
- 一次真实实验如何形成稳定身份；
- 系统建议、正式计划、实际执行和真实测量如何严格分开；
- 参数、过程事件和来源如何保存；
- 多轮实验历史如何长期保留；
- 哪些内容不得复制进入 ScientificState。

本文不定义：

- PVD 八个 capability 的职责；
- Skill 的科研算法与判断逻辑；
- capability 之间的结果交接方式；
- Shared Binding 的解析与执行方式；
- BO 的候选生成与选择逻辑；
- UI、队列、worker、retry 等执行状态。

## 2. ScientificState 中的 PVD 科研事实

PVD 正式科研事实保存在：

```text
scientificFacts
├─ experimental_system_contexts[]
├─ current_experimental_system_context_ref
├─ current_recommendation_decision
└─ experiment_history[]
```

四者职责严格分开：

- `experimental_system_contexts[]`：保存同一 ResearchTask 中出现过的正式实验体系；
- `current_experimental_system_context_ref`：指向当前正式使用的实验体系；
- `current_recommendation_decision`：保存 Capability 7 最近一次已接受结果形成的当前推荐决策状态；
- `experiment_history[]`：保存真实实验历史，每条记录引用当时使用的实验体系。

不得再完整保存一份“当前实验体系”，形成第二套正式事实。

## 3. 实验体系事实

### 3.1 `experimental_system_contexts[]`

一个 ResearchTask 可以包含多套实验体系。

每套正式实验体系只完整保存一次：

```text
experimental_system_contexts[]
├─ experimental_system_context_ref
└─ experimental_system_context
```

#### `experimental_system_context_ref`

- 必填；
- 在同一 ResearchTask 内唯一；
- 是该实验体系的稳定身份；
- 必须能够稳定解析到对应的 `experimental_system_context`；
- 不得由数组位置、文本内容、Skill 版本或临时执行 ID 推断。

#### `experimental_system_context`

- 结构由本合同及 `schemas/pvd/pvd-scientific-state-facts-v1.schema.json#/$defs/experimentalSystemContext` 定义；具体 Skill 只负责形成或更新该事实，不成为结构 Authority；
- 本合同不重新定义设备、靶材、衬底、靶基距等字段；
- Skill Artifact 可以形成或更新该事实，但长期正式事实由 ScientificState 持有；
- 导入项目历史中的真实实验体系时，应先登记为正式实验体系，再由历史实验引用。

### 3.2 `current_experimental_system_context_ref`

表示当前正式实验体系。

规则：

- 必须引用 `experimental_system_contexts[]` 中已经存在的 `experimental_system_context_ref`；
- 实验体系变化时更新该引用；
- 不复制完整实验体系；
- 更新当前引用不得改变历史实验原先绑定的实验体系。

### 3.3 `current_recommendation_decision`

Capability 7 每个通过正式 schema 校验并被当前项目接受的 Result，都更新当前推荐决策状态。该事实用于跨轮保留会影响下一研究决定的认识，不形成第二套 Recommendation history。

最小语义必须包含：

```text
current_recommendation_decision
├─ decision_status
├─ important_unknowns[]
├─ next_step
└─ provenance
```

`decision_status` 只允许：

```text
recommendation
no_recommendation
```

规则：

- 首个 Capability 7 Result 被接受前，该字段可以尚不存在；
- `recommendation` 与 `no_recommendation` 都必须按 Result 的正式结构原样保存 `important_unknowns` 与 `next_step`，不得由代码根据文本猜测或补造；
- `provenance` 必须指向产生本次已接受 Result 的正式 Artifact；
- `current_recommendation_decision` 只表达当前决策状态、关键未知和下一步，不复制参数、validation goal、failure signal 或推荐解释；
- 正式参数建议仍只进入对应实验记录的 `system_recommendation`；
- `no_recommendation` 不新增实验记录，不形成 `system_recommendation`，也不形成 `planned_execution`、`actual_execution` 或 `measurements`；
- 更新当前决策不得删除或改写已有 `experiment_history`；
- 下一次 ExecutionDecision 必须从 canonical ScientificState 读取该事实，使本轮关键未知与下一步真实参与下一研究工作的决定；
- 只保存当前状态，不增加独立的推荐决策历史或消费者专用副本。

## 4. 实验历史

`experiment_history[]` 是 PVD 跨轮次真实实验事实的唯一正式历史。

每条记录表示一次可独立追溯的真实实验：

```text
experiment_history[]
├─ experiment_ref
├─ round_ref
├─ experimental_system_context_ref
├─ system_recommendation
├─ planned_execution
├─ actual_execution
├─ measurements[]
└─ process_events[]
```

数组按真实时间顺序保存：

```text
oldest → newest
```

不得另外保存 `chronology_index`、`latest_history`、`optimization_round`，也不得为 Candidate Direction、Diagnosis、Recommendation 或其他消费者建立专用历史副本。

### 4.1 `experiment_ref`

- 必填；
- 在同一 ResearchTask 内唯一；
- 是该真实实验记录的稳定身份；
- 同一次实验后续补充真实执行、测量或过程事件时，继续使用同一个 `experiment_ref`；
- 不得由数组位置、文件名或 Skill 临时 ID 推断。

### 4.2 `round_ref`

表示该实验是否属于 OpenAIR PVD 正式优化轮次：

```text
R0
R1
R2
...
null
```

规则：

- `null` 表示项目既有历史，或尚未纳入正式优化轮次；
- 不得根据数组位置自动生成 R0 / R1 / R2；
- 不增加第二个字段重复表达轮次顺序。

### 4.3 `experimental_system_context_ref`

- 必填；
- 必须引用 `experimental_system_contexts[]` 中已登记的正式实验体系；
- 表示该次实验当时真实使用的实验体系；
- 不在历史记录中复制完整 context。

## 5. 四类事实必须严格分开

```text
system_recommendation
≠ planned_execution
≠ actual_execution
≠ measurements
```

四类事实不得互相补值、替代或覆盖。

### 5.1 `system_recommendation`

表示系统针对本轮实验正式给出的工艺参数建议。

推荐参数集合不在 ScientificState 合同中预先写死，而由当前 ResearchTask / ResearchDesign、正式项目事实和 Recommendation 最终推荐决定。

最小语义应能够表达：

```text
system_recommendation
├─ parameters[]
└─ provenance
```

`parameters[]` 中每一项表示一个正式推荐参数，至少能够表达参数身份、建议值或范围、适用单位和必要的来源追溯。具体 public schema 由正式参数 recommendation contract 定义，本合同不建立第二套字段体系。

规则：

- 没有正式建议时为 `null`；
- 一旦存在，每个正式推荐参数都必须具有合法、明确的建议，不得用未知值冒充正式建议；
- 必须追溯到正式系统推荐来源；
- 后续用户计划或真实执行即使不同，也不得修改原建议；
- 推荐参数集合由当前研究任务和 Recommendation 正式结果决定，不得因当前演示案例固定为四项；
- 当前 AlN / AlSc 演示案例可以记录功率、N₂ 流量、Ar 流量和沉积时间四个推荐参数，但这只是案例实例；
- BO candidate、GP 预测值、UCB 分数等优化中间结果不是 `system_recommendation`。只有经过 Feasibility 核验并由 Recommendation 正式采用的最终建议才能写入。

### 5.2 `planned_execution`

表示用户已经明确确认，或正式项目记录已经明确形成的计划执行事实。

规则：

- 没有正式计划时为 `null`；
- 系统建议不能自动升级为 `planned_execution`；
- 只有用户明确确认，或正式项目记录明确记载计划时，才可写入；
- 可以与 `system_recommendation` 不同；
- 可以包含正式推荐参数，也可以包含该次实验计划中的其他工艺条件；
- 后续实际执行发生后，原计划仍保留，不得被实际值覆盖。

### 5.3 `actual_execution`

表示真实发生的实验执行事实，或明确未执行的事实。

最小语义必须包含：

```text
execution_ref
status
validity
parameters
other_process_conditions[]
provenance
```

`status` 只允许：

```text
executed
partially_executed
not_executed
```

`validity` 只允许：

```text
valid
partial
invalid
unknown
```

规则：

- 没有任何正式执行事实时为 `null`；
- 只能来自用户提交、实验记录或导入项目记录等正式事实来源；
- 禁止用系统建议或计划值补齐实际执行；
- 实际执行与系统建议或计划不一致，属于合法科研事实；
- `invalid`、`partial` 或 `not_executed` 的事实仍需保留，不得从历史删除。

### 5.4 `measurements[]`

表示该次实验真实得到的测量或观测事实。

每条测量至少能够表达：

```text
measurement_ref
metric_key
value / qualitative_result
unit
status
validity
method
measurement_condition
evaluation_basis_ref
repeat_group_ref
uncertainty
reported_anomaly
provenance
```

`status` 只允许：

```text
provisional
final
```

`validity` 只允许：

```text
valid
invalid
unknown
```

规则：

- 没有真实测量时为 `[]`；
- 只保存真实项目结果；
- 文献结果、模型估计、BO 预测不得写入；
- `reported_anomaly` 只保存用户、实验记录或设备正式报告的异常事实；
- Skill 推断出的异常原因、机制解释或诊断结论属于 Artifact，不写入 ScientificState。

## 6. 参数事实

PVD 参数集合不由本合同固定。不同 ResearchTask 可以关注不同数量和种类的参数，例如功率、气体流量、沉积时间、温度、压力、偏压或其他可控工艺变量。

当前 AlN / AlSc 演示案例使用：

| 参数 | 单位 |
|---|---|
| `power` | W |
| `n2_flow` | sccm |
| `ar_flow` | sccm |
| `deposition_time` | s |

这四项只是案例参数，不是 ScientificState 的固定参数字段。

参数事实至少需要稳定表达：

```text
parameter identity
status
value / range when applicable
unit when applicable
provenance
```

`status` 至少区分：

```text
known
unknown
not_reported
```

规则：

- `known` 时必须有合法值；需要单位时使用正式统一单位；
- `unknown` / `not_reported` 时，不得用默认值、推荐值、历史值或模型推断补成 `known`；
- `system_recommendation` 中的正式推荐参数必须具有明确合法的建议；如果 recommendation 支持范围，应按其 public contract 原样保存，不得私自取中点或默认值；
- `planned_execution` 与 `actual_execution` 可以部分未知，但必须保留真实状态；
- 同一个工艺条件在不同任务中可以是推荐参数、固定条件或约束，具体角色由正式研究任务和项目事实决定；
- 不得因为某参数没有进入 BO，就把它从 ScientificState 的计划、执行或正式 recommendation 中删除；
- 不得为了让参数进入 BO 而改变其真实类型、补造数值或覆盖未知状态。

## 7. 来源追溯

进入 ScientificState 的可变科研事实必须能够追溯到正式来源。

最小 provenance：

```text
origin
source_ref
```

`origin` 只允许：

```text
system
user
imported_project_record
```

规则：

- 系统建议必须追溯到正式系统来源；
- `planned_execution` 只有形成正式计划事实后才能记录对应 provenance；
- `actual_execution` 和真实测量不得标记为 `system`；
- `source_ref` 必须稳定且可解析；
- provenance 只说明事实来自哪里，不代表该事实已经经过科研解释或验证。

## 8. 过程事件

`process_events[]` 保存真实执行过程中需要跨轮保留的过程事实，例如：

- 实验中断；
- 设备异常；
- 样品损坏；
- 实际过程偏离计划。

每条事件至少能够表达：

```text
event_ref
statement
provenance
```

规则：

- 只记录正式发生或正式报告的过程事实；
- 没有可靠时间时不得猜测时间；
- Skill 对事件原因的分析或诊断属于 Artifact，不写入过程事实本身。

## 9. ScientificState 与 Artifact 的边界

ScientificState 只保存跨轮次继续成立的正式项目事实。

以下研究分析内容由对应 Artifact 持有，不复制进 `scientificFacts`：

- Literature Evidence Closure 研究记录、文献证据与证据抽取内容；
- Comparability 可比性分析；
- Candidate Direction 因素分析与候选方向；
- Feasibility 参数可行性核验；
- Diagnosis 实验结果诊断、原因解释和轮次分析；
- Recommendation 推荐依据、风险、解释等分析内容；
- BO candidate、模型预测、不确定性、采集函数分数等优化中间结果。

如果 ScientificState 需要跨轮追溯这些研究产物，只保存已有的稳定 `artifact_ref` / `source_ref`，不得复制分析正文形成第二套正式内容。

当某个 Artifact 中的内容被本合同明确提升为长期项目事实时，可以通过正式状态更新进入 ScientificState。例如：

- Node1 形成或更新正式实验体系事实；
- Capability 7 的每个已接受 Result 更新正式 `current_recommendation_decision`；
- Capability 7 的 `recommendation` Result 另形成正式 `system_recommendation`；`no_recommendation` Result 不形成实验历史。

进入 ScientificState 后，以本合同定义的事实结构为准，不保留消费者专用副本。

## 10. 历史与状态更新规则

ScientificState 按不可变版本保存。

PVD 状态更新必须满足：

1. 新事实进入后形成新的 ScientificState version；
2. 旧版本不得原地覆盖；
3. 同一次实验后续补充执行或测量时继续使用同一 `experiment_ref`；
4. 新推荐不得覆盖旧实验记录中的原推荐；
5. 正式计划不得被后续实际执行覆盖；
6. 后续测量不得改写当时真实执行参数；
7. 失败、异常、无效、部分执行和与建议不一致的真实实验不得从历史删除；
8. 实验体系发生变化后，旧实验继续通过原 `experimental_system_context_ref` 指向当时真实体系；
9. 不保存任何 Candidate Direction / Diagnosis / Recommendation / BO 专用历史视图；
10. Shared Binding 或其他共享层不得为某个消费者重组出第二套科研状态事实。
11. 每个已接受的 Capability 7 Result 都更新 `current_recommendation_decision`；只有 `recommendation` 可以新增带 `system_recommendation` 的实验记录；
12. `no_recommendation` 只更新当前推荐决策状态，不得创建实验、计划、执行或测量事实。

## 11. 合同不变量

```text
一个实验体系完整事实只保存一次
当前体系只保存 ref
当前推荐决策状态保存 status、unknowns、next step 与 provenance
recommendation 与 no_recommendation 都更新当前推荐决策状态
no_recommendation 不创建推荐或实验历史
每条实验历史必须绑定真实 experimental system
系统建议 ≠ 正式计划 ≠ 实际执行 ≠ 真实测量
推荐参数集合由当前研究任务决定，不由产品写死
BO 只影响进入数值优化的参数子集，不改变真实科研事实
真实事实不得由推荐、模型或历史默认值补造
Artifact 分析正文不复制进 ScientificState
同一种科研事实不保存消费者专用第二表示
历史事实不可因后续轮次被覆盖或删除
```
