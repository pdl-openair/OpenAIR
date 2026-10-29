# OpenTrajectory 评测接入快速熟悉文档 V0.3

> 面向对象：新加入 OpenTrajectory 评测工作的同学。  
> 文档目标：在较短时间内看懂 OpenTrajectory 是做什么的、流程如何推进、一级阶段 / 二级节点 / 三级原子 Skill 分别代表什么，以及当前可以从哪里接入。  

---

## 0. 新同学快速熟悉顺序

| 顺序 | 先看什么 | 目的 |
|---:|---|---|
| 1 | 本文第 2–4 节 | 建立 L1 / L2 / L3 的整体认知，理解完整主链。 |
| 2 | 第 8 节与第 10 节 | 理解当前正式可接入链路，以及资料如何逐步转化为机制链。 |
| 3 | `PaperPacket[] → EvidencePacket[] → MechanismPacket[]` | 熟悉当前最核心的结构化产物。 |
| 4 | 第 11 节 | 根据 Skill 索引找到具体接入入口。 |
| 5 | 第 13 节 | 理解不同结构化产物之间的语义边界。 |

---

## 1. OpenTrajectory 是做什么的

OpenTrajectory 是一个面向科研资料分析与研究线索生成的工作系统。

它从一个科研问题出发，逐步完成需求理解、资料获取、原文深读、证据抽取、机制链分析和研究线索生成，最终形成可追溯、可核验、可继续验证的科研结果。

完整目标流程如下：

```text
科研问题
→ 结构化需求与约束
→ 检索策略与证据模型
→ 候选文献与关键原文
→ 可追溯证据
→ 机制链
→ 已有结论
→ 科研线索
→ 待验证假说
→ 最小验证方案
→ 可回溯科研结果包
```

OpenTrajectory 最终希望解决两个问题：

```text
1. 生成的科研线索与假说是否真正有帮助
2. 支撑这些结果的证据是否高质量、可追溯、可核验
```

---

## 2. 必须先理解的三层定义

OpenTrajectory 运行在 OpenAIR 的统一工作流体系中。理解流程时，需要严格区分三个层级。

| 层级 | 正式名称 | 回答的问题 | 典型例子 | 理解方式 |
|---|---|---|---|---|
| L1 | 一级阶段 `Stage` | 当前科研任务推进到哪一类阶段？ | 问题建模、方案设计、执行验证、洞察生成 | 平台级固定主线。数量少，跨不同科研任务尽量保持稳定。 |
| L2 | 二级节点 `Substage` | 当前需要完成什么科研动作？ | 文献召回与来源归一、证据抽取、机制链分析 | 面向科研动作和阶段产物。一个 L2 可以编排一个或多个 L3 Skill。 |
| L3 | 三级原子能力 `Atomic Skill` | 这个科研动作具体怎么执行？ | 候选文献去重、渐进式深读、证据节点抽取 | 最小可运行能力。职责单一、输入输出明确、可独立接入、可复用、可替换。 |

三层关系如下：

```text
一级阶段 Stage
→ 包含多个二级节点 Substage
→ 每个二级节点可编排一个或多个三级原子 Skill
```

需要特别注意：

```text
L2 表示“要完成什么科研动作”
L3 表示“具体怎么完成这个动作”

L2 不等于单个代码文件
L2 也不要求永久只对应一个 Skill
```

一个 L2 当前只有一个默认 Skill，并不代表两者永久绑定。后续可以增加替代 Skill、辅助 Skill 或不同领域版本。

---

## 3. 四个一级阶段

OpenTrajectory 统一使用 OpenAIR 的四个一级阶段。

