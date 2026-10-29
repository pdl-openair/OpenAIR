# Spec — Literature Review（文献调研与研究目标）

<!-- Substage: literature_review
     Stage: problem_modeling
     L2 skill: materials-literature-review
     L3 skills: research-lit, mp-search, pymatgen -->

## 目标

把用户关于材料的自然语言需求转换为可以检索、生成、筛选和验证的具体目标，并补充材料背景和文献依据，为目标阈值和后续方案提供依据。

## Input

- 用户自然语言需求原文
- （可选）用户提供的已有结构或化学式

## 产出物（output_file: `literature_review.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| research_goal | ✅ | 明确的性质目标和限制条件（目标化学式或元素体系、要优化/接近的性质及阈值方向、元素黑名单、原子数/空间群限制、候选与 DFT 预算、是否需要制备建议和湿实验） |
| literature_summary | ✅ | 目标材料所属结构类型、材料家族关键词、已报道性质、已有制备路线的文献摘要（标注来源） |
| property_threshold_basis | ✅ | 性质目标阈值的依据（文献实测值或明确的用户决策，不混写模型估计） |
| mp_search_keywords | ⭑ | 后续 MP 检索的关键词建议 |
| goal_file | ⭑ | 落盘的 goal.yaml 或同等文件路径 |

## 验收标准

- [ ] research_goal 中每个性质目标都有明确的方向（最大化/最小化/接近目标值）和可判断的指标
- [ ] 文献摘要与模型预测值明确区分，未把模型估计写成文献实测值
- [ ] 已报道性质和制备路线各自标注证据来源
- [ ] 研究目标已落盘为文件，不只在对话中
- [ ] 给出下游 `database_search` 的检索关键词或子空间引导

## 与上下游的衔接

- **上游**：用户需求（无上游 substage）
- **下游**：`database_search`（按目标检索 MP）、`candidate_generation`（种子不足时演化补充）
