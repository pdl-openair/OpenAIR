# Amplify 研究工作流完整指南

> 7 个 Phase + 4 个 Gate 的一次性参考文档。每个 Phase 列出：目标、核心步骤、交付文件、关联技能。每个 Gate 列出：完整 Checklist。

---

## 全局流程概览

```
Phase 0          Phase 1               Phase 2              Phase 3            Phase 4a        Phase 4b             Phase 5            Phase 6
领域锚定    →    方向探索        →    问题验证       →    方法/分析设计  →  探索性验证  →   全规模执行     →   结果整合     →   论文写作
(Domain         (Direction            (Problem             (Method &           (Exploratory      (Full               (Results           (Paper
Anchoring)      Exploration)          Validation)          Framework Design)    Stage)            Execution)          Integration)       Writing)
                  │                                         │                                      │                    │
                  ▼                                        ▼                                      ▼                    ▼
                 G1                                       G2                                    G3                  G4
              (Topic &                                (Plan Freeze)                      (Execution          (Write-Ready)
               Venue)                                                                    Complete)
```

### 非线性迭代路径

研究不是直线。Amplify 支持结构化回退：

| 触发点 | 回退目标 | 说明 |
|--------|---------|------|
| Phase 2 验证失败 | → Phase 1 | 携带失败原因作为搜索约束，不从头开始 |
| Phase 4a 探索发现设计问题 | → Phase 3（局部调整/针对性讨论） | 最常见；仅极端情况回 Phase 2 |
| Phase 4a 探索发现问题本身有误 | → Phase 2 | 极少用 |
| Phase 5 评审发现缺失实验 | → Phase 4 | 仅跑补充实验，不重跑全部 |
| Phase 6 写作评审发现缺失实验 | → Phase 4 | 同上 |

---

## 研究类型速查

| 类型 | 代号 | 核心驱动 | 例子 |
|------|------|---------|------|
| 方法 | M | 性能驱动，需基线+消融+统计显著性 | "新的少样本学习方法" |
| 发现 | D | 故事驱动，需分析广度+机制探索+排除替代解释 | "癌症中的基因表达模式" |
| 工具 | C | 效用驱动，需正确性+性能+可扩展性+竞品比较 | "分子对接管线" |
| 混合 | H | 双轨道，需 M+D 两套 | "新的疾病亚型整合方法" |

---

## Phase 0 — 领域锚定（Domain Anchoring）

**技能：** `domain-anchoring`

**目标：** 确定研究领域、研究类型、生成专家角色。

**核心步骤：**
1. 从用户描述中识别领域和子领域
2. 确认研究类型（M / D / C / H）
3. 生成领域专家角色（评审关注点）
4. 预估目标会议/期刊

**交付文件：**

| 文件 | 说明 |
|------|------|
| `docs/01_intake/research-anchor.yaml` | 项目锚点（域、类型、专家角色、目标会议、资源） |

`research-anchor.yaml` 关键字段（Phase 0 填写）：

```yaml
domain: ""                    # 领域
subdomain: ""                 # 子领域
research_type: ""             # M / D / C / H
expert_persona: ""            # 专家角色
reviewer_focus: []            # 评审关注点
target_venue:
  primary: ""                 # 目标会议/期刊
  tier: ""                    # A / B / C
resources:
  compute: ""                 # 计算资源
  data_status: ""             # 数据状态
```

---

## Phase 1 — 方向探索（Direction Exploration）

**技能：** `research-direction-exploration`

**目标：** 文献综述 → 深度思考 → 多想法生成 → 多智能体头脑风暴 → 用户选定方向。

**核心步骤：**

