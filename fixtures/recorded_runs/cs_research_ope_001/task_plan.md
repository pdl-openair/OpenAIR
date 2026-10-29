# 任务拆解与计划：面向 RL 训练过程的 OPE 方法选择实践指南

> 生成时间：2026-04-09（v2 更新于 2026-04-22）
> 研究主题：面向 RL 训练过程的 OPE 方法选择实践指南——基于 SB3 的系统性基准研究
> 基于实验设计：openair/ope-benchmark/design/experiment_design-v2.md（v2）
> 代码库路径：S:\research\OpenAIR\sb3_ope
> 版本说明：Phase 0-5 保持不变，Phase 6-7 已根据 v2 实验设计更新（环境 6→4、OPE 方法 5→7、新增 RQ1-RQ3 三维度实验）

---

## 1. 模块划分

| 模块 | 子模块 | 主要任务 | 难度 | 备注 |
|-----|-------|---------|------|------|
| M1: 项目脚手架 | pyproject.toml, 目录结构, CI | 初始化项目、配置依赖、建立目录结构 | 低 | 所有后续任务的基础 |
| M2: 数据层 | TrajectoryDataset, DataCollector | 轨迹数据容器、数据收集器、HDF5 序列化 | 中 | 供所有估计器使用 |
| M3: 工具层 | action_probability, continuous_is | SB3 策略概率获取、连续空间 IS 权重 | 高 | 关键技术难点 |
| M4: IS 估计器 | IS, WIS | 离散+连续空间的 IS/WIS 实现 | 中 | 最基础的 OPE 方法 |
| M5: DM 估计器 | DM, QFunction | Q 函数训练 + 直接方法估计 | 高 | 需要训练 Q 网络 |
| M6: DR 估计器 | DR, SN-DR | 结合 IS + DM 的双重稳健估计 | 中 | 依赖 M4 + M5 |
| M7: FQE 估计器 | FQE | 迭代拟合 Q^π | 高 | 最复杂的估计器 |
| M8: Callback | OPECallback | SB3 训练中实时 OPE 评估 | 中 | SB3 BaseCallback 集成 |
| M9: 评估框架 | metrics, evaluate_ope | MSE/Bias/Variance/RankCorrelation | 低 | 统计分析工具 |
| M10: 实验执行 | 数据收集, 主实验, 消融, 跨库对比 | 全部实验流程 | 中 | 依赖所有代码模块 |
| M11: 结果分析 | 可视化, 统计检验, 报告 | 图表制作和论文素材 | 低 | — |

---

## 2. 任务依赖关系

### 2.1 依赖图

```
M1(脚手架) ──→ M2(数据层) ──→ M4(IS估计器) ──→ M6(DR估计器)
     │              │                                ↑
     │              │           M5(DM估计器) ─────────┤
     │              │              ↑                  │
     │              │              │                  ↓
     │         M3(工具层) ────→ M7(FQE估计器)    M8(Callback)
     │              │                                │
     │              ↓                                ↓
     │         M9(评估框架) ←───────────────────────┘
     │              │
     ↓              ↓
M10(实验执行) ──→ M11(结果分析)
```

### 2.2 关键路径

```
M1 → M2 → M3 → M4 → M5 → M6 → M8 → M9 → M10 → M11
                  ↘ M7 ↗
```

**关键路径上的瓶颈**：
1. M3（工具层）：action probability 获取是所有估计器的前置，需先验证可行性
2. M5（DM 估计器）：Q 函数训练是 DR 和 FQE 的基础
3. M10（实验执行）：GPU 密集，受硬件限制

---

## 3. 子任务清单

### 3.1 Phase 0: 项目初始化（M1）

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T01 | 创建项目目录结构 | M1 | 无 | 0.5h | P0 | 目录结构与 8.3 节一致 |
| T02 | 编写 pyproject.toml（依赖、元数据） | M1 | T01 | 0.5h | P0 | `uv pip install -e .` 成功 |
| T03 | 创建所有 __init__.py 空文件 | M1 | T01 | 0.2h | P0 | 包可导入 |
| T04 | 配置 ruff + mypy | M1 | T01 | 0.5h | P1 | `ruff check` 和 `mypy` 通过 |
| T05 | 配置 pytest | M1 | T01 | 0.3h | P1 | `pytest` 可运行（空测试） |
| T06 | 验证 SB3 action probability 获取方式 | M1 | T02 | 2h | P0 | 脚本验证 DQN/PPO/SAC 均可获取 log_prob |
| T07 | 验证 Gymnasium MuJoCo 安装和运行 | M1 | T02 | 0.5h | P0 | HalfCheetah-v5 可正常 step |

