# PVD Candidate Direction Analysis

## 用户价值

基于当前正式参数集合、可比性授权和真实反馈诊断，判断每个参数下一步应增、减、保持或保留不确定。

## Input

- `structured_context`
- `recommendation_parameter_set`
- comparability + `allowed_uses`
- ScientificState
- experimental result diagnosis（后续轮次可选）

## Output

- 与正式参数集合完全一致的方向判断
- uncertainty / rationale / evidence basis

## Acceptance

- exact dynamic scope：不得新增、删除或重命名参数；
- 允许 uncertainty；
- 不产生 concrete recommendation values；
- 首轮和后续轮次复用同一科研能力；
- BO 只是后续可选确定性候选工具，不拥有方向 Authority。