| 一级阶段（L1） | 中文名称 | 阶段目标 |
|---|---|---|
| `problem_modeling` | 问题建模 | 将科研问题整理为明确、可执行、可约束的任务。 |
| `solution_design` | 方案设计 | 在获取资料前，确定检索策略和证据组织方式。 |
| `execution_validation` | 执行验证 | 获取资料、筛选文献、深读原文、抽取证据、构建机制链并整理已有结论。 |
| `insight_generation` | 洞察生成 | 基于已有证据和机制链生成科研线索、待验证假说、验证方案和最终结果包。 |

---

## 4. 一张图看懂完整流程

```text
平台前置入口
任务创建与流程编排
new-task-planning
        │
        ▼
┌──────────────────────────────────────────────┐
│ L1：问题建模 problem_modeling               │
│                                              │
│ 科研需求理解                                 │
│ intent_understanding                         │
│        ↓                                     │
│ 约束与标准                                   │
│ constraints_criteria                         │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ L1：方案设计 solution_design                │
│                                              │
│ 检索策略设计                                 │
│ retrieval_strategy_design                    │
│        ↓                                     │
│ 证据模型设计                                 │
│ evidence_schema_design                       │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ L1：执行验证 execution_validation           │
│                                              │
│ 文献召回与来源归一                           │
│ source_acquisition                           │
│        ↓                                     │
│ 候选文献筛选与优选                           │
│ source_screening_selection                   │
│        ↓                                     │
│ 文献渐进式深读                               │
│ progressive_reading                          │
│        ↓                                     │
│ 证据抽取                                     │
│ evidence_extraction                          │
│        ↓                                     │
│ 机制链分析                                   │
│ mechanism_explanation                        │
│        ↓                                     │
│ 已有结论凝练                                 │
│ findings_synthesis                           │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ L1：洞察生成 insight_generation             │
│                                              │
│ 科研线索生成                                 │
│ research_lead_generation                     │
│        ↓                                     │
│ 待验证假说生成与更新                         │
│ hypothesis_update                            │
│        ↓                                     │
│ 最小验证方案生成                             │
│ validation_plan_generation                   │
│        ↓                                     │
│ 科研结果包组装                               │
│ report_generation                            │
└──────────────────────────────────────────────┘
```

---

## 5. 平台前置入口

平台前置入口不属于 OpenTrajectory 的科研主链，但负责将用户需求编排成可执行工作流。

### 接入状态说明

| 接入状态 | 含义 |
|---|---|
| 可接入 | 已提供正式接入入口，可以直接开展接入工作。 |
| 待正式接入 | 已有实现，但接入入口或边界仍在收敛，暂不纳入当前正式接入范围。 |
| 暂未接入 | 尚未提供可用接入入口。 |

| 类型 | 节点 | 默认 Skill | 作用 | 接入状态 |
|---|---|---|---|---|
| 平台通用入口 | 任务创建与流程编排 | `new-task-planning` | 根据用户目标判定 Skill Pack，生成 `stage → substage → skill` 工作流，并等待用户确认。 | OpenAIR 通用能力 |

---

## 6. 一级阶段一：问题建模

目标：将自然语言科研问题整理为后续流程可以直接使用的结构化任务。

| 二级节点（L2） | 节点作用：做什么 | 当前默认 Skill（L3） | Skill 作用：怎么做 | 主要输入 | 主要输出 | 接入状态 |
|---|---|---|---|---|---|---|
| `intent_understanding`<br>科研需求理解 | 将用户提出的科研问题整理为结构化需求，明确研究对象、研究目标、约束和预期结果。 | `structured_requirement_generation` | 将自然语言需求转换为结构化 `StructuredRequirement`。 | 用户自然语言科研问题 | `StructuredRequirement` | 可接入 |
| `constraints_criteria`<br>约束与标准 | 解释硬约束、软约束、筛选条件和检索边界，为后续方案设计提供明确规则。 | `constraint_interpretation_skill` | 将结构化需求中的约束转换为检索关键词、过滤规则和约束解释。 | `StructuredRequirement` | 约束解释、筛选边界、关键词与过滤规则 | 可接入 |

---

