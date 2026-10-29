# SDR (Spec-Driven Research) 模板说明

## 设计哲学

核心理念和 OpenSpec 一致：**先写规范，再动手做**。但面向的不是写代码，而是做科研。

SDR 的链路是：

```
问题定义(problem) → 方法设计(method) → 实验分析(experiment) → 报告撰写(report)
```

**规约管研究流程，技能管具体执行**（论文撰写、画图、统计分析等属于技能，不在规约范围内）。

## 研究类型（M/D/C/H）

SDR 按研究产出目标分为四类，决定各阶段的内容侧重和 checklist 条目：

| 类型 | 代号 | 核心驱动 | 例子 |
|------|------|---------|------|
| 方法 | M | 性能驱动，需基线+消融+统计显著性 | "新的少样本学习方法" |
| 发现 | D | 故事驱动，需分析广度+机制探索+排除替代解释 | "癌症中的基因表达模式" |
| 工具 | C | 效用驱动，需正确性+性能+可扩展性+竞品比较 | "分子对接管线" |
| 混合 | H | 双轨道，需 M+D 两套 | "新的疾病亚型整合方法" |

在 `research-config.yaml` 中声明研究类型。

## 六类 ID 与追溯链路

六类 ID 分布在三个阶段，形成从尾到头的追溯链：

```
阶段          ID类型    含义        方向
──────────────────────────────────────────────
01-problem    G-xxx   研究空缺     "文献中缺什么？"
01-problem    Q-xxx   研究问题     "我要回答什么？"
01-problem    H-xxx   研究假设     "我猜答案是什么？"
02-method     M-xxx   方法步骤     "我怎么验证？"
03-experiment A-xxx   实验产出     "我拿到了什么数据？"
03-experiment F-xxx   研究发现     "数据告诉我什么？"
```

追溯方向（从尾到头）：

```
F-001 ← A-001 ← M-001 ← H-001 ← Q-001 ← G-001
 发现     产出     方法     假设     问题     空缺
```

每条发现必须能一路追回到它所回应的原始研究空缺。每层是 **1:N** 关系——一个空缺可以衍生多个问题，一个问题可以有多条假设，一条假设可以有多个验证步骤，一个步骤可以产出多个数据，多个数据可以汇集成一条发现。

### 追溯示例

```
F-001: "r=0.72, p<0.01，运动频率与睡眠质量显著正相关"
  ↑ 证据来自
A-001: survey-responses.csv (hash:sha256:abc...)
  ↑ 由以下步骤产出
M-003: 问卷数据采集 (n=500, 线上发放)
  ↑ 用于验证
H-001: "规律运动者的睡眠评分显著高于不运动者"
  ↑ 回答
Q-001: "运动习惯如何影响睡眠质量？"
  ↑ 源于
G-001: "现有文献未同时考察运动频率与睡眠质量的量化关系"
```

## 文件结构

```
research-project/
├── research-config.yaml                          # 项目配置
├── 01-problem/
│   ├── research-question.md             # 研究问题与假设
│   ├── literature-review.md             # 文献综述
│   ├── gap-analysis.md                  # 研究空缺分析（G-ID）
│   └── checklist.md                     # 审查清单
├── 02-method/
│   ├── method_design.md                 # 方法论与变量定义
│   ├── experiment_design.md             # 数据策略 + 分析计划 + 基线 + 消融 + 可复现性
│   ├── task.md                          # 可执行步骤 + 实验矩阵
│   └── checklist.md
└── 03-experiment/
    ├── execution.md                     # 执行实录（4a 探索 + 4b 全规模）
    ├── analysis.md                      # 分析与发现（六层追溯矩阵）
    ├── argument-blueprint.md            # 论点蓝图（故事线 + 声明-证据映射）
    └── checklist.md
```

## 逐文件说明

### research-config.yaml — 项目控制中心

声明研究类型（M / D / C / H）、研究域、目标会议、计算资源、AI 参与模式（copilot / autopilot）、最低成功标准、以及哪些阶段需要人工 checkpoint 审批。

