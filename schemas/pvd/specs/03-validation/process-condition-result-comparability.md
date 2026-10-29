# PVD Evidence Comparability & Scientific-Use Authorization

## 用户价值

判断外部 Evidence / ResearchRecords 与当前实验体系是否可比较、可以支持什么科研用途，以及哪些迁移限制必须保留。

## Input

- `structured_context`
- ResearchRecords / Evidence
- conditional support targets（如存在）

## Capability

- Evidence Comparability & Scientific-Use Authorization
- 407b 正式链内部保持 Global Planner → 并行 Deep Verification → Synthesis。
- Synthesis 是 `allowed_uses` 的最终科研授权 Authority。

## Output

- comparability analysis
- support level
- transfer limitations
- `allowed_uses`（仅 `mechanism` / `trend_direction` / `magnitude_reference` / `direct_numeric`；每个 support target 最多保留一个最强正向授权）
- 每个 target conclusion 对应的 verified record / Evidence provenance、transfer limitations、conflicts、important unknowns
- conditional support-target results

## Acceptance

- Deep Verification 不直接产生最终 `allowed_uses`；
- 单个 deep batch 失败局部隔离；
- Synthesis 正常路径必须存在；
- 普通 synthesis timeout/provider failure 记为 capability unavailable，不伪造 degraded synthesis；
- 每个正向 `allowed_uses` 必须追到同一 support target 下已完成 Deep Verification 的具体 record，并由确定性代码恢复其已核验 Evidence provenance；
- 比较关系本身不构成 `allowed_uses`，`relative_comparison` 不作为独立科研用途授权；
- transfer limitations、conflicts、important unknowns 随对应 target conclusion 保留；
- Final Synthesis 不可用或授权 provenance 不闭合时，不产生正向 `allowed_uses`；
- pending parameter 只作为 support target，不自动升格正式参数。