## 7. 一级阶段二：方案设计

目标：在真正开始获取资料前，先明确“搜什么”和“后续需要什么证据”。

| 二级节点（L2） | 节点作用：做什么 | 当前默认或规划 Skill（L3） | Skill 作用：怎么做 | 主要输入 | 主要输出 | 接入状态 |
|---|---|---|---|---|---|---|
| `retrieval_strategy_design`<br>检索策略设计 | 根据研究目标和约束，设计覆盖不同方向、机制和指标的检索矩阵。 | `retrieval_matrix_generation` | 生成多维检索矩阵、查询变体和检索意图标注。 | 结构化需求、约束解释 | 检索矩阵、查询组合、方向覆盖说明 | 可接入 |
| `evidence_schema_design`<br>证据模型设计 | 根据科研问题动态确定后续需要抽取哪些实体、实验条件、性能指标、理论计算信息、关系类型，以及什么证据足以支撑后续判断。 | `evidence_schema_generation` | 生成任务级证据模型（Evidence Schema）、关系约束和抽取目标，作为深读、证据抽取和机制链分析的共同约束。 | 结构化需求、约束解释、检索策略 | 任务级证据模型（Evidence Schema）、关系定义、证据充分性规则 | 暂未接入 |

---

## 8. 一级阶段三：执行验证

目标：从文献与原文中获得高质量、可追溯、可核验的证据，并形成有依据的机制链和已有结论。

| 二级节点（L2） | 节点作用：做什么 | 当前默认 Skill（L3） | Skill 作用：怎么做 | 主要输入 | 主要输出 | 接入状态 |
|---|---|---|---|---|---|---|
| `source_acquisition`<br>文献召回与来源归一 | 执行多来源文献召回，合并重复候选，保留来源信息，并判断后续可访问程度。 | `academic_source_retrieval_skill`<br>`candidate_dedup_and_provenance`<br>`source_access_readiness_resolver` | 分别完成多源文献元数据召回、候选文献去重与来源保留、访问准备状态判断。 | 检索矩阵 | 去重后的候选文献池、来源信息、访问准备状态 | 可接入 |
| `source_screening_selection`<br>候选文献筛选与优选 | 从候选文献池中筛出值得深读的文献，并形成可解释的 Top-K 文献集合。 | `title_abstract_screening`<br>`candidate_source_scoring`<br>`candidate_tiering`<br>`topk_source_selection` | 分别完成标题摘要初筛、候选评分、候选分层和 Top-K 选择。 | 候选文献池、访问准备状态、约束与筛选边界 | 带筛选理由、分数和层级的 Top-K 文献 | 可接入 |
| `progressive_reading`<br>文献渐进式深读 | 对 Top-K 文献逐步深入阅读，优先获得与科研问题相关的关键原文片段和可追溯锚点。 | `deepxiv_progressive_reading` | 对 arXiv 文献执行 `brief → head → section` 渐进式阅读，输出 `PaperPacket[]` 和可供证据抽取使用的原文定位锚点（source anchors）。 | Top-K arXiv 候选文献 | `PaperPacket[]` | 可接入 |
| `evidence_extraction`<br>证据抽取 | 从深读结果中提取可回溯、可核验的证据节点。 | `evidence_node_extraction` | 从 `PaperPacket[]` 抽取 `EvidencePacket[]`，并保留原文定位信息。 | `PaperPacket[]` | `EvidencePacket[]` | 可接入 |
| `mechanism_explanation`<br>机制链分析 | 将分散证据组织成有依据的机制边和机制链，明确支持、冲突和证据缺口。 | `mechanism_chain_synthesis` | 从 `EvidencePacket[]` 生成 `MechanismPacket[]`，绑定 `evidence_id`，形成稳定 `mechanism_edge_id`，并区分 `support / conflict / gap`。 | `EvidencePacket[]` | `MechanismPacket[]` | 可接入 |
| `findings_synthesis`<br>已有结论凝练 | 只整理已有证据和机制链已经支持的结论，明确适用边界与置信度，不混入新增假说。 | `research_lead_hypothesis_generation` 中的 `evidence_supported_conclusions[]` 分支 | 从机制链中输出已有证据支持的结论，并保留 `evidence_id` 和 `mechanism_edge_id`。 | `MechanismPacket[]` | `evidence_supported_conclusions[]` | 待正式接入 |

