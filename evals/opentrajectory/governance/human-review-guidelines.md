# OpenTrajectory 人工复核规范

## 1. 目的

人工复核不是补充说明，而是评估规则迭代的重要来源。

本规范只定义人工复核流程、抽样和裁决记录。不同 Skill 类别的强制复核要求，以 `skill-category-eval-guidelines.md` 为准。

人工复核需要回答：

1. 当前输出是否可用；
2. 当前 checklist、测试集、rubric、Judge 和权重是否合理。

## 2. 触发方式

人工复核分为两类：

| 类型 | 说明 |
| --- | --- |
| 类别要求 | 由 `skill-category-eval-guidelines.md` 定义 sampled 或 mandatory |
| 事件触发 | 出现新失败模式、回归、冲突或显著分数变化时触发 |

事件触发至少包括：

- 命中新的失败模式；
- 已修复 regression case 再次出现；
- LLM Judge 与 deterministic checks 冲突；
- LLM Judge 与领域判断明显冲突；
- 分数较 baseline 明显变化；
- 输入属于高价值、边界或异常场景；
- 评估规则本身被质疑。

## 3. 抽样复核

对于非强制全量复核节点，应在单 Skill Eval Spec 中明确：

| 项目 | 要求 |
| --- | --- |
| 抽样比例或数量 | `<ratio-or-count>` |
| 分层方式 | core / cross-domain / boundary / regression |
| 高风险样例 | 强制纳入 |
| 随机种子 | 需要记录 |
| Reviewer | 需要记录 |

新增 Eval Suite 初期，应提高人工抽样比例；规则稳定后再逐步降低。

## 4. 复核记录

每次人工复核至少记录：

| 字段 | 内容 |
| --- | --- |
| Run ID | `<run-id>` |
| Reviewer | `<reviewer>` |
| 样例范围 | `<scope>` |
| 结论 | `<approved/rejected/needs-revision>` |
| 主要问题 | `<issues>` |
| 与 Judge 的差异 | `<diff>` |
| 是否新增 regression case | `<yes/no>` |
| 是否调整 rubric | `<yes/no>` |
| 是否调整 dataset | `<yes/no>` |
| 是否调整权重 | `<yes/no>` |

## 5. 与 LLM Judge 冲突时的处理

发生冲突时，不应直接选择任一方作为最终答案。先判断问题属于：

```text
Skill 输出
schema
adapter
测试集
checklist
rubric
judge prompt
judge model
人工判断标准
```

处理顺序：

```text
定位冲突来源
→ 明确正确标准
→ 修正 rubric / dataset / judge 或 Skill
→ 固化 regression case（如适用）
→ 重新回归
```

## 6. 规则迭代

真实运行中发现稳定问题时：

```text
真实失败
→ 人工复核
→ 固化 regression case
→ 更新 checklist / dataset / rubric
→ 优化 Skill
→ 回归验证
```

不得仅修改 prompt 后重新运行，而不更新失败记录和评估规则。
