# OpenTrajectory Eval Suite 规范

## 1. 目的

本规范用于统一 OpenTrajectory Skill 的 Eval Suite 目录、版本记录、运行留档与回归门禁。

Eval Suite 不只保存一次 `pass / fail`，还应支持：

- 固定标准验收；
- 跨版本比较；
- 失败定位；
- 历史回归；
- 人工复核与规则迭代；
- 按 Skill 类别执行适配的真实链路验收。

## 2. 适用范围

适用于 OpenTrajectory 中所有需要真实效果验收的 Skill。

不同 Skill 类型使用统一留档结构，但不得机械共用同一套评分规则。各类别的测试材料、人工复核要求与真实链路验收强度，以 `skill-category-eval-guidelines.md` 为准。

## 3. 单 Skill 目录结构

```text
evals/opentrajectory/<skill-name>/
├── quality-contract.md
├── eval-spec.md
├── datasets/
│   ├── core.jsonl
│   ├── cross-domain.jsonl
│   ├── boundary.jsonl
│   └── regression.jsonl
├── manifests/
│   ├── datasets/
│   └── runs/
├── rubrics/
├── checks/
├── baselines/
├── runs/
└── reports/
```

| 目录或文件 | 作用 |
| --- | --- |
| `quality-contract.md` | 定义职责、边界、成功标准和禁止行为 |
| `eval-spec.md` | 定义测试范围、checks、rubric、score 与 pass gate |
| `datasets/` | 保存固定测试集 |
| `manifests/datasets/` | 保存测试集输入快照和数据版本元数据 |
| `manifests/runs/` | 保存单次运行清单 |
| `rubrics/` | 保存分类适配后的语义评分规则 |
| `checks/` | 保存可重复执行的确定性检查 |
| `baselines/` | 保存已确认基线引用 |
| `runs/` | 保存 captured run 的 trace 与 artifacts |
| `reports/` | 保存版本比较、回归和人工复核结论 |

### 3.1 共享数据集例外

相邻 Skill 可共用：

```text
evals/opentrajectory/datasets/<dataset-id>/
```

只共享输入与 manifest，不共享成功标准、评分口径或职责边界。各 Skill 仍须按自身 Quality Contract、Eval Spec 和 Rubric 独立验收。

## 4. 版本规则

正式 Eval Run 必须能够追溯以下信息：

| 对象 | 最少记录 |
| --- | --- |
| Skill | `skill_id`、`skill_version` |
| Eval Suite | `eval_suite_version` |
| Dataset | `dataset_id`、`dataset_version`、hash |
| Prompt | `prompt_version` 或 prompt hash，如适用 |
| 模型 | provider、model、可追溯版本标识，如适用 |
| Adapter | adapter version 或 commit，如适用 |
| Parser | parser 名称与版本，如适用 |
| Runtime | 与结果相关的关键参数和可追溯 commit，如适用 |
| Scoring | scoring rules version、rubric version |

只记录与当前 Skill 有关的字段；不适用项写 `N/A`，不得为了形式完整伪造版本信息。

无法追溯关键变化来源的运行，不得作为正式 baseline。

## 5. Captured Run 规则

每次正式运行至少保存：

```text
输入快照
→ prompt / 模型 / 关键参数（如适用）
→ trace + artifacts + 输出
→ 错误与耗时
→ deterministic / statistical checks（按需）
→ rubric-based grading + LLM Judge（按需）
→ 人工复核（按类别要求或触发条件）
→ 可比较 score + pass / fail
→ baseline diff + regression 命中情况
```

详细字段使用 `run-manifest-template.md`。

## 6. Checks 分类

| 类型 | 作用 | 示例 |
| --- | --- | --- |
| Schema / contract checks | 验证结构合法、必填字段和主键完整 | schema valid、ID 完整、输出文件存在 |
| Deterministic quality checks | 验证可稳定判断的质量要求 | 禁止 fallback、SourceAnchor 绑定、占位词、pairwise variants |
| Statistical checks | 验证数量、比例、覆盖、稳定性和性能 | recall、重复率、池分布、误排除率、耗时 |
| Rubric-based grading | 验证无法仅靠规则判断的语义质量 | query 价值、Evidence 准确性、链路解释力 |
| Human review | 处理高风险或规则不确定情况 | 人工抽样、强制复核、裁决记录 |
| Real-chain acceptance | 验证上下游真实传递与产品可用性 | 冻结链路 replay、真实 PDF、真实候选池 |

## 7. 验收层级

```text
schema check
≠ p0_validation
≠ smoke
≠ real-chain acceptance
≠ 真实效果验收
```

| 层级 | 目的 | 可否替代下一层 |
| --- | --- | --- |
| Schema Check | 验证结构合法 | 否 |
| `p0_validation.py` | 快速工程门禁 | 否 |
| Smoke | 验证最小链路可达 | 否 |
| Real-Chain Acceptance | 验证真实上下游传递 | 否 |
| 真实效果验收 | 验证真实场景质量、泛化与可用性 | — |

真实链路验收不是所有 Skill 的统一强制门槛。各类别适用强度以 `skill-category-eval-guidelines.md` 为准，并在具体 `eval-spec.md` 中明确。

## 8. Baseline 规则

正式 baseline 必须满足：

- 输入、关键运行配置和数据集可追溯；
- captured run 完整；
- 适用的 checks 与 rubric 已执行；
- 无未解释的硬门禁失败；
- 已按 Skill 类别要求完成人工复核与真实链路验收；
- baseline 结论经过明确确认。

历史 execution outputs、临时文件和单次 smoke 不自动成为 baseline。

## 9. 回归门禁

每次 Skill 调整后至少验证：

```text
core
cross-domain
boundary
regression
```

历史 regression case 不得静默失效。新增稳定失败模式时，应先固化 case，再进入 Skill 优化。
