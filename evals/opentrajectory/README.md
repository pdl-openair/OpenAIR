# OpenTrajectory Evals

## 1. 目录用途

本目录管理 OpenTrajectory Skill 的真实效果验收、回归验证与版本比较。

```text
schema valid ≠ 输出可用
p0_validation.py passed ≠ 真实效果验收通过
单次 smoke passed ≠ 真实链路验收通过
```

统一治理规则与模板入口见 `governance/README.md`。

## 2. 当前状态

| 批次 | 状态 | 内容 |
| --- | --- | --- |
| 批次 0 | 已建立 | 通用治理模板与评分约定 |
| 批次 1 | Eval Suite 初版 `v0.2.1` | Phase1 / Phase2 / Phase3 结构解析与检索策略类 Skill；Phase3 LLM judge 资产 **deferred**（见 `phase3-retrieval-matrix-llm-judge-v0/README.md`） |

批次 1 已具备 Eval 文档、数据集、`harness/` 确定性 checks 与 dry-run runner；`runs/` 留档与 baselines 仍在演进，**尚未**证明全部 Skill 已通过真实效果验收。

## 3. 批次 1 导航

| 路径 | 用途 |
| --- | --- |
| `datasets/strategy-phase1-3/` | Phase1–3 共享科研问题与分阶段 Eval 标注 |
| `harness/strategy_phase1_3/` | Phase1–3 dry-run runner、deterministic checks、LLM judge 适配 |
| `harness/common/` | 数据集加载、run manifest、通用 checks |
| `harness/tests/` | harness 单元与回归测试 |
| `runs/strategy-phase1-3/` | 运行留档目录（本地生成，默认 gitignore） |
| `structured-requirement-generation/` | Phase1 Quality Contract、Eval Spec 与 Rubric |
| `constraint-interpretation-skill/` | Phase2 Quality Contract、Eval Spec 与 Rubric |
| `retrieval-matrix-generation/` | Phase3 Quality Contract、Eval Spec 与 Rubric |

批次 1 入口示例：

```bash
# 在仓库根目录
python evals/opentrajectory/harness/strategy_phase1_3/run_strategy_phase1_3.py --help
```
