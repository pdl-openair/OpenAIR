# PVD Parameter Feasibility & Experimental Admission

## 用户价值

判断候选方向或可选候选点是否能在真实设备、已知边界和当前缺失信息下安全、合理地进入实验。

## Input

- `structured_context`
- Candidate Direction Analysis
- optional BO candidates
- hard/device boundaries

## Output

- feasible / conditional / insufficient / infeasible 等明确状态
- conditions / risks / missing information
- recommendation gate

## Acceptance

- 保持正式动态参数 scope；
- 不修改上游 direction 或 candidate value；
- unavailable / insufficient 不得冒充 infeasible；
- 只有明确 infeasible 才能形成正式 block；
- 未提供的设备数值边界保持 unknown / not_supplied，不猜值。