### 3.2 Phase 1: 数据层 + 工具层（M2, M3）

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T08 | 实现 TrajectoryDataset 数据类 | M2 | T03 | 3h | P0 | 支持创建、属性访问、is_discrete 判断 |
| T09 | 实现 TrajectoryDataset.from_hdf5 / to_hdf5 | M2 | T08 | 2h | P0 | 序列化/反序列化往返正确 |
| T10 | 实现 TrajectoryDataset.subset | M2 | T08 | 0.5h | P1 | 子采样正确 |
| T11 | 实现 DataCollector | M2 | T06, T08 | 4h | P0 | CartPole 上收集 100 条轨迹，含 action_prob |
| T12 | 测试 DataCollector 在离散环境 | M2 | T11 | 1h | P0 | CartPole + DQN 收集通过 |
| T13 | 测试 DataCollector 在连续环境 | M2 | T11 | 1h | P0 | Pendulum + SAC 收集通过 |
| T14 | 实现 get_action_probability 工具函数 | M3 | T06 | 3h | P0 | DQN/PPO/SAC/TD3 四种策略均正确 |
| T15 | 实现 compute_is_weights 工具函数 | M3 | T14 | 2h | P0 | 离散空间 IS 权重计算正确 |
| T16 | 实现 ContinuousISWeightCalculator | M3 | T15 | 4h | P1 | 连续空间 KDE 权重计算通过 |
| T17 | 编写数据层+工具层单元测试 | M2/M3 | T08-T16 | 2h | P0 | 所有测试通过 |

### 3.3 Phase 2: IS/WIS 估计器（M4）

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T18 | 实现 BaseOPEEstimator 基类 | M4 | T03 | 1.5h | P0 | estimate() / estimate_std() / name 接口定义 |
| T19 | 实现 ImportanceSampling（离散） | M4 | T15, T18 | 3h | P0 | CartPole 上 IS 估计与手算一致 |
| T20 | 实现 WeightedImportanceSampling（离散） | M4 | T19 | 1.5h | P0 | WIS 方差低于 IS |
| T21 | 扩展 IS/WIS 支持连续动作空间 | M4 | T16, T19 | 3h | P1 | Pendulum 上 IS/WIS 运行无错 |
| T22 | IS/WIS 单元测试 + 精度验证 | M4 | T21 | 2h | P0 | 与 SCOPE-RL 结果交叉验证 |

### 3.4 Phase 3: DM + DR 估计器（M5, M6）

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T23 | 实现 QFunction 模型（PyTorch MLP） | M5 | T02 | 4h | P0 | fit() + predict() 在 CartPole 上收敛 |
| T24 | QFunction 离散动作空间支持 | M5 | T23 | 2h | P0 | Q(s,a) 对离散动作正确 |
| T25 | QFunction 连续动作空间支持 | M5 | T23 | 3h | P1 | Q(s,a) 对连续动作正确 |
| T26 | 实现 DirectMethod 估计器 | M5 | T24, T18 | 2h | P0 | DM 在 CartPole 上估计合理 |
| T27 | 实现 DoublyRobust 估计器 | M6 | T19, T26 | 3h | P0 | DR 估计值在 IS 和 DM 之间 |
| T28 | DM/DR 单元测试 | M5/M6 | T26, T27 | 2h | P0 | 与理论公式验证 |

### 3.5 Phase 4: FQE 估计器（M7）

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T29 | 实现 FittedQEvaluation 核心逻辑 | M7 | T23, T18 | 5h | P0 | 迭代拟合 Q^π 收敛 |
| T30 | FQE 离散动作空间适配 | M7 | T29 | 2h | P0 | CartPole 上 FQE 估计合理 |
| T31 | FQE 连续动作空间适配 | M7 | T29 | 3h | P1 | Pendulum 上 FQE 运行通过 |
| T32 | FQE 单元测试 + 与 d3rlpy 交叉验证 | M7 | T30 | 2h | P0 | FQE 结果与 d3rlpy 一致 |

