# Claude Code Prompt: Run Skill 2 P0 Effect Eval From Skill 1 Outputs

你现在在 `retrieval-matrix-generation` 目录下工作。

任务：对 Skill 2 做 P0 效果验证。不要修改 skill 文件，除非我后续明确要求。

## 重要背景

本验证集使用真实 Skill 1 输出，而不是手写输入。
Skill 1 输出来自中文真实 query replay，已经通过 Skill 1 P0.1 realistic regression。

请优先使用：

`tests/replay/p0_effect_eval_from_skill1_outputs/replay_cases_raw.json`

不要先用 normalized 版本美化结果。

## 执行步骤

1. 阅读：
- SKILL.md
- input.schema.json
- output.schema.json
- references/
- tests/replay/p0_effect_eval_from_skill1_outputs/replay_cases_raw.json
- tests/replay/p0_effect_eval_from_skill1_outputs/gold_expectations.json
- tests/replay/p0_effect_eval_from_skill1_outputs/effect_eval_rubric.md

2. 先对 raw cases 做 input schema validation。
如果失败，请明确记录：
- 哪些字段导致失败
- 是否是 `downstream_filter_rules` object/string 兼容问题
- 这是 contract failure，不要把它算成 query generation 质量失败
- 不要静默转换输入

3. 若 raw cases 通过 input schema validation，则逐一运行 retrieval-matrix-generation。
每个 case 只使用 `case.input` 作为 skill 输入。

4. 将每个 case 原始输出保存到：
`tests/replay/p0_effect_eval_from_skill1_outputs/outputs/{case_id}.json`

5. 生成报告：
`tests/replay/p0_effect_eval_from_skill1_outputs/replay_report.md`

报告必须包含：
- input schema validation 是否通过
- 每个 case 是否通过
- 每个 case 的评分，满分 100
- 失败归因：schema contract / intent 缺失 / variant_type 缺失 / query 中文混入 / Boolean 语法错误 / query 重复 / 过窄 / 过宽 / risk_flags 被转 NOT / 臆造材料或阈值
- 三个方向平均分：battery_materials / agent_systems / theoretical_materials
- 按输入类型平均分：precise / mixed / fuzzy / vague / underspecified
- 总平均分
- 是否达到 Skill 2 P0 effect pass
- 是否需要回改 Skill 2 P0 schema 或 SKILL.md

6. 验收标准：
- 无 critical failure 是硬门槛
- 总平均分 >= 82：P0 effect pass
- 总平均分 >= 85：strong pass
- 如果 raw cases 因 `downstream_filter_rules` object 失败，先判定为 schema contract issue，需要修 schema 后再跑效果验证。

只做验证和报告，不要修改 skill。
