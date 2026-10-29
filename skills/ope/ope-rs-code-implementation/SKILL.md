---
name: ope-rs-code-implementation
description: 当用户想要实现 OPE 核心算法、编写实验代码、或做单元测试时自动触发。常见场景：用户说"帮我实现这个 OPE 估计器"、"写 DR 代码"、"单元测试"。本 skill 完成核心算法实现、单元测试、可复现性配置，不负责批量实验执行与数据验证。
---

# 代码实现（L3 — OPE）

实现 OPE 核心算法代码，完成单元测试，保证可复现性。

## Method

### Step 1：环境准备
- 用 AskUserQuestion 确认项目根目录与环境管理器偏好（uv / conda / venv）
- 切换到项目根目录并激活虚拟环境
- 完成小特性后及时 git commit，遵循 commit 规范
- 只能在虚拟环境里安装包

### Step 2：理解任务需求
读取 `design/experiment_design.md`，提取与代码实现相关的部分：
- 要实现的估计器/模块（IPS/DR/WDR/FQE 等）
- 输入输出规范（轨迹 → 标量估计）
- 性能要求（数值稳定性、batch 处理能力）
- 依赖的其他模块（行为策略估计、奖励模型）

### Step 3：代码结构设计
- 确定文件位置（遵循项目现有结构）
- 设计类/函数接口
- 用 Glob/Grep 查找可复用代码（d3rlpy/ogle/coax 等已有库）

### Step 4：实现代码
按照设计实现：
- 神经网络架构（如 FQE 用 Q 网络）
- 训练循环（如 DM 的奖励拟合）
- 数据处理流程（轨迹切分、动作对齐）
- 错误处理（数值不稳定、log(0)、除零）

深度学习框架选择：
- 优先复用项目现有框架
- 常见：PyTorch / TensorFlow / JAX
- 不确定时用 AskUserQuestion 询问偏好

### Step 5：编写单元测试
- 单元测试：每个估计器/模块的输入输出正确性
- 集成测试：模块间接口正确性
- 边界条件：极端输入、空轨迹、单条样本

测试覆盖点示例：
```python
- test_ipe_output_shape()           # 输出形状正确（标量）
- test_ipe_value_finite()           # 估计值有限
- test_dr_equals_dm_plus_ips()      # DR = DM + IPS 关系
- test_data_loader_batch()          # batch 生成
- test_trajectory_sampler()         # 轨迹采样
```

测试覆盖率目标：≥ 80%，框架优先用 pytest。

### Step 6：编写真实执行脚本
- 调用实现代码，使用真实离线数据（**严禁用合成数据**冒充真实数据）
- 设定评估指标：误差、loss 值等
- 基于理论分析确定预期指标（如"MSE 小于 DR 基线的 80%"），执行目标**未经用户确认不得更改**
- 反思迭代，直至达成目标

### Step 7：验证可复现性
```python
import random, numpy as np, torch
random.seed(42); np.random.seed(42)
torch.manual_seed(42); torch.cuda.manual_seed_all(42)
```
- 依赖管理：requirements.txt 或 pyproject.toml，锁定版本（`package==1.2.3`）
- 配置管理：YAML/JSON 配置文件，版本化提交
- 可复现性清单：种子固定 / 依赖锁定 / git commit hash / 配置版本化 / 数据集版本 / 实验命令记录

### Step 8：验收检查
- 代码完整性：所有功能已实现、文件已创建、符合项目规范
- 测试通过：覆盖率 ≥ 80%、所有用例通过、边界条件覆盖
- 执行通过：达成执行目标
- 代码质量：符合规范、必要注释、无代码异味
- 可复现性：依赖记录、种子固定、配置文件、环境信息

任何一项失败：记录原因到实现报告，不标记任务完成。

## Input

- 实验设计方案（来自 `ope-rs-experiment-design` 的 `design/experiment_design.md`）
- 任务计划（来自 `ope-rs-task-planning` 的 `plan/task_plan.md`）
- 可选：用户已有的代码框架或实现思路

## Output

按 `schemas/ope/specs/implementation.md` 模板产出，使用 Write 工具写入 `execute/code/implementation.md`，结构：
- 实现概述（已实现模块表 + 代码结构）
- 核心算法实现（算法列表 + 关键代码片段）
- 测试报告（用例 + 覆盖率 + 已知问题）
- 可复现性配置（依赖 + 种子 + 复现命令）
- 下一步行动

## Constraints

- 禁止使用合成数据/模拟数据替代真实数据做测试
- 禁止操作数据或选择性报告结果
- 必须报告所有结果，包括不达标的
- 模块化、可测试、可复现三大底线
- 多轮讨论：用 AskUserQuestion 确认实现细节
- 结果落盘到 `execute/code/implementation.md`
