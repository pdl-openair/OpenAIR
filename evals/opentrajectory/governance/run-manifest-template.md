# Run Manifest: `<run-id>`

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Run ID | `<run-id>` |
| Timestamp | `<iso-8601>` |
| Skill ID | `<skill-id>` |
| Skill Version | `<skill-version>` |
| Eval Suite Version | `<eval-suite-version>` |
| Dataset ID | `<dataset-id>` |
| Dataset Version | `<dataset-version>` |
| Baseline Run ID | `<baseline-run-id-or-na>` |

## 2. 运行配置

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

## 3. 输入快照

| 项目 | 路径或引用 | Hash | 说明 |
| --- | --- | --- | --- |
| 输入 | `<path-or-ref>` | `<hash>` | `<note>` |

## 4. Captured Run

| 项目 | 路径或引用 | 说明 |
| --- | --- | --- |
| Trace | `<path-or-ref>` | `<note>` |
| Artifacts | `<path-or-ref>` | `<note>` |
| Raw Output | `<path-or-ref>` | `<note>` |
| Normalized Output | `<path-or-ref>` | `<note>` |
| Errors | `<path-or-ref-or-none>` | `<note>` |

## 5. 性能

| 指标 | 数值 | 阈值 | 结论 |
| --- | --- | --- | --- |
| 总耗时 | `<value>` | `<threshold>` | `<pass/fail>` |
| Token / Cost | `<value>` | `<threshold-or-na>` | `<pass/fail/na>` |

## 6. 检查结果

| 类型 | 路径或引用 | 结果摘要 |
| --- | --- | --- |
| Deterministic Checks | `<path>` | `<summary>` |
| Statistical Checks | `<path-or-na>` | `<summary>` |
| Rubric Result | `<path>` | `<summary>` |
| LLM Judge Result | `<path>` | `<summary>` |
| Human Review | `<path-or-na>` | `<summary>` |
| Real-Chain Acceptance | `<path-or-na>` | `<summary>` |

## 7. Score 与结论

| 项目 | 结果 |
| --- | --- |
| Hard Gate | `<pass/fail>` |
| Deterministic Score | `<score>` |
| Rubric Score | `<score>` |
| LLM Judge Score | `<score>` |
| Human Review | `<approved/rejected/na>` |
| Real-Chain Acceptance | `<pass/fail/na>` |
| Final Score | `<score>` |
| Final Result | `<pass/fail>` |

## 8. Baseline Diff

| 指标 | Baseline | Current | 差异 | 结论 |
| --- | --- | --- | --- | --- |
| `<metric>` | `<value>` | `<value>` | `<diff>` | `<acceptable/regression/improvement>` |

## 9. Regression Cases

| Case ID | 是否命中 | 状态 | 说明 |
| --- | --- | --- | --- |
| `<case-id>` | `<yes/no>` | `<open/fixed/verified/reopened>` | `<note>` |

## 10. 备注

`<notes>`
