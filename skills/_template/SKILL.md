---
name: <kebab-case-skill-name>
description: >-
  <一句话说明这个 Skill 做什么、什么时候触发。写得"主动"一些——
  涵盖用户可能的多种表述方式，包括直接调用和隐含需求的场景。
  非空、≤1024 字符。这是触发机制的主要依据，注册脚本会读它写进 substage-skill-map.yaml。
  所有"何时使用"的信息放这里，**不放正文**（不要写 "## When to use" 章节）。
---

# <Skill Display Name>

## Input

<描述输入字段。仅 llm_structured / python_adapter runtime 需要严格匹配 input.schema.json；
claude_code runtime 用自然语言描述输入边界即可。>

## Steps

1. <步骤名> — <做什么，输出什么>
2. <步骤名> — <做什么，输出什么>
3. <步骤名> — <做什么，输出什么>

## Output

<产出什么。L2 orchestration：明确产出路径，与 skill.yaml.output_file 一致
（如"写入当前任务工作区的 <output_file>"）。
L3 atomic（claude_code runtime）：产出附 L2，不在此声明独立 output_file。>

## Failure handling

<失败时如何返回结构化错误、如何降级。覆盖：输入缺失、目标模糊、范围不支持等。>

---

<!--
OpenAIR 增强约束（复制后删除本注释块）：
- frontmatter.name 必须等于父目录名 = skill.yaml.claude_name（三者完全一致）
- frontmatter.description 非空、≤1024 字符；**所有触发信息放这里**——正文不要写 "## When to use" 章节（skill-creator 规范）
- 治理字段（skill_id / pack / substages / spec / runtime / skill_type / output_file 等）放 skill.yaml，不要写进 SKILL.md 正文
- 主体写作风格（intent capture → interview → eval/iterate → description 优化）见
  docs/dev_skills/openair-skill-guider/reference/skill-creator/SKILL.md
-->

