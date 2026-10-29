# Wave1-04 After

结论=未达到97

Before=历史 12 个真实上游回放平均 89.8/100、当时无 critical failure；当前主 `SKILL.md` 13696 bytes。历史输出快照不是当前合同的动态基线，因为它们早于 `assembled_facets` 和当前恰好 8 条 variant 的 Schema。

After=完成一次最小、可泛化的 Skill 说明治理；12 个既有输入 + 6 个预先固定的边界输入均通过当前 input schema；Schema 定义和 example request/response 均通过；关键说明静态检查 10/10 通过。真实 Provider After 未运行，因为 slim pack 缺少运行时服务/Provider 路径，`tests/p0_validation.py` 在导入 `app.execution.retrieval_matrix_sanitizer` 时失败。

真实改善=
- 把固定 7 intent 明确为结构槽位，缺少证据时允许空，停止为填槽位补造机制/方法/指标。
- 删除电池专用相邻词和“领域里常见即可加入”的路径，改为只允许可追溯事实、直接同义词/标准术语邻域。
- `risk_flags`、未确认 candidate、明确排除项三类语义重新分开；排除只来自用户明确排除或 out_of_scope。
- Boolean 统一为显式分组，并将 NOT 形式收敛为 `AND NOT (...)`；同义词按概念簇 OR，不再靠多条近重复 query 展开。
- 明确比较 baseline 必须保留在高优先级检索；来源范围约束不再伪装成主题 query。
- 修复说明与当前 Schema 的直接冲突：从“至少 8 条”改为“恰好 8 条”，并移除 Schema 不允许的 `upstream_quality` 输出要求。

真实退化=静态检查未发现确定性退化；但没有真实 Provider After，不能确认模型在 18 个场景上的稳定性，也不能确认允许空 intent 后是否会在恰好 8 条 variant 的约束下产生新的 fallback 重复。

性能变化=主 `SKILL.md` 13696 -> 9473 bytes（-30.8%）；`SKILL.md + intent-guide.md` 16344 -> 12907 bytes（-21.0%）。无真实模型输入/输出 token、模型耗时和总耗时数据。

最高价值剩余缺口=用真实目标 Provider 对同一 12 个回放 + 6 个边界场景执行 After，并按本任务 100 分 rubric 评分。尤其观察“无明确约束”时，当前 Schema 强制恰好 8 条 query 是否仍能避免语义重复和无依据扩展；若真实证据显示无法同时满足，才升级为 `NEEDS_CONTRACT_CHANGE`。

修改文件=
- `SKILL.md`
- `references/intent-guide.md`
- `evals/wave1_04_cases.json`
- `evals/wave1_04_before.md`
- `evals/wave1_04_after.md`
- `evals/wave1_04_static_validation.py`

验证命令/场景=
- `python tests/p0_validation.py`：环境阻塞，缺少 `services/api/app.execution.retrieval_matrix_sanitizer`，与 TASK 已知限制一致。
- `python evals/wave1_04_static_validation.py`：PASS；Schema/example/12 个既有输入/6 个边界输入/10 项说明检查全部通过。
- 边界场景：无明确约束、明确 out-of-scope、仅 source-scope 约束、明确比较 baseline、多个同义对象、完全非材料领域。
- 目标范围检查：所有改动均位于 `skills/opentrajectory/retrieval-matrix-generation/`。