| 步骤 | 内容 |
|------|------|
| 入口路径 | Path A（模糊方向）/ Path B（明确目标）/ Path C（提供数据） |
| Step 1 — 广度搜索 | arXiv、Google Scholar、Semantic Scholar，收集 15-30 篇候选 |
| Step 2 — 全文获取 | 自主下载 PDF → HTML 全文 → API → 请求用户协助（分级：`full_text` / `abstract_only` / `download_failed`） |
| Step 3 — 用户协助 | 仅针对获取失败的论文，提供下载/摘要/跳过三种选项 |
| Step 4 — 深度阅读 | 5-10 篇最相关论文，提取问题/方法/数据集/指标/结果/局限 |
| Step 5 — Gap 分析 | 每个空缺验证是否已解决，附置信度 |
| Step 5b — 深度思考策略 | 6 种策略全部应用：矛盾挖掘、假设质疑、跨域迁移、局限转化、反事实推理、趋势外推 |
| Step 5c — 多想法生成 | 综合洞察为 ≥5 张结构化 Idea 卡片 |
| Step 5d — 多智能体头脑风暴 | Visionary Researcher + Pragmatic Advisor + Field Scout，最多 5 轮，输出 2-3 个精炼 idea |
| Step 6 — 元素收集 | Type M/H：备选方法、基准、指标、SOTA；Type D：分析方法、领域标准 |
| Step 7 — 基线早期确认 | Type M/H：主动询问用户是否有指定基线/已有结果 |

**交付文件：**

| 文件 | 说明 |
|------|------|
| `docs/02_literature/paper-list.md` | 15-30 篇候选论文，含 URL 和获取状态 |
| `docs/02_literature/literature-review.md` | 深度阅读表（5-10 篇） |
| `docs/02_literature/gap-analysis.md` | Gap 分析，含置信度 |
| `docs/02_literature/deep-thinking-insights.md` | 6 种策略的原始洞察（10-20 条） |
| `docs/02_literature/brainstorming-results.md` | 5 张 idea 卡 + 3 智能体审议结果 + 最终排名 |
| `docs/02_literature/baseline-collection.md` | Type M/H 基线列表（含 `user_specified` / `[PRE-COMPUTED]` 标签） |
| `docs/02_literature/papers/` | 已下载的 PDF |

**数据与资源就绪检查（离开 Phase 1 前必做）：**

| 项目 | 状态 | 未就绪时的行动 |
|------|------|-------------|
| 数据可用性 | user-provided / public / needs collection | 提供下载脚本或讨论可行性 |
| 计算资源 | sufficient / insufficient | 讨论替代方案或缩减范围 |
| 工具链 | ready / gaps | 识别缺失工具，安装或替代 |
| 基线（Type M/H） | user-specified / system-recommended / pending | 无则重访文献 |

**初步新颖性评估：** 调用 `novelty-classifier`，如果是"工程整合"或"规模扩展"→ 在 G1 前警告用户。

---

## G1 Gate — 主题与会议（Topic & Venue）

**位置：** Phase 1 与 Phase 2 之间。

**全部通过后 → 调用 `problem-validation` 进入 Phase 2。**

### Checklist

**— 研究定位 —**
- [ ] 领域和子领域已识别且用户确认
- [ ] 研究类型已确认（M / D / C / H）
- [ ] 专家角色已锚定（`research-anchor.yaml` 中 `reviewer_focus` 已设置）

**— 文献与洞察 —**
- [ ] 文献综述完成（广度搜索 + 深度阅读）
- [ ] 研究空缺已识别且验证（未被已有工作解决）
- [ ] 6 种深度思考策略已应用（Step 5b 洞察已记录）
- [ ] 至少 5 个候选 idea 已生成（Step 5c idea 卡片已记录）
- [ ] 多智能体头脑风暴已完成（Step 5d — 前 2-3 个 idea 已精炼）

**— 用户决策 —**
- [ ] 用户已从头脑风暴结果中选定研究方向
- [ ] 用户已批准方向并同意推进

**— 前置评估 —**
- [ ] 初步新颖性评估已完成（`novelty-classifier` 已调用）
- [ ] 目标会议/期刊已预估（附理由）
- [ ] 项目意图清晰（什么贡献，给谁）

**— 资源就绪 —**
- [ ] 数据和资源已评估为就绪
- [ ] `research-anchor.yaml` 已生成并保存在 `docs/01_intake/`

---

## Phase 2 — 问题验证（Problem Validation）

**技能：** `problem-validation`

**目标：** 对选定方向进行对抗性审查，确保研究问题值得投入资源。

**此阶段不可跳过，对所有研究类型（M/D/C/H）均为必需。**

**核心步骤：**

