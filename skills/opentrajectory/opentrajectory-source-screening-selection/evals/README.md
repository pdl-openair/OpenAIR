# 文献筛选与优选评测说明

本目录只用于评测，不形成新的生产 Authority。

## 目的

`evals.json` 固定 12 组代表性候选集，用于比较同一模型、同一运行条件下的 Before / After。覆盖任务要求的最小 10 类场景，并额外拆出 EvidenceSchema 部分覆盖与访问状态边界。

## 评分重点

沿用任务给定的 100 分结构：

- 必须深读来源召回与优先级：30
- `exclude` / `defer` 精度：20
- EvidenceSchema 目标对齐：15
- `reason` 的科研判别力：15
- 信息不足、摘要缺失、访问状态与相关性的边界：10
- 输出简洁与成本：10

出现以下任一情况，不能判为 97+：核心必读来源被排除或明显降级；明显无关来源进入 `priority`；把访问失败当成科研不相关；从标题/摘要编造事实；为了凑 role/target 强行映射。

## 动态运行方式

使用真实生产 Provider 时，必须让 Before 与 After 使用：

- 同一批 `evals.json` 输入；
- 同一模型、温度、Provider 和输入投影；
- 相同或等价的模型输出 Schema；
- 分别记录输入/输出 token、模型耗时和总耗时。

重点人工检查 `reason` 是否说明“这篇来源能提供什么会影响当前科研判断的证据”，而不是只写“相关/不相关”。

## 当前包内可执行验证

`tests/test_eval_suite.py` 只验证：

1. 12 组输入满足当前正式输入 Schema；
2. 场景覆盖达到任务要求；
3. oracle 不引用不存在的 candidate/target；
4. oracle 所要求的判断能够被当前 model/output/finalizer 合同合法表达并进入下游。

它不替代真实模型 Before / After，因此不能单独作为 97+ 证据。
