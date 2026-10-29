---
name: ope-rs-experiment-execution
description: 当用户想要运行 OPE 实验、批量评估估计器、或管理实验任务时自动触发。常见场景：用户说"跑 OPE 实验"、"执行批量评估"、"批量运行 FQE"。本 skill 完成批量执行、日志记录、失败重试，不负责代码实现与数据清洗。
---

# 实验执行（L3 — OPE）

按照实验设计方案批量执行 OPE 实验，强制记录实验日志。

## Method

### Step 1：理解实验需求
读取 `design/experiment_design.md`，提取：
- 要运行的算法 × 环境 × 种子组合
- 评估指标（MSE/Bias/Variance）
- 样本量要求

### Step 2：生成实验配置
- 算法 × 环境 × 种子的所有组合
- 生成 YAML/JSON 配置文件，保存到 `execute/experiments/configs/`
- 默认种子：[42, 123, 456, 789, 1000]，或从 `experiment_design.md` 读取

### Step 3：小样本验证
- 选 1-2 个配置运行少量 episodes
- 验证代码无错误（数值稳定、形状正确）
- 验证 OPE 估计值在合理量级（不爆 NaN/Inf）

### Step 4：批量执行
根据可用并行工具：

```bash
# 串行
for config in configs/*.yaml; do
    python run_ope.py --config $config
done

# GNU parallel
ls configs/*.yaml | parallel python run_ope.py --config {}

# Python multiprocessing
python run_ope_parallel.py --configs configs/*.yaml

# Slurm
sbatch run_ope_array.sh
```

目录结构：
```
execute/experiments/
├── configs/          # 实验配置文件
├── results/          # 实验结果（按 exp_001/ 子目录）
├── logs/             # 实验日志
└── execution_log.md  # 总日志
```

### Step 5：监控进度
- 实时记录已完成/进行中/失败实验
- 失败实验重试最多 3 次
- 记录资源使用（GPU、内存、磁盘）

### Step 6：记录实验日志
**必须**生成 `execute/experiments/execution_log.md`，包含六项内容：
1. 实验配置文件路径（每个实验）
2. 实验开始和结束时间
3. 使用的随机种子
4. 实验状态（成功/失败/中断）
5. 失败实验的错误信息（完整堆栈）
6. 结果文件路径和大小

实验清单表：
```markdown
| 实验ID | 配置文件 | 算法 | 环境 | 种子 | 状态 | 结果文件 | 备注 |
|--------|---------|------|------|------|------|---------|------|
| exp_001 | configs/exp_001.yaml | DR | Hopper | 42 | ✅ 成功 | results/exp_001/ | - |
| exp_002 | configs/exp_002.yaml | IPS | Hopper | 123 | ❌ 失败 | - | OOM |
```

失败实验必须在"问题记录"章节详细说明（错误堆栈、可能原因、是否已重试）。

## Input

- 实验设计方案（`design/experiment_design.md`）
- 已实现的实验代码（来自 `ope-rs-code-implementation`）
- 可选：实验配置覆盖、并行工具偏好

## Output

按 `schemas/ope/specs/execution_log.md` 模板产出，使用 Write 工具写入 `execute/experiments/execution_log.md`，结构：
- 实验配置汇总（总数、算法数、环境数、种子数）
- 执行进度（已完成/进行中/失败）
- 实验清单表
- 资源使用统计
- 已知问题与处理
- 下一步行动

## Constraints

- 完成率 ≥ 80%，否则标记失败
- 失败实验必须重试（最多 3 次）并记录
- 日志六项内容缺一不可
- 可复现：种子/配置/环境版本/实验命令全部记录
- 可追踪：实验状态全程可查
- 可中断：支持断点续跑
- 结果落盘到 `execute/experiments/execution_log.md`
