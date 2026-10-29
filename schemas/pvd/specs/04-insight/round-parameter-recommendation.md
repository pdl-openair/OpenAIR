# PVD Experiment Recommendation / Portfolio Design

## 用户价值

在正式参数方向和可行性已经确定后，为当前动态参数集合给出真正可执行的具体参数方案，并在需要时形成有价值的探索组合。

## Input

- `structured_context`
- `recommendation_parameter_set`
- ResearchRecords
- comparability / `allowed_uses`
- Candidate Direction Analysis
- Feasibility & Experimental Admission
- ScientificState

## Output

- primary recommendation
- optional exploratory portfolio
- 每组完整动态参数向量
- rationale / risk / unknown conditions / execution requirements

## Acceptance

- F 是 concrete value 的最终科研 Authority；
- 参数集合与上游 formal scope 完全一致；
- concrete vector 必须经过 deterministic hard-boundary validation；
- 用户请求 1–10 组时按当前正式合同解释组数目标；
- exploratory failure 不丢失 primary；
- 证据不足时不生成 filler；
- legacy Skill ID 中的 four-parameter 命名不得把行为退回固定四参数。
