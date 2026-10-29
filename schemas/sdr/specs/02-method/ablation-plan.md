# Method — Ablation Plan

<!-- substage: ablation_design
     规划逐步移除/替换组件的消融实验（Type M/H required, Type C/D optional）。
     产出汇入 experiment_design.md 的「Ablation Plan」部分。 -->

## 消融组件 (Components to Ablate)

<!-- 列出拟消融的方法组件，每个组件对应一个假设贡献。 -->

| # | Component | Contribution Claim | Maps to Hypothesis |
|---|-----------|-------------------|-------------------|
| 1 | <!-- component name --> | <!-- 该组件声称的贡献 --> | H-xxx |
| 2 | <!-- component name --> | <!-- --> | H-xxx |

## 消融矩阵 (Ablation Matrix)

<!-- 每行一次消融：移除/替换/简化一个组件。
     所有消融使用相同的种子集合（见 reproducibility.md）。 -->

| # | Component | Ablation Method | Expected Impact on Primary Metric | Rationale |
|---|-----------|----------------|-----------------------------------|-----------|
| 1 | <!-- component name --> | remove / replace / simplify | <!-- predicted delta --> | <!-- why --> |
| 2 | <!-- component name --> | remove / replace / simplify | <!-- predicted delta --> | <!-- why --> |

## 预期影响 (Expected Impact per Component)

<!-- 每个组件消融后对主指标的预期影响方向和量级。 -->

| Component | Metric | Expected Direction | Expected Magnitude | Confidence |
|-----------|--------|-------------------|-------------------|------------|
| <!-- --> | <!-- --> | increase / decrease / neutral | <!-- --> | high / medium / low |

## 报告指标 (Metrics to Report)

<!-- 消融实验中需要报告的指标。 -->

| Metric | Definition | Primary / Secondary |
|--------|-----------|-------------------|
| <!-- --> | <!-- --> | primary / secondary |
