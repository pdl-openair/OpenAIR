# Migration Notes: constraint-interpretation-skill

## 代码来源

| 文件 | 路径 |
|------|------|
| 适配器实现 | `04_backend/app/skills/constraint_interpretation_adapter.py` |
| Pydantic schema | `04_backend/app/schemas/constraint_interpretation.py` |

## Schema 映射

### Input（来自 `ConstraintInterpretationInput`）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_query` | string | 是 | 原始用户问题，默认 `""` |
| `research_domain` | string\|null | 否 | 研究领域 |
| `material_system` | string\|null | 否 | 材料体系 |
| `research_context` | object\|null | 否 | 上游上下文（StructuredRequirement 等） |
| `task_schema` | object\|null | 否 | 任务 schema 定义 |
| `raw_constraints` | array | 否 | 原始约束列表，默认 `[]` |
| `available_documents_summary` | string\|null | 否 | 可用文档摘要 |

### Output（来自 `ConstraintInterpretationOutput` + `InterpretedConstraint`）

`interpreted_constraints` 每项字段来自 `InterpretedConstraint`：

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `constraint_name` | string | 必填 |
| `original_phrase` | string | 必填 |
| `constraint_type` | string | 必填 |
| `interpreted_value` | string\|null | null |
| `candidate_values` | array | [] |
| `domain_rationale` | string | "" |
| `confidence` | number [0,1] | 0.5 |
| `needs_user_confirmation` | boolean | true |
| `downstream_search_terms` | array | [] |
| `evidence_basis` | string\|null | null |
| `risk_flags` | array | [] |

## 迁移边界

**包含：**
- 约束识别与解读逻辑（LLM 路径 + 确定性降级）
- `downstream_search_terms` 生成
- 正式约束语义保真传递；检索强度和排除策略由 `retrieval-matrix-generation` 判断
- `quality_report` 生成

**不包含（由下游 skill 负责）：**
- 文献检索执行
- 检索矩阵生成（→ `retrieval-matrix-generation`）
- 候选池过滤执行

## 降级策略

原始实现曾使用确定性 regex/关键词降级来重新解释 `user_query`、`research_context` 或 `task_schema`。该语义降级不属于当前生产合同：模型不可用时由 durable runtime 报告并恢复，确定性代码不得补造科研约束或检索政策。

## 已废弃字段

旧版本曾输出 `downstream_filter_rules`。当前生产合同不再生成或传播该字段；兼容边界若收到旧字段，只能整体忽略，不能读取其内容或将其恢复为检索 Authority。
