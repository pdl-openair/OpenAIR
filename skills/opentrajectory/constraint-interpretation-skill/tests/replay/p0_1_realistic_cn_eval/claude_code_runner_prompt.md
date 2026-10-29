# Claude Code Prompt: Run Skill 1 P0.1 Realistic Chinese Replay

你现在在 `constraint-interpretation-skill` 目录下工作。

任务：对 Skill 1 做 P0.1 真实中文 query 效果验证。不要修改 skill 文件。

请执行：

1. 阅读：
- SKILL.md
- input.schema.json
- output.schema.json
- references/
- tests/replay/p0_1_realistic_cn_eval/replay_cases.json
- tests/replay/p0_1_realistic_cn_eval/gold_expectations.json
- tests/replay/p0_1_realistic_cn_eval/effect_eval_rubric.md

2. 对 replay_cases.json 中 12 个 case 逐一运行 constraint-interpretation-skill。
每个 case 只使用 case.input 作为 skill 输入。
注意：这些 query 全部是中文，且包含精确、模糊、混合和信息不足情况。不要为了迎合 gold_expectations 修改输出。

3. 将每个 case 原始输出保存到：
tests/replay/p0_1_realistic_cn_eval/outputs/{case_id}.json

4. 生成报告：
tests/replay/p0_1_realistic_cn_eval/replay_report.md

报告必须包含：
- 每个 case 是否通过
- 每个 case 的评分，满分 100
- 失败项归因：约束遗漏 / 类型错误 / confidence 不准 / search terms 漂移 / 中文混入 / hard filter 误用 / risk_flags 缺失 / JSON/schema 错误 / 过度臆测
- 三个方向平均分：battery_materials / agent_systems / theoretical_materials
- 按输入类型平均分：precise / mixed / fuzzy / underspecified
- 总平均分
- 是否达到 P0.1 realistic regression pass
- 是否存在 critical failure
- 建议：是否需要 P0.2，还是可以进入 Skill 2 P0

5. 验收标准：
- 无 critical failure 是硬门槛
- 总平均分 >= 82：P0.1 realistic regression pass
- 总平均分 >= 85：strong pass
- fuzzy/underspecified case 单项低于 80 不必直接判失败，但必须说明问题来自 query 不足还是 Skill 解释问题

只做验证和报告，不要修改 skill。