### 01-problem — 问题发现

| 文件                     | 作用                                      |
| ---------------------- | --------------------------------------- |
| `research-question.md` | 定义研究背景、研究问题（Q-ID）、可证伪假设（H-ID）、预期贡献、范围约束 |
| `literature-review.md` | 检索策略、文献摘要表格                             |
| `gap-analysis.md`      | 研究空缺识别（G-ID）、置信度、空缺到问题的映射             |
| `checklist.md`         | 审查清单（含按研究类型的专属条目）           |

关键设计：假设必须声明**证伪标准**，不是空话。每个问题必须追溯到研究空缺（G-ID）。

### 02-method — 方法设计

| 文件                      | 作用                                        |
| ----------------------- | ----------------------------------------- |
| `method_design.md`      | 方法论选型、变量定义                             |
| `experiment_design.md`  | 数据策略、**预注册分析计划**、基线选择（Type M/C/H）、消融实验计划（Type M/H）、可复现性保障、风险预案 |
| `task.md`               | 从 method_design.md 和 experiment_design.md 拆解出的可执行步骤清单（M-ID）+ 实验矩阵（R-ID） |
| `checklist.md`          | 审查清单（含按研究类型的专属条目）            |

关键设计：
- 分析计划在执行前声明，防止 p-hacking 和 HARKING
- `method_design.md` 纯粹包含方法论与变量定义
- `experiment_design.md` 集中管理所有实验相关设计：数据、分析计划、基线、消融、可复现性、风险
- `task.md` 中的实验矩阵是机器可查的必须完成列表，内容从 `method_design.md` 和 `experiment_design.md` 拆解而来

### 03-experiment — 实验执行

| 文件                      | 作用                                      |
| ----------------------- | --------------------------------------- |
| `execution.md`          | 4a 探索性执行 + 4b 全规模执行记录、偏差表、负面结果记录  |
| `analysis.md`           | 实际分析执行、与预注册计划的偏差、研究发现（F-ID）、假设验证结果、六层追溯矩阵 |
| `argument-blueprint.md` | 论点蓝图：电梯演讲、核心论点、内容点（CLAIM+EVIDENCE+INTERPRETATION）、叙事弧、已确认缺口 |
| `checklist.md`          | 审查清单（含按研究类型的专属条目）           |

关键设计：

- 执行分为两阶段：**4a 探索**（快速验证计划可行性）→ 用户决策点 → **4b 全规模**
- `analysis.md` 包含六层追溯矩阵（G → Q → H → M → A → F），这是 SDR 框架追踪的终点
- `argument-blueprint.md` 是从原始结果到论文叙事的桥梁

## 执行两阶段模型

```
Phase 4a（探索性）           用户决策点              Phase 4b（全规模）
─────────────────     ←──── MANDATORY STOP ────→  ─────────────────
最小验证                    1. 按计划推进              全数据集 × 全方法
1-2 基线                   2. 局部调整                全种子 × 全消融
错误分析                    3. 针对性重新设计           基线复现
计划可行性评估               4. 返回方法阶段            统计显著性检验
                           5. 返回问题阶段
```

4a 的目标是在全规模投入前发现计划问题，避免资源浪费。

## 四阶段能力划分

每个阶段下有若干能力，完成能力需要调用 Skill。

### 问题定义（problem）↔ `01-problem/`

| 能力 | 产出文件 | 说明 |
|------|---------|------|
| **意图理解** | `research-config.yaml` | 从用户描述识别研究目标、领域、类型（M/D/C/H） |
| **文献检索** | `literature-review.md` | 广度搜索、全文获取、深度阅读 |
| **空缺识别** | `gap-analysis.md` | 从文献中识别未解决问题，标注置信度（G-ID） |
| **问题凝练** | `research-question.md` | 将空缺转化为具体可研究的问题（Q-ID） |
| **假说形成** | `research-question.md` | 提出可证伪的研究假设（H-ID） |
| **约束与标准** | `research-config.yaml` | 定义范围、资源约束、成功标准 |

