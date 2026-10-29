# Field Mapping

## Input to Output

- `user_query` -> `original_user_query`

## Output Field Semantics

- `research_object`: 研究对象或核心问题，一句话表达，不扩写成方案。
- `target_metrics`: 检索和证据分析需要覆盖的评估指标、优化目标、观察结果或性能维度。
- `boundary_conditions`: 用户明确给出的材料/体系/场景/实验条件/约束范围。
- `comparison_baseline`: 对照对象、默认方案、基线材料、基准方法或比较集合。
- `output_intent`: 标准意图枚举：`comparison` / `summary` / `decision_support` / `literature_review` / `hypothesis_generation` / `null`。
- `requested_source_scope`: 用户明确提出的来源范围，如时间、语言、论文类型、可复现性、高被引、综述/实验论文等。
- `ambiguities`: 会影响检索召回、证据判断或下游路线生成的关键缺口。
- `requirement_confidence`: 对结构化完整性的估计，范围 `[0,1]`。
