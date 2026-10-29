# Method — Analysis Plan

<!-- substage: analysis_planning
     预注册分析计划：在看到数据前声明分析方法，防止 p-hacking 和 HARKING。
     产出汇入 experiment_design.md 的「Analysis Plan」部分。 -->

## 预注册分析方法 (Preregistered Analysis Methods)

| Analysis | Target Hypothesis | Method | Tool / Library | Expected Output |
|----------|-------------------|--------|----------------|-----------------|
| <!-- name --> | H-xxx | <!-- e.g. t-test, ANOVA, regression --> | <!-- e.g. scipy, R --> | <!-- e.g. p-value, effect size --> |

## 统计检验 (Statistical Tests)

<!-- 每个假设对应的检验方法、前提假设检查。 -->

| Hypothesis | Test | Assumptions | Assumption Check |
|-----------|------|-------------|-----------------|
| H-xxx | <!-- --> | <!-- e.g. normality, homoscedasticity --> | <!-- e.g. Shapiro-Wilk --> |

## 显著性阈值 (Significance Threshold)

- **Alpha:** <!-- e.g. 0.05, 0.01 -->
- **Confidence interval level:** <!-- e.g. 95% -->

## 多重比较校正 (Multiple Comparison Correction)

<!-- 是否需要校正，使用哪种方法。 -->

- **Method:** <!-- e.g. Bonferroni, FDR-BH, Holm, none -->
- **Number of comparisons:** <!-- -->
- **Adjusted alpha:** <!-- -->

## 偏差处理协议 (Deviation Protocol)

<!-- 如果实际分析与预注册计划有偏差，如何记录和处理。 -->

- Any deviation MUST be documented with: what changed, why, and when the decision was made (before or after seeing data).
