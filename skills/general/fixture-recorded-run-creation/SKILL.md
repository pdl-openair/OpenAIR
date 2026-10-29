---
name: fixture-recorded-run-creation
description: >-
  当用户想要将实际科研流程的节点和输入输出转化为 OpenAIR fixtures 样例时自动触发。
  常见场景：用户说"帮我创建 fixture"、"把我的流程记录转成 fixtures"、"提交一组流程样例到 fixtures 目录"、
  "为 recorded run 创建 task.json 和 skill_io 样例"、"我有实际运行记录，帮我整理成 fixtures"。
  本 skill 负责引导用户从实际科研流程中提取阶段节点、能力调用和产物信息，
  按 fixture_rules.md 规范生成 recorded run 和 skill_io 样例文件。
---

# Fixture Recorded Run 创建

将用户的实际科研流程记录转化为符合 `fixtures/fixture_rules.md` 规范的 recorded run 样例。

## 适用场景

用户有一组已执行或正在执行的科研任务（含阶段节点、能力调用、产物），想要创建为 fixtures 用于前端联调、看板展示或 Skill 回放。

## 核心原则

1. **Fixtures 不是假数据** — 每个样例来自真实流程，有明确来源和用途
2. **严格遵循 fixture_rules.md** — 字段名、结构、命名规则全部以规范为准
3. **用户无关** — 不包含用户绝对路径、密钥、私有信息
4. **skill_io 是 examples 的超集** — 输入输出样例必须包含基础字段

## 前置确认

开始前，先用 **AskUserQuestion 工具** 确认以下信息：

1. **流程场景描述** — 这是什么科研任务？包含哪些阶段？
2. **阶段列表和状态** — 每个阶段的完成状态（completed / running / not_started / failed）
3. **能力调用清单** — 每个阶段调用了哪些 Skill？
4. **产物文件** — 有哪些产物需要复制到 fixtures？
5. **产物引用方式** — 复制到 fixtures 本地（推荐）还是引用外部路径？
6. **时间戳处理** — 用实际日期估算还是用占位日期？

## 创建流程

### Step 1: 创建 recorded run 目录

```
fixtures/recorded_runs/<domain>_<scenario>_<number>/
```

命名规则：`<domain>_<scenario>_<number>`（如 `cs_research_ope_001`）。

### Step 2: 创建 task.json

按 fixture_rules.md 第 6 节，task.json 只包含精简字段：

```json
{
  "task_id": "<任务ID>",
  "title": "<任务标题>",
  "user_goal": "<用户输入的科研目标>",
  "domain": "<能力域>",
  "current_stage": "<当前阶段>",
  "status": "<任务状态>",
  "created_at": "<ISO时间戳>",
  "updated_at": "<ISO时间戳>"
}
```

字段说明：
- `domain` 从 `common_research` / `materials` / `cs_research` / `opentrajectory` / `skill_management` 中选择
- `status` 从 `not_started` / `running` / `completed` / `failed` 中选择
- **不要**内嵌 artifacts、confirmations、capabilityCalls 数组

### Step 3: 创建 stages.json

按 fixture_rules.md 第 7 节，每个阶段只包含 4 个字段：

```json
[
  {
    "stage": "<阶段名>",
    "status": "<阶段状态>",
    "summary": "<阶段摘要>",
    "artifact_refs": ["<产物ID列表>"]
  }
]
```

允许的阶段状态：`not_started` / `running` / `completed` / `failed`

注意：阶段数量以用户实际流程为准，不强制四阶段。

### Step 4: 创建 capability_calls.json

按 fixture_rules.md 第 8 节，每个调用包含：

```json
[
  {
    "call_id": "call_001",
    "skill_id": "<Skill名称>",
    "runtime": "claude_agent_sdk",
    "status": "completed",
    "started_at": "<ISO时间戳>",
    "ended_at": "<ISO时间戳>",
    "input_ref": "skill_io/<skill_id>/request.json",
    "output_ref": "skill_io/<skill_id>/response.json",
    "artifact_refs": ["<产物ID列表>"],
    "error": null
  }
]
```

运行中的调用：`"status": "running"`，`"ended_at": null`。

### Step 5: 创建 artifacts.json

按 fixture_rules.md 第 9 节，每个产物包含：

```json
[
  {
    "artifact_id": "art-001",
    "type": "<产物类型>",
    "title": "<产物标题>",
    "format": "markdown",
    "path": "<本地文件名>",
    "created_by": "claude_agent_sdk",
    "related_stage": "<关联阶段>",
    "related_skill_id": "<关联Skill>"
  }
]
```

产物类型从以下选择：`stage_summary` / `research_note` / `evidence_pack` / `graph_data` / `report_section` / `code_result` / `experiment_record` / `error_report`

如果用户选择复制产物到本地，在此步骤复制文件并记录 `path` 为本地文件名。

### Step 6: 创建 skill_io 样例

为 capability_calls.json 中每个 skill_id 创建配对文件：

```
fixtures/skill_io/<skill_id>/
  request.json
  response.json
```

**request.json 必须包含基础字段**（skill_replay_test_rules.md 第 6 节）：

```json
{
  "task_id": "<任务ID>",
  "run_id": "<运行ID>",
  "stage": "<阶段>",
  "user_goal": "<用户目标>",
  "context": {},
  "constraints": []
}
```

`context` 中放 Skill 特有的超集字段。`constraints` 中放执行约束。

**response.json 必须包含基础字段**（skill_replay_test_rules.md 第 7 节）：

```json
{
  "status": "completed",
  "summary": "<输出摘要>",
  "artifacts": [],
  "capability_calls": [],
  "next_actions": [],
  "error": null
}
```

失败样例的 error 结构：

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "错误说明",
    "recoverable": true,
    "suggested_action": "建议处理方式"
  }
}
```

### Step 7: 创建 summary.md

```markdown
# <目录名>：<任务简述>

## 场景
（描述这组样例模拟什么科研流程）

## 阶段节点
（阶段列表和状态）

## 能力调用清单
（按阶段列出调用）

## 产物文件
（文件列表和来源）

## 用途
（前端联调 / Skill 回放 / 看板展示等）
```

### Step 8: 自检清单

创建完成后，逐项检查：

```text
□ 目录命名：<domain>_<scenario>_<number> 格式
□ 5 个必备文件：task.json / stages.json / capability_calls.json / artifacts.json / summary.md
□ task.json：精简结构，snake_case 字段，无内嵌 artifacts/confirmations
□ stages.json：只有 stage/status/summary/artifact_refs 四个字段
□ capability_calls.json：含 skill_id/runtime/input_ref/output_ref/artifact_refs/error
□ artifacts.json：含 artifact_id/type/title/format/path/created_by/related_stage/related_skill_id
□ skill_io/：每个 skill_id 都有 request.json + response.json
□ request.json 含 task_id/run_id/stage/user_goal/context/constraints
□ response.json 含 status/summary/artifacts/capability_calls/next_actions/error
□ 不含用户绝对路径、密钥、token
□ 不含未脱敏数据
□ 产物文件已复制到本地（如用户选择）
□ artifact_refs / related_skill_id 交叉引用一致
```

## 禁止事项

- 禁止包含用户绝对路径（如 `S:\research\...`）
- 禁止包含密钥、token、私有接口地址
- 禁止用 camelCase 字段名（fixtures 统一用 snake_case）
- 禁止内嵌 artifacts/confirmations 到 task.json
- 禁止创建无来源说明的样例
- 禁止把 fixture_rules.md 示例中的通用占位值原样复制（应替换为实际内容）
