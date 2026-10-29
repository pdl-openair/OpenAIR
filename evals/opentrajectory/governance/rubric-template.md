# `<skill-id>` Rubric

## 1. 适用范围

| 字段 | 内容 |
| --- | --- |
| Skill ID | `<skill-id>` |
| Skill Version | `<skill-version>` |
| Rubric Version | `<rubric-version>` |
| 适用数据集 | `<datasets>` |

## 2. 评分等级

统一使用 `0–4` 分制：

| 分数 | 含义 |
| --- | --- |
| 4 | 完全满足，结果稳定、准确，可直接供下游使用 |
| 3 | 基本满足，存在轻微缺陷，但不影响主要用途 |
| 2 | 部分满足，缺陷已影响可用性或需要明显人工修正 |
| 1 | 明显不满足，结果不可靠或无法供下游使用 |
| 0 | 不可接受，或触发硬性失败条件 |

## 3. 评分维度

不同 Skill 类别可以复用本骨架，但必须单独定义评分维度、权重和硬性拒绝项。不得机械复制其他类别的 rubric。

| 维度 | 定义 | 权重 | 评分要点 | 常见扣分项 | 硬性拒绝项 |
| --- | --- | --- | --- | --- | --- |
| `<dimension>` | `<definition>` | `<weight>` | `<criteria>` | `<deductions>` | `<hard-reject>` |

## 4. 证据要求

每个分数必须附带：

- 支撑分数的输出片段或 artifact；
- 对应输入；
- 命中的扣分项；
- 是否触发硬性拒绝；
- 不确定性说明；
- 是否需要人工复核。

## 5. 边界样例

| 场景 | 预期判断 | 说明 |
| --- | --- | --- |
| `<boundary-case>` | `<expected-score-or-rule>` | `<note>` |

## 6. LLM Judge 输出要求

LLM Judge 必须输出结构化结果：

```json
{
  "rubric_version": "<version>",
  "dimension_scores": [
    {
      "dimension": "<dimension>",
      "score": 0,
      "evidence": ["<artifact-or-output-fragment>"],
      "deductions": ["<reason>"],
      "hard_reject": false,
      "confidence": 0.0
    }
  ],
  "hard_rejects": [],
  "overall_comment": "<comment>",
  "human_review_required": false
}
```

## 7. 人工复核说明

以下情况必须人工复核：

- LLM Judge 置信度不足；
- Judge 与 deterministic checks 冲突；
- 命中新失败模式；
- 分数较 baseline 明显变化；
- 高风险节点；
- `<skill-specific-trigger>`。
