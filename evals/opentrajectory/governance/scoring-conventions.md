# OpenTrajectory Eval 评分规范

## 1. 目的

本规范用于统一不同 Skill Eval Suite 的评分优先级、硬门禁和分数可追溯规则。

统一的是评分框架，不是每类 Skill 的具体 rubric，也不是人工复核流程。类别差异见 `skill-category-eval-guidelines.md`，人工复核流程见 `human-review-guidelines.md`。

## 2. 评分组成

```text
最终结论
= 硬门禁
+ deterministic checks
+ statistical checks（如适用）
+ rubric-based grading（如适用）
+ LLM Judge（如适用）
+ 人工复核结论（按类别要求或触发条件）
+ 真实链路验收结论（按类别要求）
```

## 3. 优先级

### 3.1 硬门禁优先

出现硬门禁失败时：

- 总分不得覆盖失败；
- LLM Judge 不得覆盖失败；
- 人工复核可以解释原因，但不得静默放行；
- 如需例外，必须显式记录裁决、原因和适用边界。

### 3.2 Schema 只属于基础门禁

```text
schema valid
≠ 语义准确
≠ 跨场景泛化
≠ 下游可用
```

### 3.3 LLM Judge 不是唯一依据

LLM Judge 适用于规模化语义初评，但必须：

- 使用明确 rubric；
- 输出分维度得分；
- 提供扣分依据；
- 标记置信度；
- 不得覆盖确定性失败；
- 不得作为唯一通过依据。

## 4. 通用评分结构

单 Skill 可根据类别调整权重，但必须明确以下组成是否适用：

| 组成 | 作用 | 是否可覆盖硬门禁 |
| --- | --- | --- |
| Hard Gate | 阻断不可接受输出 | 否 |
| Deterministic Score | 自动判断稳定规则 | 否 |
| Statistical Score | 衡量覆盖、比例、稳定性和性能 | 否 |
| Rubric Score | 判断语义质量 | 否 |
| LLM Judge | 执行规模化 rubric 初评 | 否 |
| Human Review | 提供人工裁决记录 | 不静默覆盖 |
| Real-Chain Acceptance | 验证上下游真实可用性 | 否 |

具体 Skill 的人工复核和真实链路验收强度，由 `skill-category-eval-guidelines.md` 和该 Skill 的 `eval-spec.md` 确定。

## 5. Score 可比较性

正式 score 必须能追溯到与当前 Skill 相关的：

- Skill version；
- prompt version，如适用；
- 模型与版本，如适用；
- dataset version；
- adapter version，如适用；
- parser version，如适用；
- runtime commit，如适用；
- scoring rules version；
- rubric version。

无法追溯关键变化来源的分数，不得作为版本改进证据。

## 6. Regression Gate

每次正式回归必须检查：

- 已修复 regression case 是否重新出现；
- 新版本是否牺牲 core 或 cross-domain 场景；
- 性能是否显著恶化；
- 规则变化是否导致评分口径漂移。

## 7. 人工复核引用

人工复核触发条件、抽样方式、冲突处理和裁决记录，以 `human-review-guidelines.md` 为准。
