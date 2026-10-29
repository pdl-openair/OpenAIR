# 文件模板：recorded run 各 JSON 文件

本文档提供 recorded run 各文件的空模板，使用时替换 `<>` 占位符为实际内容。

## task.json

```json
{
  "task_id": "<任务ID，如 task_demo_001>",
  "title": "<任务标题>",
  "user_goal": "<用户输入的科研目标>",
  "domain": "<common_research|materials|cs_research|opentrajectory|skill_management>",
  "current_stage": "<当前阶段>",
  "status": "<not_started|running|completed|failed>",
  "created_at": "<ISO时间戳>",
  "updated_at": "<ISO时间戳>"
}
```

## stages.json

```json
[
  {
    "stage": "<阶段名>",
    "status": "<not_started|running|waiting_user|completed|failed|skipped>",
    "summary": "<阶段摘要>",
    "artifact_refs": []
  }
]
```

## capability_calls.json

```json
[
  {
    "call_id": "call_001",
    "skill_id": "<Skill名称>",
    "runtime": "claude_agent_sdk",
    "status": "<completed|running|failed>",
    "started_at": "<ISO时间戳>",
    "ended_at": "<ISO时间戳或null>",
    "input_ref": "skill_io/<skill_id>/request.json",
    "output_ref": "skill_io/<skill_id>/response.json",
    "artifact_refs": [],
    "error": null
  }
]
```

## artifacts.json

```json
[
  {
    "artifact_id": "art-001",
    "type": "<stage_summary|research_note|evidence_pack|graph_data|report_section|code_result|experiment_record|error_report>",
    "title": "<产物标题>",
    "format": "<markdown|json|csv>",
    "path": "<本地文件名>",
    "created_by": "claude_agent_sdk",
    "related_stage": "<关联阶段>",
    "related_skill_id": "<关联Skill>"
  }
]
```

## skill_io/request.json

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

## skill_io/response.json

```json
{
  "status": "<completed|running|failed>",
  "summary": "<输出摘要>",
  "artifacts": [],
  "capability_calls": [],
  "next_actions": [],
  "error": null
}
```

## summary.md

```markdown
# <目录名>：<任务简述>

## 场景

（描述这组样例模拟什么科研流程）

## 阶段节点

| 阶段 | 状态 | 核心产物 |
|------|------|---------|
| <阶段> | <状态> | <产物> |

## 能力调用清单

| 阶段 | 调用 | 说明 |
|------|------|------|
| <阶段> | <skill_id> | <说明> |

## 产物文件

| 文件 | 阶段 | 来源 |
|------|------|------|
| <文件名> | <阶段> | <原始路径> |

## 用途

- <用途1>
- <用途2>
```
