# `_template/` — OpenAIR Skill 模板

新建 OpenAIR Skill 时复制本目录作为起点。

## 文件用途与必/可选

| 文件 | 用途 | 何时必须 |
|---|---|---|
| `SKILL.md` | Claude Code 读取和执行的主说明 | **必须**（所有 skill） |
| `skill.yaml` | OpenAIR 管理元数据 | **必须**（所有 skill） |
| `input.schema.json` | 输入 JSON Schema | 仅 `llm_structured` / `python_adapter` runtime 必填 |
| `output.schema.json` | 输出 JSON Schema | 仅 `llm_structured` / `python_adapter` runtime 必填 |
| `README.md` | 本说明 | 复制后可删 |

## 复制流程

1. `cp -r skills/_template/ skills/<pack>/<claude_name>/`
2. 删除复制后的 `README.md`（它是模板说明，不属于 skill 本身）
3. 改 `skill.yaml`：替换所有 `<占位>`；按 skill_type（L2/L3）和 runtime 决定保留哪些字段段
4. 改 `SKILL.md`：填 frontmatter（name+description）和正文；删除注释块
5. 若 runtime = llm_structured/python_adapter：填写 `input.schema.json` 和 `output.schema.json`
6. 跑注册：`python scripts/register_skills.py update`

## 字段决策指引

- **L2 orchestration**（新建 substage）：`skill_type: orchestration` + 取消注释 `pack` / `spec` / `output_file` 段
- **L3 atomic + claude_code runtime**（最常见）：模板默认骨架，删除所有注释段
- **L3 atomic + llm_structured/python_adapter runtime**：取消注释对应 runtime 段 + 必填 schema 文件

完整字段消费矩阵见 `docs/dev_skills/openair-skill-guider/SKILL.md`。
