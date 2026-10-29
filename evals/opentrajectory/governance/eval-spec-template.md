# `<skill-id>` Eval Spec

## 1. Eval 目标

验证 `<skill-id>` 是否满足 Quality Contract，并能够在核心场景、跨领域场景、边界场景和历史回归样例中稳定输出可供下游使用的结果。

## 2. 适用版本

| 字段 | 内容 |
| --- | --- |
| Skill ID | `<skill-id>` |
| Skill Version | `<skill-version>` |
| Eval Suite Version | `<eval-suite-version>` |
| Quality Contract Version | `<quality-contract-version>` |

## 3. 测试范围

| 数据集 | 目的 | 是否必跑 | 说明 |
| --- | --- | --- | --- |
| `core` | 验证主要业务能力 | 是 | `<note>` |
| `cross-domain` | 防止单一领域过拟合 | 是 | `<note>` |
| `boundary` | 验证信息不足、冲突、噪声和降级 | 是 | `<note>` |
| `regression` | 防止历史缺陷重新出现 | 是 | `<note>` |

## 4. 数据集引用

| Dataset ID | Version | Manifest | Hash | 说明 |
| --- | --- | --- | --- | --- |
| `<dataset-id>` | `<version>` | `<path>` | `<hash>` | `<note>` |

## 5. 运行配置

| 字段 | 内容 |
| --- | --- |
| Model Provider | `<provider>` |
| Model | `<model>` |
| Model Version | `<model-version>` |
| Prompt Version / Hash | `<prompt-version-or-hash>` |
| Adapter Version / Commit | `<adapter-version-or-commit>` |
| Parser Version | `<parser-version-or-na>` |
| Runtime Commit | `<commit>` |
| 关键参数 | `<params>` |

## 6. Captured Run 要求

每次正式运行必须保存：

- 输入快照；
- prompt 或 prompt version；
- trace；
- artifacts；
- 输出；
- 耗时；
- 错误；
- checks；
- rubric；
- LLM Judge；
- 人工复核；
- score；
- baseline diff；
- regression 命中情况。

## 7. 硬门禁

| Gate ID | 检查项 | 失败条件 | 检查方式 |
| --- | --- | --- | --- |
| `<gate-id>` | `<check>` | `<failure-condition>` | `<deterministic/manual>` |

说明：硬门禁失败时，总分不得覆盖失败结论。

## 8. Deterministic Checks

| Check ID | 检查项 | 适用数据集 | 指标或规则 | 阈值 | 输出字段 |
| --- | --- | --- | --- | --- | --- |
| `<check-id>` | `<check>` | `<dataset>` | `<rule>` | `<threshold>` | `<result-field>` |

## 9. Rubric-Based Grading

Rubric 文件：`<rubric-path>`

| 维度 | 权重 | 评分方式 | 人工复核触发条件 |
| --- | --- | --- | --- |
| `<dimension>` | `<weight>` | `0–4` | `<trigger>` |

## 10. LLM Judge

| 字段 | 要求 |
| --- | --- |
| Judge Model | `<judge-model>` |
| Judge Prompt Version | `<judge-prompt-version>` |
| 输出格式 | 分维度评分、扣分理由、证据、命中失败模式、置信度 |
| 限制 | 不得覆盖确定性失败；不得作为唯一通过依据 |

## 11. 人工复核

人工复核强度按 Skill 类别确定，并可由具体失败模式触发。类别要求见 `skill-category-eval-guidelines.md`。

| 项目 | 内容 |
| --- | --- |
| 适用强度 | `<not-required/sampled/mandatory>` |
| 触发条件 | `<category-requirement-or-trigger>` |
| 复核方式 | `<review-mode>` |
| 抽样比例或数量 | `<ratio-or-count>` |
| 记录位置 | `<path>` |

## 12. 真实链路验收

真实链路验收不是所有 Skill 的统一强制门槛。应根据 Skill 类别和当前 Eval 目标明确为 `not-required`、`replay-required` 或 `mandatory-real-chain`。类别要求见 `skill-category-eval-guidelines.md`。

| 项目 | 内容 |
| --- | --- |
| 适用强度 | `<not-required/replay-required/mandatory-real-chain>` |
| 上游输入 | `<upstream-snapshot-or-live-input>` |
| 下游验证 | `<downstream-consumer>` |
| 必须保留的 trace | `<trace>` |
| 通过标准 | `<criteria>` |

## 13. Score 计算

```text
最终结论 = 硬门禁结果 + 加权评分 + 人工裁决（如适用）
```

| 项目 | 权重或规则 |
| --- | --- |
| Deterministic Checks | `<rule>` |
| Rubric Score | `<weight>` |
| LLM Judge | `<weight-or-reference-only>` |
| Human Review | `<category-rule-or-triggered-review>` |
| Real-Chain Acceptance | `<not-required/replay-required/mandatory-real-chain>` |

## 14. Pass Gate

| Gate | 通过条件 |
| --- | --- |
| 硬门禁 | 全部通过 |
| 总分 | `>= <threshold>` |
| 单维度下限 | `<dimension-threshold>` |
| Regression | 不得出现已修复 case 回归 |
| Human Review | `<not-required/sampled/mandatory + approval-rule>` |
| Real Chain | `<not-required/replay-required/mandatory-real-chain>` |

## 15. Baseline 比较

| 对比项 | Baseline | Current | 差异 | 是否可接受 |
| --- | --- | --- | --- | --- |
| `<metric>` | `<value>` | `<value>` | `<diff>` | `<yes/no>` |

## 16. 报告输出

每轮正式运行至少输出：

```text
run manifest
checks result
rubric result
LLM Judge result
human review record（如适用）
baseline diff
regression result
final report
```