---

## 9. 一级阶段四：洞察生成

目标：在已有证据和机制链基础上，生成值得科研人员继续判断和验证的研究线索与假说。

| 二级节点（L2） | 节点作用：做什么 | 当前默认或规划 Skill（L3） | Skill 作用：怎么做 | 主要输入 | 主要输出 | 接入状态 |
|---|---|---|---|---|---|---|
| `research_lead_generation`<br>科研线索生成 | 基于已有结论、机制链和证据缺口，提炼值得继续探索的科研方向。 | `research_lead_hypothesis_generation` 中的 `research_leads[]` 分支 | 生成机制推导线索，并绑定已有 `evidence_id` 与 `mechanism_edge_id`。 | `MechanismPacket[]`、已有结论 | `ResearchLead[]` | 待正式接入 |
| `hypothesis_update`<br>待验证假说生成与更新 | 将科研线索转化为尚待验证的可证伪假说，明确变量、方向、适用边界和不确定性。 | `research_lead_hypothesis_generation` 中的 `hypotheses[]` 分支 | 生成 `HypothesisDraft[]`，并区分论文已有结论与新增假说。 | `ResearchLead[]`、`MechanismPacket[]`、已有结论 | `HypothesisDraft[]` | 待正式接入 |
| `validation_plan_generation`<br>最小验证方案生成 | 为每条假说设计最小可执行验证方案，明确变量、预测、证伪条件、风险和优先级。 | `validation_plan_generation` | 根据假说生成 `ValidationPlan[]`，不直接调用实验设备、计算平台或优化器。 | `HypothesisDraft[]` | `ValidationPlan[]` | 暂未接入 |
| `report_generation`<br>科研结果包组装 | 将证据、机制链、已有结论、科研线索、假说和验证方案组装为可回溯科研结果包。 | `research_result_package_generation` | 生成可供科研人员审阅、导出和后续计算或实验接入的结构化结果包。 | 全链路结构化产物 | 可回溯科研结果包 | 暂未接入 |

---

## 10. 当前正式可接入链路

当前可以直接从以下链路开始接入：

```text
用户科研问题
→ structured_requirement_generation
→ constraint_interpretation_skill
→ retrieval_matrix_generation
→ academic_source_retrieval_skill
→ candidate_dedup_and_provenance
→ source_access_readiness_resolver
→ title_abstract_screening
→ candidate_source_scoring
→ candidate_tiering
→ topk_source_selection
→ deepxiv_progressive_reading
→ evidence_node_extraction
→ mechanism_chain_synthesis
```

这条链路中最核心的结构化数据流为：

```text
Top-K arXiv Candidate
→ PaperPacket[]
→ EvidencePacket[]
→ MechanismPacket[]
```

以下能力已经具备实现，但暂不纳入当前正式接入范围：

```text
MechanismPacket[]
→ evidence_supported_conclusions[]
→ ResearchLead[]
→ HypothesisDraft[]
```

---

## 11. Skill 快速索引

### 11.1 当前可接入 Skill