| 步骤 | 内容 |
|------|------|
| Pre-Step | 处理用户指定的问题：已知答案 → 告知并提供替代；有效但弱 → 继续审议；强且具体 → 正常进行 |
| Step 0 — 新颖性快速测试 | "如果项目完美成功，什么**新知识**是之前不存在的？" 不通过 → 必须重构，不接受"Phase 3 再找问题" |
| Step 0b — 数据新颖性预筛 | Type D 必须：检查数据集是否被过度分析（如 PBMC 3k、MNIST），是 → 警告用户提供替代 |
| Step 1 — 3 智能体审议 | Senior Professor + Journal Editor + Research Scientist（Type C 为 Target User + Editor + Software Architect），最多 5 轮 |
| Step 2 — 项目意图分类 | 用户确认一个主要意图：Performance / Efficiency / Quality / Discovery / Engineering |
| Step 3 — 会议目标精调 | 重新评估目标会议是否仍然合理，不足 → 推荐降级并附理由 |
| Step 4 — 最终可行性评估 | 6 项全部 ✅ 才能通过 |

**交付文件：**

| 文件 | 说明 |
|------|------|
| `research-anchor.yaml`（更新） | `project_intent` 已填写 |

**3 智能体审议的关键规则：**
- Professor 连续 2 轮 FAIL → 立即升级给用户（不等第 5 轮）
- Editor 说 "desk-reject" → 立即升级
- Scientist 说 "infeasible" → 立即升级
- 质量检查：最终问题不可匹配"提供完整证据链"/"分析可复现"/"使用标准工具标准数据"等模糊模式

**升级选项（审议不收敛时）：**
1. 返回 Phase 1（携带失败原因）
2. 继续精炼（提出新的问题表述）
3. 按现状推进（记录已知局限，调整目标会议）
4. 暂停

---

## Phase 3 — 方法/分析设计（Method & Framework Design）

**技能：** `method-framework-design`、`evaluation-protocol-design`、`analysis-storyboard-design`

**目标：** 按研究类型分支设计完整方案，锁定评估协议。

**核心原则：** G2 之前禁止写任何实现代码。

### Type M 路径（方法开发）

| 步骤 | 内容 |
|------|------|
| M-Step 1 | 锁定评估协议 → `evaluation-protocol.yaml`（指标、协议、种子、统计、数据集、基线、完成标准、公平性约束），`locked: true` |
| M-Step 2 | 设计创新方法（创新点限 1-2 个），写入 `innovation_point` 和 `value_proposition` |
| M-Step 3 | 确认基线 + 检查已有结果的兼容性（`accepted` / `reference_only` / `incompatible`） |
| M-Step 4 | 消融实验计划 |
| M-Step 4b | （可选）理论分析计划 |
| M-Step 5 | 3 智能体方法设计审议（Innovation Advisor + Technical Architect + Baseline Devil's Advocate），最多 5 轮 |
| M-Step 6 | 确认故事线 |

**智能体：** Innovation Advisor + Technical Architect + Baseline Devil's Advocate

### Type D 路径（发现/数据分析）

| 步骤 | 内容 |
|------|------|
| D-Step 0 | 数据选择验证（非过度分析数据集、有足够复杂度） |
| D-Step 1 | 设计分析框架（描述性 → 主分析 → 机制 → 稳健性 → 探索性） |
| D-Step 2 | 定义充分性标准（8 项全部勾选） |
| D-Step 3 | 预识别替代解释（每个主要发现 ≥2 个） |
| D-Step 4 | 3 智能体分析设计审议（Domain Scientist + Methodology Consultant + Statistical Rigor Advisor），最多 5 轮 |
| D-Step 5 | 确认故事线 |

**智能体：** Domain Scientist + Methodology Consultant + Statistical Rigor Advisor

### Type C 路径（工具/软件）

| 步骤 | 内容 |
|------|------|
| C-Step 1 | 定义效用声明（含量化目标 ≥2 个） |
| C-Step 2 | 设计评估框架（正确性、性能、可扩展性、比较、案例研究、可用性） |
| C-Step 3 | 识别竞品工具 |
| C-Step 4 | 3 智能体工具设计审议（Target User + Competing Tool Expert + Software Quality Advisor），最多 5 轮 |
| C-Step 5 | 确认故事线 |

**智能体：** Target User + Competing Tool Expert + Software Quality Advisor

