# T05-03｜科研用途授权与可信边界

## 正式责任链

`verified Evidence / ResearchRecord → support target → support conclusion → allowed_uses`

- Final Synthesis 是唯一科研授权 Authority。
- Final Synthesis 负责科研选择：哪些已完成 Deep Verification 的 record 实际支撑哪个 support target，以及该 target 最强可授权用途是什么。
- 确定性 adapter 只负责校验 record/target 身份、恢复已经由 Deep Verification 引用的 T04 Evidence provenance、生成稳定 `conclusion_id`、结构校验与 fail-closed。
- 代码不得根据 support level、关键词、分数或字段存在性自行推导 `allowed_uses`。

## `allowed_uses` 正式四类

每个 support target 最多保留一个最强正向授权：

1. `mechanism`
2. `trend_direction`
3. `magnitude_reference`
4. `direct_numeric`

比较关系（例如 matched / relative comparison）是证据关系事实，不是独立下游使用许可，因此不作为 `allowed_uses`。

## Provenance

每个正式 target conclusion 必须携带：

- `conclusion_id`：由代码基于冻结 `target_id` 确定性形成；
- `judgment_target`：冻结的 T03 support target；
- `evidence_record_refs`：Final Synthesis 从成功 Deep Verification 中明确选择；
- `evidence_refs`：代码仅从这些 record 在该 target 下已经核验的 `evidence_refs_used` 确定性恢复；
- `transfer_limits` / `conflicts` / `important_unknowns`：由 Final Synthesis 形成并随该 conclusion 保留；
- `allowed_uses`：只有 Final Synthesis 可以签发。

正向授权若无法闭合到“已核验 record + 同一 target + 具体 Evidence”，整个 Final Synthesis 输出按授权 fail-closed 处理，不把未经核验内容升级为正式授权。

## 后置边界

- Registry / Binding / WorkUnit / Python adapter 的正式生产激活属于 T05-04。
- Fresh 主链与 T06 输入边界验收属于 T05-05。
- 本 Task 不生成候选方向、参数值或推荐。
