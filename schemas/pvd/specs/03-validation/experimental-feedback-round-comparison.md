# PVD Experimental Result Diagnosis

## 用户价值

在真实实验反馈已经通过事实准入并写入 ScientificState 后，对结果做科学解释、异常诊断，并判断下一轮研究是否具备继续条件。

## Input

- ScientificState 中已正式写入的 actual execution / measurements
- current structured context
- comparability `allowed_uses`
- prior direction / recommendation（仅作上下文）

## Output

- experimental result diagnosis
- anomaly / change interpretation
- downstream readiness

## Acceptance

- 输入事实准入不属于本科研 capability；
- 缺正式参数或测量事实时不写入伪历史；
- 只使用 comparability 正式允许的 detailed uses；
- trend-direction-only 证据不越权进入 diagnosis；
- diagnosis 不拥有实验事实，不建立第二套历史；
- 后续直接复用 Direction → optional BO → Feasibility → Recommendation。