### Type H 路径（混合）

执行 M + D 两条轨道。先确认主轨道（`primary_track`），两轨道审议可顺序或并行。

### 交付文件

| 文件 | 适用类型 | 说明 |
|------|---------|------|
| `docs/03_plan/evaluation-protocol.yaml` | M / C / H | 评估协议，`locked: true` |
| `docs/03_plan/method-design.md` | M / H | 创新方法设计、失败模式、后备方案 |
| `docs/03_plan/ablation-design.md` | M / H | 消融计划 |
| `docs/03_plan/theoretical-analysis-plan.md` | M（可选） | 定理、证明策略、假设、回退方案 |
| `docs/03_plan/analysis-storyboard.md` | D / H | 主故事线 + ≥2 支撑线 + 充分性标准 + 替代解释表 |
| `research-anchor.yaml`（更新） | 全部 | `value_proposition`、`innovation_point`、`story_line`、`primary_track`（H） |

---

## G2 Gate — 计划冻结（Plan Freeze）

**位置：** Phase 3 与 Phase 4 之间。

**通过后激活 `metric-lock` 技能。全部通过后 → 进入 Phase 4。**

### Checklist — Type M 专属

- [ ] `evaluation-protocol.yaml` 已锁定（`locked: true`）
- [ ] 创新方法已充分论证，`innovation_point` 已标记
- [ ] 基线已确认（来源：`official_repo` / `our_implementation` / `paper_reported`）
- [ ] 已有结果（如有）已做兼容性检查（`accepted` / `reference_only` / `incompatible`）
- [ ] 消融实验计划已记录
- [ ] 失败模式已预测，每项有后备方案
- [ ] 3 智能体方法设计审议已完成

### Checklist — Type D 专属

- [ ] 数据选择已验证（非过度分析数据集、有足够复杂度支撑发现）
- [ ] 分析框架和故事板已冻结
- [ ] 主故事线 + ≥2 条支撑线已确认
- [ ] 充分性标准已定义且全部勾选：
  - [ ] 描述性分析覆盖完整数据概览
  - [ ] 主假设从多角度检验
  - [ ] 探索性分析已规划
  - [ ] 所有支撑线连接回主故事
  - [ ] ≥4-6 个内容点，含计划图表
  - [ ] 混杂因素已识别并处理
  - [ ] 每个声明有充分统计支持
- [ ] 替代解释已预识别（每个主要发现 ≥2 个）
- [ ] 3 智能体分析设计审议已完成

### Checklist — Type C 专属

- [ ] 效用声明已陈述，含量化目标（≥2 个）
- [ ] 评估框架已定义（正确性、性能、可扩展性、比较）
- [ ] 竞品工具已识别并比较（≥2 个）
- [ ] 案例研究已规划（2-3 个）
- [ ] 文档计划已就位
- [ ] 3 智能体工具设计审议已完成

### Checklist — Type H 额外

- [ ] 已标记主轨道（`primary_track`：`method` 或 `discovery`）
- [ ] M 和 D 两条轨道的 checklist 全部满足

### Checklist — 通用（所有类型必选）

- [ ] 故事线已由用户确认
- [ ] `value_proposition` 已写入 `research-anchor.yaml`
- [ ] 目标会议/期刊仍然合理（`venue-alignment` 已调用）
- [ ] `metric-lock` 技能已激活
- [ ] 用户已明确批准所有内容

---

## Phase 4 — 实验执行（Experiment Execution）

**技能：** `experiment-execution`

**目标：** 两阶段执行：4a 探索性验证 → 4b 全规模执行。

**全局约束（注入每个子智能体）：**
1. **VALUE_ANCHOR** — 所有工作服务于 `value_proposition`
2. **METRIC_LOCK** — 主指标不可变
3. **VENUE_ALIGNMENT** — 实验规模匹配目标会议
4. **ANTI_CHERRY_PICK** — 所有种子、所有结果、所有失败必须记录
5. **PREMATURE_EXPERIMENT_GUARD** — 基线必须先于自身方法复现

### Phase 4a — 探索性阶段（Exploratory Stage）

**此阶段不可跳过。** 目标是在全规模投入前验证计划可行性。

#### Type M