| 三级原子 Skill | 中文作用 | 所属二级节点 |
|---|---|---|
| `structured_requirement_generation` | 将自然语言科研问题转为结构化需求 | `intent_understanding` |
| `constraint_interpretation_skill` | 将约束转为可执行的边界、关键词和过滤规则 | `constraints_criteria` |
| `retrieval_matrix_generation` | 生成多维检索矩阵和查询组合 | `retrieval_strategy_design` |
| `academic_source_retrieval_skill` | 从多来源召回文献元数据（Metadata） | `source_acquisition` |
| `candidate_dedup_and_provenance` | 去重候选文献并保留来源 | `source_acquisition` |
| `source_access_readiness_resolver` | 判断候选文献的后续访问准备状态 | `source_acquisition` |
| `title_abstract_screening` | 基于标题和摘要执行初筛 | `source_screening_selection` |
| `candidate_source_scoring` | 对候选文献进行多维评分 | `source_screening_selection` |
| `candidate_tiering` | 对候选文献进行分层门控 | `source_screening_selection` |
| `topk_source_selection` | 选择最值得深读的 Top-K 文献 | `source_screening_selection` |
| `deepxiv_progressive_reading` | 对 arXiv 文献执行渐进式深读 | `progressive_reading` |
| `evidence_schema_design` | 根据科研问题生成任务级证据模型（EvidenceSchema） | `evidence_schema_design` |
| `evidence_node_extraction` | 从深读内容提取可追溯证据节点 | `evidence_extraction` |
| `mechanism_chain_synthesis` | 将证据组织成机制边和机制链 | `mechanism_explanation` |

### 11.2 待正式接入 Skill

| Skill | 中文作用 | 所属二级节点 | 说明 |
|---|---|---|---|
| `research_lead_hypothesis_generation` | 生成已有结论、科研线索和待验证假说 | `findings_synthesis`、`research_lead_generation`、`hypothesis_update` | 当前以一个复合 Skill 承载三个节点，尚未纳入正式接入范围。 |

### 11.3 暂未接入 Skill

| 规划 Skill | 中文作用 | 所属二级节点 |
|---|---|---|
| `validation_plan_generation` | 为假说生成最小可执行验证方案 | `validation_plan_generation` |
| `research_result_package_generation` | 组装可回溯科研结果包 | `report_generation` |

---

## 12. 可接入对象

OpenTrajectory 可以按不同粒度接入。具体评测规则、数据集和评分方式由评测团队进一步设计。

| 粒度 | 接入对象 | 用途 |
|---|---|---|
| 单个 Skill | 单个三级原子能力的输入与输出 | 单独查看某一项能力的效果和稳定性。 |
| 二级节点 | 一个二级节点下一个或多个 Skill 的组合产物 | 查看一个完整科研动作是否形成了可供下游继续使用的结果。 |
| 端到端链路 | 从科研问题到机制链，后续逐步扩展到科研假说和结果包 | 查看完整链路是否真正帮助科研人员形成下一步判断。 |

---

## 13. 理解输出时需要知道的系统边界

以下内容是系统产物的语义边界，接入时需要统一理解。

| 边界 | 含义 |
|---|---|
| 已有结论、科研线索、待验证假说必须分开 | 论文和现有证据已经明确支持的内容属于已有结论；基于机制链推导出的方向属于科研线索；尚未验证的判断属于假说。 |
| 关键结果必须可追溯 | 证据使用 `evidence_id`；机制边使用 `mechanism_edge_id`；后续结论、线索和假说需要绑定已有证据与机制边。 |
| 原文证据必须可核验 | 证据节点保留原文定位信息；短引文需要能够回到原文核查。 |
| 支持、冲突和缺口需要显式保留 | 机制链不仅记录支持关系，也记录相互冲突的证据和当前证据缺口。 |
| 待验证假说不等于论文已有结论 | 论文已报告的结果不能直接改写为新增假说。 |
| 验证方案生成不等于执行实验 | 后续验证方案只负责提出最小验证路径，不直接调用实验设备、计算平台或优化器。 |

---

## 14. 一句话记住 OpenTrajectory

```text
OpenTrajectory 将科研问题逐步转化为可追溯证据、可解释机制链和可验证科研假说，帮助科研人员更可靠地决定下一步做什么。
```