### 方法设计（method）↔ `02-method/`

| 能力 | 产出文件 | 说明 |
|------|---------|------|
| **方法论选型** | `method_design.md` | 选择适合的研究方法，论证选型理由 |
| **变量定义** | `method_design.md` | 明确自变量、因变量、控制变量 |
| **数据策略** | `experiment_design.md` | 确定数据来源、采集方法、样本量论证 |
| **分析计划** | `experiment_design.md` | 预注册：在看到数据前声明分析方法 |
| **基线/竞品选择** | `experiment_design.md` | 选择对比方法，确认来源和兼容性 |
| **消融设计** | `experiment_design.md` | 规划逐步移除/替换组件的实验（Type M/H） |
| **任务拆解** | `task.md` | 拆为可执行步骤（M-ID）+ 实验矩阵（R-ID） |
| **可复现性** | `experiment_design.md` | 环境锁定、种子固定、版本控制 |

### 实验分析（experiment）↔ `03-experiment/`

| 能力 | 产出文件 | 说明 |
|------|---------|------|
| **探索执行（4a）** | `execution.md` | 最小验证、错误分析、计划可行性检查 |
| **全规模执行（4b）** | `execution.md` | 全数据集 × 全方法 × 全种子 × 全消融 |
| **证据抽取** | `analysis.md` | 从原始结果提取结构化证据（A-ID） |
| **统计分析** | `analysis.md` | 按预注册计划执行检验，记录偏差 |
| **假设验证** | `analysis.md` | 将发现映射回假设，判定 Supported / Not Supported / Inconclusive |
| **负面结果记录** | `execution.md` | 记录失败实验，禁止静默丢弃 |
| **发现凝练** | `analysis.md` | 形成研究发现（F-ID），构建六层追溯矩阵 |
| **机制解释** | `analysis.md` | 解释为什么结果是这样，连接文献 |
| **论点蓝图** | `argument-blueprint.md` | 将散落发现组织为连贯论点：电梯演讲、内容点、叙事弧 |
| **结果验收** | `checklist.md` | 最终质量、追溯完整性、类型专属条目检查 |

### 报告撰写（report）↔ Skill 驱动

| 能力 | 说明 |
|------|------|
| **研究线索生成** | 从发现中提炼后续方向和未解决问题 |
| **假说更新** | 根据证据修正或生成新假说 |
| **报告/图表生成** | 生成论文、图表、报告片段 |

## 与 OpenSpec 的对比

| 维度   | OpenSpec                | SDR                           |
| ---- | ----------------------- | ----------------------------- |
| 目标   | AI 编码的确定性               | AI 科研的确定                      |
| 阶段   | proposal → spec → code  | problem → method → experiment |
| 验证   | 测试通过/失败                 | 过程合规 + 人工 checklist          |
| 追溯   | requirement → test case | G → Q → H → M → A → F（六层）     |
| 核心约束 | Spec 先于 Code            | 分析计划先于数据（预注册）                 |
| 类型分支 | 无                       | M / D / C / H（四类研究）          |
| 执行模型 | 单阶段                     | 4a 探索 + 4b 全规模（两阶段）         |

## 命令集（Slash Commands）

| 命令                    | 作用                              |
| --------------------- | ------------------------------- |
| `/sdr:init`           | 交互式初始化项目（生成 research-config.yaml + 目录结构） |
| `/sdr:start <stage>`  | 开始某阶段工作，AI 读取前序阶段产物作为上下文        |
| `/sdr:checkpoint`     | 对当前阶段执行 checklist 审查，未通过则阻止推进  |
| `/sdr:next`           | 通过 checklist 后推进到下一阶段          |
| `/sdr:trace <id>`     | 查询某 ID 的全链路追溯                   |
| `/sdr:revise <stage>` | 回退到某阶段修订（保留历史）                  |