1. 搭建最小基础设施（数据加载、评估函数、种子控制）
2. 运行 1-2 个最强基线（全执行，非玩具运行）
3. 错误分析：实际失败模式 vs Phase 3 预测
4. 可选：快速原型验证核心想法
5. 可选：理论分析草稿

#### Type D

1. 预处理 + 质量控制
2. 跑一次核心分析管线
3. 检查结果合理性（领域知识对照）
4. 评估 Phase 3 分析计划是否仍适用

#### Type C

1. 跑 1 个基准测试，对比 1-2 个竞品
2. 测试安装是否顺利

**交付文件：** `docs/05_execution/phase4a-exploration-report.md`

**⚠ 强制停止点 — 用户决策**

Phase 4a 结束后必须展示探索报告，用户从 5 个选项中选择：

| 选项 | 适用场景 |
|------|---------|
| 1. 按计划推进至 4b | 结果符合预期 |
| 2. 局部调整（最常见） | 需增改部分实验，无需重做 Phase 3 |
| 3. 针对性重新讨论 | 特定问题需 Phase 3 智能体针对性讨论（最多 2-3 轮） |
| 4. 返回 Phase 3 | 方法/分析设计有根本性问题 |
| 5. 返回 Phase 2 | 研究问题本身有误（极少用） |

### Phase 4b — 全规模执行（Full Execution）

#### Type M 执行流程

| 阶段 | 内容 | 规则 |
|------|------|------|
| **Stage A** — 搭建实验框架 | 数据管线、评估函数、日志、可复现脚手架 | — |
| **Stage B** — 基线复现 | 先检查 `accepted`/`reference_only`/`incompatible` 状态 + Phase 4a 已跑基线，仅补跑剩余基线。与论文值偏差 >5% → 停止调试 | 基线优先，无例外 |
| **Stage C** — 实现核心方法 | 最小可用版本 → 单元测试 → 增量构建 + 可选理论证明 | 每个组件可独立测试 |
| **Stage D** — 初始实验 + 迭代 | 小规模验证 → 若无改善 → 强制迭代循环（≥3 轮 diagnose-hypothesize-fix-measure） | 严禁报告未跑完的结果 |
| **Stage E** — 全规模实验 | 全数据集 × 全方法 × 全种子 + 消融 + 参数敏感性 + 效率分析 + 领域陷阱检查 | 用户确认方法可用 + 达到最低性能门槛后才能进入 |

**迭代规则：**
- 每轮必须：诊断 → 假设 → 单一修改 → 测量 → 向用户报告
- 用户必须确认每轮报告后才能开始下一轮
- 连续 3 次失败 → 触发 `pivot-or-kill`
- 最低性能门槛：主指标达到最佳基线的 80%

#### Type D 执行流程

| 阶段 | 内容 |
|------|------|
| **Stage A** — 预处理 + 质控 | 原始数据不修改，每步脚本化 |
| **Stage B** — 执行分析框架 | 按故事板顺序：描述性 → 主分析 → 机制 → 稳健性 → 探索性。每步生成图表 |
| **Stage C** — 排除替代假设 | 调用 `alternative-hypothesis-check` |
| **Stage D** — 充分性自查 | 对照 Phase 3 充分性标准逐项检查。≥2 轮结果展示。领域合理性检查 |

**Type D 领域合理性检查（所有结果声明前必做）：**
- 结果是否符合基本领域知识？
- 异常结果是否是真正的新发现而非伪影？
- 领域专家看到结果会立刻认为"有问题"吗？

#### Type C 执行流程

| 阶段 | 内容 |
|------|------|
| **Stage A** — 实现 + 测试 | 核心实现 + 单元/集成测试 + 边界情况 + 文档 + 打包 |
| **Stage B** — 基准对比 | 全部竞品 × 相同基准（正确性、性能、可扩展性） |
| **Stage C** — 案例研究 | 2-3 个真实场景 |
| **Stage D** — 可用性审查 | 干净环境安装测试、教程演练、API 一致性 |

### 交付文件