### 3.6 Phase 5: Callback + 评估框架（M8, M9）

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T33 | 实现 OPECallback | M8 | T18, T08 | 4h | P1 | PPO learn() 中 callback 触发 OPE |
| T34 | OPECallback 集成测试 | M8 | T33 | 2h | P1 | 训练过程中 OPE 结果保存正确 |
| T35 | 实现 compute_metrics（MSE/Bias/Variance/RankCorrelation） | M9 | 无 | 2h | P0 | 指标计算与手算一致 |
| T36 | 实现 evaluate_ope 一键评估函数 | M9 | T35, T18 | 2h | P0 | 传入多个估计器返回汇总表 |
| T37 | 编写 examples/basic_discrete.py | M9 | T36 | 1h | P1 | 示例可直接运行 |
| T38 | 编写 examples/basic_continuous.py | M9 | T21, T36 | 1h | P1 | 示例可直接运行 |

### 3.7 Phase 6: 实验执行（M10）

> 基于 experiment_design-v2.md 的三阶段实验流程：策略训练+数据收集 → RQ1/RQ2/RQ3 OPE 评估
> 环境从 v1 的 6 个精简为 4 个，OPE 方法从 5 个扩展为 7 个，新增 checkpoint 训练进度维度

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T39 | 编写自动化 pipeline 脚本（训练→checkpoint→数据收集→OPE评估） | M10 | T11, T22, T28, T32 | 4h | P0 | 脚本可一键启动全部流程；支持断点续跑 |
| T40 | 运行策略训练+Checkpoint 保存（4环境×~1.5算法×5种子=30个run） | M10 | T39 | 6h GPU | P0 | 300 个 checkpoint（30×10），每个 checkpoint 保存为独立模型文件 |
| T41 | 运行离线数据收集（每个 checkpoint 收集 10K 轨迹，5 个子采样规模） | M10 | T40 | 8h GPU | P0 | 1,500 个离线数据集（300×5规模），HDF5 格式；V_true 复用 10K 均值无需单独采集 |
| T42 | 运行 RQ1 实验：目标策略训练进度 × OPE 方法 | M10 | T41, T22, T28, T32 | 6h GPU | P0 | 2,100 次 OPE 评估完成；results/rq1/*.csv 生成 |
| T43 | 运行 RQ2 实验：行为策略时间窗口 × OPE 方法 | M10 | T41, T22, T28, T32 | 5h GPU | P0 | 1,680 次 OPE 评估完成；results/rq2/*.csv 生成；含 KL 散度计算 |
| T44 | 运行 RQ3 实验：数据量 × OPE 方法 | M10 | T41, T22, T28, T32 | 4h GPU | P0 | 1,050 次 OPE 评估完成；results/rq3/*.csv 生成 |
| T45 | 计算 KL 散度（行为策略与目标策略之间） | M10 | T41 | 2h | P0 | 每个策略对的 KL 散度矩阵；用于 RQ2 距离度量 |
| T46 | 数据质量验证 + OPE 结果完整性检查 | M10 | T42, T43, T44 | 2h | P0 | 无异常值，数据格式正确，OPE 评估总数 ~4,830 无遗漏 |

### 3.8 Phase 7: 结果分析（M11）

> 基于 experiment_design-v2.md 的 RQ1-RQ3 实验设计，对应假说 H1'-H4'

| 任务ID | 任务描述 | 所属模块 | 依赖任务 | 估计工时 | 优先级 | 验收标准 |
|-------|---------|---------|---------|---------|-------|---------|
| T47 | RQ1 统计分析：双因素重复测量 ANOVA（训练进度 × OPE 方法交互效应）+ Friedman/Nemenyi 后检验 + 变点检测 | M11 | T42 | 3h | P0 | 训练进度与 OPE 方法的交互效应结论；最优方法切换点识别 |
| T48 | RQ2 统计分析：Spearman 相关检验（策略距离 vs MSE）+ 误差容忍度分析（ε∈{5%,10%,20%}） | M11 | T43, T45 | 3h | P0 | 策略距离与 MSE 相关性结论；最大可接受策略距离阈值 |
| T49 | RQ3 统计分析：MSE vs 数据量幂律拟合 + 数据充分性阈值 | M11 | T44 | 2h | P0 | 各方法幂律参数 a,b；最小充分数据量 |
| T50 | 联合分析：(策略距离, 数据量) 平面精度等高线图 + 方法排序稳定性 | M11 | T47, T48, T49 | 2h | P1 | 二维精度等高线图生成 |
| T51 | 制作 RQ1 可视化：折线图（x=训练进度,y=MSE）+ 热力图（方法×训练进度） | M11 | T47 | 3h | P0 | §5 图表：7条方法折线图 + MSE 热力图 |
| T52 | 制作 RQ2+RQ3 可视化：策略距离折线图 + 数据量 log-log 折线图 + 等高线图 | M11 | T48, T49, T50 | 3h | P0 | §6-7 图表：距离-MSE折线图 + 阈值热力图 + 等高线图 |
| T53 | 制作 Rank Correlation 基准热力图（方法×环境）+ 实践决策树/流程图 | M11 | T42, T47, T48, T49 | 3h | P0 | §4 基准验证热力图 + §8 实践指南决策树 |
| T54 | 撰写实验结果报告（含 H1'-H4' 假说验证结论） | M11 | T47-T53 | 3h | P0 | 完整报告文档，含所有假说验证结论 |

---

## 4. 里程碑计划

| 里程碑 | 目标日期 | 交付物 | 对应任务 |
|-------|---------|-------|---------|
| M1: 项目就绪 + 技术验证 | 第 1 周末 | 可安装的包 + SB3 接口验证报告 | T01-T07 |
| M2: 数据层 + 工具层完成 | 第 2 周中 | TrajectoryDataset + DataCollector + action_prob 工具 | T08-T17 |
| M3: IS/WIS/PDIS 估计器完成 | 第 2 周末 | 离散+连续 IS/WIS/PDIS 可运行 + 单元测试 | T18-T22 |
| M4: DM + DR/SN-DR 估计器完成 | 第 3 周中 | DM/DR/SN-DR 可运行 + 与理论验证 | T23-T28 |
| M5: FQE + Callback 完成 | 第 4 周末 | 全部 7 种估计器 + OPECallback 可用 | T29-T38 |
| M6: 策略训练+数据收集完成 | 第 5 周中 | 300 checkpoint + 1,500 离线数据集（4 环境 × 5 种子） | T39-T41 |
| M7: RQ1/RQ2/RQ3 实验完成 | 第 6 周末 | ~4,830 次 OPE 评估结果 + KL 散度矩阵 | T42-T46 |
| M8: 结果分析+实践指南完成 | 第 7 周中 | H1'-H4' 假说验证结论 + 可视化图表 + 决策树 | T47-T54 |

---

## 5. 资源计划

### 5.1 GPU 时间分配

| 阶段 | 任务 | GPU 需求 | 估计时长 |
|-----|------|-------|---------|
| 策略训练 + Checkpoint 保存 | T40 | RTX 3080 x 1 | ~6h |
| 离线数据收集（10K 轨迹 × 300 checkpoint） | T41 | RTX 3080 x 1 | ~8h |
| RQ1 实验（2,100 次 OPE 评估） | T42 | RTX 3080 x 1 | ~6h |
| RQ2 实验（1,680 次 OPE 评估） | T43 | RTX 3080 x 1 | ~5h |
| RQ3 实验（1,050 次 OPE 评估） | T44 | RTX 3080 x 1 | ~4h |
| KL 散度计算 | T45 | CPU 即可 | ~2h |
| **总计** | | | **~31h GPU** |

### 5.2 周计划

| 周次 | 主要任务 | 目标完成 | 关键产出 |
|-----|---------|---------|---------|
| Week 1 | 项目初始化 + SB3 接口验证 | T01-T07 | 可安装包 + 接口验证脚本 |
| Week 2 | 数据层 + IS/WIS/PDIS 实现 | T08-T22 | DataCollector + IS/WIS/PDIS 估计器 |
| Week 3 | DM + DR/SN-DR 实现 | T23-T28 | DM/DR/SN-DR 估计器 + Q 函数 |
| Week 4 | FQE + Callback + 评估框架 | T29-T38 | 全部 7 种估计器 + OPECallback |
| Week 5 | 策略训练 + 数据收集（GPU 密集） | T39-T41 | 300 checkpoint + 1,500 离线数据集 |
| Week 6 | RQ1/RQ2/RQ3 OPE 评估 | T42-T46 | ~4,830 次 OPE 评估结果 |
| Week 7 | 结果分析 + 可视化 + 实践指南 | T47-T54 | 假说验证 + 图表 + 决策树 |

### 5.3 并行策略

```
Week 2 可并行：
  ├── 数据层（T08-T13）—— 可与工具层并行
  └── 工具层（T14-T17）—— 依赖 T06

Week 4 可并行：
  ├── FQE 实现（T29-T32）
  ├── Callback 实现（T33-T34）—— 依赖 T18
  └── 评估框架（T35-T38）—— 无强依赖

Week 5 可并行（GPU 密集）：
  ├── CartPole + LunarLander 训练+数据收集（离散环境）
  └── Pendulum + HalfCheetah 训练+数据收集（连续环境）

Week 6 可并行：
  ├── RQ1 实验（T42）—— 变目标策略训练进度
  ├── RQ2 实验（T43）—— 变行为策略时间窗口
  └── RQ3 实验（T44）—— 变数据量
  注：RQ1/RQ2/RQ3 共享数据集（T41），无依赖关系，可并行执行
```

---

## 6. 风险与应对

| 风险 | 可能性 | 影响 | 应对方案 |
|-----|-------|------|---------|
| SB3 策略不暴露 action probability | 中 | 高（阻塞所有 IS 方法） | T06 优先验证；备选：monkey-patch 或 wrapper |
| 连续空间 IS 权重数值不稳定 | 高 | 中（IS/WIS 在连续空间不可用） | 优先保证 DM/DR/FQE；IS 连续空间标注为实验性 |
| Q 函数训练不收敛 | 中 | 高（阻塞 DM/DR/FQE） | 参考 d3rlpy 的超参数；增加训练 epoch |
| GPU 资源不足 | 低 | 中 | 简单环境 CPU 训练；复杂环境优先 |
| MuJoCo 安装问题 | 低 | 低 | 提前验证（T07）；备选 PyBullet |
| 300 checkpoint 存储空间不足 | 低 | 中 | 预估 ~16GB HDF5；支持增量写入；可按环境分批 |
| RQ2 KL 散度计算数值不稳定 | 中 | 中（连续空间 KL 计算困难） | 使用近似 KL；备选时间距离作为距离度量 |
| PDIS/SN-DR 在长轨迹上方差仍较大 | 中 | 低 | 可截断重要性权重（cap at threshold） |

---

## 7. 下一步行动

### 7.1 立即可执行

- [ ] T01: 创建项目目录结构（`S:\research\OpenAIR\sb3_ope`）
- [ ] T02: 编写 pyproject.toml
- [ ] T06: 验证 SB3 action probability 获取方式（关键前置！）
- [ ] T07: 验证 MuJoCo 环境

### 7.2 v2 新增待决策事项

- [ ] Checkpoint 粒度：10% 是否足够？是否需要在关键区域（如切换点附近）加密？
- [ ] 行为策略选择：RQ1 中行为策略固定为 ckpt_50% 是否合理？是否需要消融？
- [ ] 距离度量：时间距离 vs KL 散度 vs 两者兼用？（影响 T45 实现方式）
- [ ] 确认 SB3 callback 的具体触发时机（T33 前决策）
- [ ] 确认连续空间 IS 的具体实现方案（T16 前决策）
- [x] FQE Q 函数网络架构选择 → 详见 experiment_design-v2.md §2.1.1

---

*本报告为 `research-task-planning` skill 的产出物（v2 更新版）。*
*Phase 0-5 保持不变，Phase 6-7 已根据 experiment_design-v2.md 更新。*
*建议后续运行 `/openair-ope-execute` 开始实验执行。*