| 文件 | 说明 |
|------|------|
| `docs/05_execution/experiment-log.md` | 实验日志 |
| `docs/05_execution/baseline-results.md` | 基线结果汇总表 |
| `docs/05_execution/negative-results.md` | 负面结果记录 |
| `docs/05_execution/phase4a-exploration-report.md` | 4a 探索报告 |
| `docs/05_execution/theoretical-analysis.md` | 理论分析（可选） |
| `docs/05_execution/benchmark-results.md` | 基准对比（Type C） |

---

## G3 Gate — 执行完成度（Execution Complete）

**位置：** Phase 4b 完成后、Phase 5 之前。

**全部通过后 → 进入 Phase 5（结果整合）。**

### Checklist

**— 执行完整性 —**
- [ ] Phase 4a 探索性阶段已完成，探索报告已生成
- [ ] Phase 4a 决策点已通过（用户已选择推进/调整方案）
- [ ] Phase 4b 全规模执行已完成
- [ ] 所有计划内的实验/分析已执行（无遗漏的计划运行）
- [ ] **[Type M]** 所有方法**跑完**（非部分/提前停止运行）
- [ ] **[Type M]** 基线实际跑过（非从论文复制不同设置的数字）
- [ ] **[Type C]** 正确性已验证，基准测试和案例研究已完成

**— 数据与结果 —**
- [ ] 数据质量报告已生成 — `docs/04_data_resource/data-quality-report.md` 已存在
- [ ] **[ML 项目]** 泄露审计通过 — `docs/04_data_resource/leakage-audit.md` 确认无数据泄露
- [ ] 所有种子已运行并报告（无挑选）
- [ ] 负面结果已记录在 `docs/05_execution/negative-results.md`

**— 竞争力（Type M）—**
- [ ] 方法在主指标上至少与一个基线具有竞争力（不严重落后于所有基线）
- [ ] 消融实验已完成（每个关键组件已测试）

**— 充分性（Type D）—**
- [ ] 分析充分性标准全部满足（对照 Phase 3 充分性清单）
- [ ] 替代解释已有证据回应
- [ ] 领域合理性检查通过

**— 用户确认 —**
- [ ] 用户已审阅结果摘要
- [ ] 用户确认可进入结果整合阶段

任何一项失败 → **停止**，回 Phase 4 解决后再推进。

---

## Phase 5 — 结果整合（Results Integration）

**技能：** `results-integration`、`claim-evidence-alignment`、`figure-quality-standards`

**目标：** 将原始结果转化为结构化的论点蓝图，作为 Phase 6 写作的直接输入。

### 核心原则

**Phase 5 设计论文的故事，Phase 6 执行写作。** Phase 6 不应从头发现故事。

### 可发表性检查（整合前必做）

> "这些结果是否包含至少一个领域专家在看到数据前**不会预测到**的发现？"

Type C 等价问题："这个工具是否**明确解决了**现有工具不能（或明显更差地解决的问题？"

答案为否 → 三个选项：深化分析 / 转换角度 / 诚实降级

### 预整合验证（所有类型）

- [ ] [Type M] 所有方法跑完（非部分/提前停止）
- [ ] [Type M] 方法与基线性能在合理范围内（如 0.42 vs 0.95 → 不合格，回 Phase 4）
- [ ] [Type C] 工具正确性已验证
- [ ] [Type C] 基准测试和案例研究已完成
- [ ] [Type M] 基线实际跑过（非从论文复制不同设置的数字）
- [ ] [Type D] 所有计划分析已执行
- [ ] [All] 所有种子已运行
- [ ] [All] 负面结果已记录

任何检查失败 → **回 Phase 4**，不可"绕过写作"。

### 按类型整合步骤

**Type M：** M-Step 1（汇总结果表）→ M-Step 2（解释：为什么有效 / 在哪里优/劣 / 消融揭示什么）→ M-Step 3（声明-证据对齐）

**Type D：** D-Step 1（沿故事线组织）→ D-Step 2（构建证据链：观察 → 分析 → 发现 → 机制 → 验证）→ D-Step 3（声明-证据对齐）

**Type C：** C-Step 1（汇总基准结果）→ C-Step 2（量化优势："X倍快"/"处理Y倍大输入"）→ C-Step 3（声明-证据对齐）

**Type H：** 执行 M 步骤 + D 步骤 + 跨轨道整合

### Step 4 — 多智能体故事设计审议（自动化，必需）

**这是论文故事设计的核心步骤，不可跳过或推迟到 Phase 6。**

**智能体：**

| 智能体 | 角色 | 优化目标 |
|--------|------|---------|
| Story Architect | 故事架构师 | 设计完整的论点结构 |
| Devil's Advocate | 魔鬼代言人 | 找出每个漏洞，标记严重性（fatal / major / minor） |
| Audience Specialist | 受众专家 | 评估是否满足目标会议要求 |

**流程：** Round 1（三个智能体并行）→ 综合为 Argument Blueprint → Round 2+（最多 5 轮，全部智能体每轮看到完整蓝图）→ 收敛或升级给用户。

**实验补充分类（Round 1 后必做）：**

| 优先级 | 处理方式 |
|--------|---------|
| REQUIRED | 必须回 Phase 4 补充，否则不能写论文 |
| STRONGLY RECOMMENDED | 用户决定，若跳过须记入 `acknowledged-gaps.md` |
| Nice to have | 记为未来工作 |

**致命漏洞硬规则：** Devil's Advocate 标记 "fatal" → **禁止**进入论文写作。必须提供：深化分析 / 转向角度 / 降级为技术报告 / 停止。**静默重新包装为"基准测试论文"绕过新颖性失败 → 禁止。**

### 交付文件

| 文件 | 说明 |
|------|------|
| `docs/06_integration/argument-blueprint.md` | 论文论点蓝图（电梯演讲、核心论点、4-6 个内容点、叙事弧、局限性、已确认缺口） |
| `docs/06_integration/acknowledged-gaps.md` | 用户选择跳过的实验补充（须出现在论文 Limitations 章节） |

**内容大纲最低要求：**
- ≥3 张图
- ≥2 个表
- ≥4 个有实质内容的内容点（每个含 CLAIM + EVIDENCE + INTERPRETATION）

---

## G4 Gate — 论文就绪（Write-Ready）

**位置：** Phase 5 与 Phase 6 之间。写作前的最后关卡。

**全部通过后 → 调用 `paper-writing` 进入 Phase 6。**

### Checklist — 结果质量

- [ ] 核心实验/分析已完成（无遗漏的计划运行）
- [ ] **[Type M]** 所有方法跑完（非部分/提前停止）
- [ ] **[Type M]** 方法在主指标上至少超过一个基线，有统计支持（p 值/置信区间）
- [ ] **[Type M]** 消融实验完成 — 每个关键组件已测试
- [ ] **[Type D]** 故事线证据链完整 — 无叙事缺口
- [ ] **[Type D]** 替代解释已有证据回应

### Checklist — 新颖性门槛

- [ ] 3 智能体故事设计审议已完成
- [ ] Devil's Advocate 发现**零个 "fatal" 级别漏洞**（如有 fatal 漏洞，已通过深化/转向/用户批准降级解决）
- [ ] 至少有一个"领域专家在看到数据前不会预测到"的发现

### Checklist — 验证

- [ ] `claim-evidence-alignment` 已通过 — 完整的声明-证据映射表已审阅
- [ ] `evaluation-protocol.yaml` 中的所有种子已运行并报告（无挑选）
- [ ] 负面结果已记录在 `docs/05_execution/negative-results.md`

### Checklist — 内容就绪

- [ ] 4-6+ 个内容点，每个有配套图表
- [ ] 至少 **3 张图**已制作且就绪
- [ ] 至少 **2 个表**已制作且就绪
- [ ] 内容大纲已展示给用户并**明确批准**

### Checklist — 用户确认

- [ ] 用户明确说了 "ready to write paper"（或清晰等效表达）
- [ ] 目标会议/期刊最终确认："[venue] — 仍然正确？"

**硬规则：**
- 用户未说 "ready to write paper" → G4 不可通过。"ok"/"looks good" 不算
- G4 通过后 → 强制停止，展示 checklist，等待用户确认后才能进入 Phase 6

---

## Phase 6 — 论文写作（Paper Writing）

**技能：** `paper-writing`

**目标：** 基于 Phase 5 的论点蓝图，模块化 LaTeX 写作，每节独立 3 智能体润色。

**核心步骤：**

| 步骤 | 内容 |
|------|------|
| 设置 | 基于 `argument-blueprint.md` 创建模块化 LaTeX 结构（每节一个 `.tex` 文件） |
| 逐节写作 + 润色 | 每节：写初稿 → 派 3 智能体（Domain Expert + Writing Editor + Adversarial Reviewer） → 综合 → 重写 → 再审（最多 5 轮） → 展示润色版本 → **停止等用户反馈** |
| 参考文献验证 | 每条引用必须可验证，禁止编造 |
| 全文审阅 | 所有节完成后，全文 3 智能体审阅 |
| 最终批准 | 用户最终批准 |

**交付文件：**

| 文件 | 说明 |
|------|------|
| `paper/main.tex` | 主 LaTeX 文件 |
| `paper/sections/*.tex` | 每节独立 `.tex` |
| `paper/figures/` | 出版质量图表（≥300 DPI，矢量格式，色盲友好） |
| `paper/references.bib` | 参考文献库 |

**最低论文质量要求：**
- ≥3 张图，≥2 个表，≥20 条参考文献
- Introduction ≥600 词，Related Work ≥500 词，Discussion ≥400 词
- 硬性词数下限：不足即失败，不接受"差不多"
- 所有节（含 Discussion、Related Work、Abstract、Conclusion）获得完整 3 智能体润色

**同方法 = 同颜色：** 所有图表中同一方法必须使用相同颜色。

---

## 附录：完整交付文件清单

```
docs/
├── 01_intake/
│   └── research-anchor.yaml                  # Phase 0 生成，后续 Phase 持续更新
├── 02_literature/
│   ├── paper-list.md                         # Phase 1
│   ├── literature-review.md                  # Phase 1
│   ├── gap-analysis.md                       # Phase 1
│   ├── deep-thinking-insights.md             # Phase 1
│   ├── brainstorming-results.md              # Phase 1
│   ├── baseline-collection.md                # Phase 1（Type M/H）
│   └── papers/                               # Phase 1 下载的 PDF
├── 03_plan/
│   ├── evaluation-protocol.yaml              # Phase 3（M/C/H），locked: true
│   ├── method-design.md                      # Phase 3（M/H）
│   ├── ablation-design.md                    # Phase 3（M/H）
│   ├── theoretical-analysis-plan.md          # Phase 3（M，可选）
│   └── analysis-storyboard.md               # Phase 3（D/H）
├── 04_data_resource/
│   ├── data-quality-report.md                # Phase 4 期间必需
│   └── leakage-audit.md                      # Phase 4 期间必需（ML 项目）
├── 05_execution/
│   ├── phase4a-exploration-report.md         # Phase 4a
│   ├── experiment-log.md                     # Phase 4b
│   ├── baseline-results.md                   # Phase 4b（M/H）
│   ├── negative-results.md                   # Phase 4b
│   ├── theoretical-analysis.md               # Phase 4b（可选）
│   └── benchmark-results.md                  # Phase 4b（C）
└── 06_integration/
    ├── argument-blueprint.md                  # Phase 5
    └── acknowledged-gaps.md                   # Phase 5（用户跳过的补充实验）

paper/
├── main.tex
├── sections/*.tex
├── figures/
└── references.bib
```

## 附录：多智能体审议汇总

| Phase | 智能体组合 | 审议对象 | 最大轮数 |
|-------|-----------|---------|---------|
| 1 | Visionary Researcher + Pragmatic Advisor + Field Scout | 候选研究 idea | 5 |
| 2 | Senior Professor + Journal Editor + Research Scientist | 研究问题 | 5 |
| 2 (Type C) | Target User + Editor + Software Architect | 研究问题 | 5 |
| 3 (Type M) | Innovation Advisor + Technical Architect + Baseline Devil's Advocate | 方法设计 | 5 |
| 3 (Type D) | Domain Scientist + Methodology Consultant + Statistical Rigor Advisor | 分析设计 | 5 |
| 3 (Type C) | Target User + Competing Tool Expert + Software Quality Advisor | 工具设计 | 5 |
| 5 | Story Architect + Devil's Advocate + Audience Specialist | 故事与论点结构 | 5 |
| 6 | Domain Expert + Writing Editor + Adversarial Reviewer | 每节论文内容 | 5 |
