# Fixtures 规范

## 1. 文档作用

本文用于约束 OpenAIR 中 fixtures 的存放、命名、内容和使用方式。

Fixtures 不是普通假数据。它们是用于前后端并行开发、Claude SDK 联调、Skill 回放、任务详情展示和态势看板统计的流程样例资产。

## 2. Fixtures 定位

Fixtures 存放：

- 跑通过的流程样例；
- recorded runs；
- 任务状态流转样例；
- Claude 调用返回样例；
- 阶段结果样例；
- 产物样例；
- 能力调用记录样例；
- 失败样例；
- 态势看板统计样例。

Fixtures 不等于：

- 随手写的 mock；
- 临时测试数据；
- 产品宣传文案；
- 真实生产数据；
- 没有来源说明的科研结论。

## 3. 目录结构

推荐结构：

```text
fixtures/
  README.md

  recorded_runs/
    opentrajectory_demo_001/
      task.json
      stages.json
      capability_calls.json
      artifacts.json
      events.json
      summary.md

  task_states/
    task_list_sample.json
    task_detail_sample.json

  artifacts/
    artifact_sample.md
    artifact_sample.json

  capability_calls/
    capability_call_sample.json

  dashboard_samples/
    situation_board_sample.json
    task_board_sample.json

  skill_io/
    literature_search/
      request.json
      response.json
````

## 4. 命名规则

流程级样例命名：

```text
<domain>_<scenario>_<number>
```

示例：

```text
opentrajectory_demo_001
cs_research_demo_001
materials_parameter_optimization_001
```

文件命名使用小写英文和下划线。

禁止：

```text
测试数据1
最终版
demo_new
随便跑的
```

## 5. recorded run 必备文件

一个流程级 recorded run 至少包含：

```text
task.json
stages.json
capability_calls.json
artifacts.json
summary.md
```

推荐补充：

```text
events.json
errors.json
dashboard_metrics.json
README.md
```

## 6. task.json 要求

`task.json` 至少包含：

```json
{
  "task_id": "task_demo_001",
  "title": "示例科研任务",
  "user_goal": "用户输入的科研目标",
  "domain": "general_research",
  "current_stage": "solution_design",
  "status": "running",
  "created_at": "2026-05-12T00:00:00Z",
  "updated_at": "2026-05-12T00:00:00Z"
}
```

## 7. stages.json 要求

`stages.json` 的阶段数量、顺序、名称和进入/完成条件应由对应 workflow / starter 定义；旧四个 stage key 仅作为兼容枚举或 fallback，不得作为所有科研任务的固定流程。每项字段如下表：

| 字段 | 类型 | 说明 |
|------|------|------|
| `stage` | string | workflow / starter 定义的一级阶段 key；旧兼容 key 示例：`problem_modeling` / `solution_design` / `execution_validation` / `insight_generation` |
| `status` | string | 阶段级状态：`not_started` / `running` / `completed` / `failed` |
| `summary` | string \| null | 阶段摘要 |
| `risks` | string \| null | 阶段风险提示，供任务详情页「阶段信息」卡片展示；未识别风险时为 `null` |
| `next_steps` | string \| null | 下一步建议，可指向后续阶段或后续动作；未规划时为 `null` |
| `artifact_refs` | string[] | 关联产物 ID 列表，指向 `artifacts.json` 中的 `artifact_id` |
| `sub_stages` | array | 二级阶段列表，结构见下表；未拆分时可为 `[]` |

二级阶段（`sub_stages` 单项）字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `sub_stage` | string | 二级阶段名称（小写蛇形），如 `literature_review` |
| `status` | string | 二级阶段状态：`not_started` / `running` / `completed` / `failed` |
| `artifact_refs` | string[] | 该二级阶段产物 ID 列表，应是父阶段 `artifact_refs` 的子集 |

示例：

```json
[
  {
    "stage": "problem_modeling",
    "status": "completed",
    "summary": "已完成问题理解。",
    "risks": "假说依赖较强分布前提，需后续验证。",
    "next_steps": "进入 solution_design 阶段设计鲁棒性实验。",
    "artifact_refs": ["art-001"],
    "sub_stages": [
      {
        "sub_stage": "literature_review",
        "status": "completed",
        "artifact_refs": ["art-001"]
      }
    ]
  },
  {
    "stage": "solution_design",
    "status": "running",
    "summary": "正在形成候选方案。",
    "risks": null,
    "next_steps": null,
    "artifact_refs": [],
    "sub_stages": []
  }
]
```

允许的阶段级与二级阶段状态：

```text
not_started
running
completed
failed
```

> 共享类型见 `packages/shared/src/task.ts` 中的 `StageDetail` / `SubStage`；前后端 API 字段名为 camelCase，fixture 磁盘字段为 snake_case，由后端 adapter 转换。

## 8. capability_calls.json 要求

能力调用记录至少包含：

```json
[
  {
    "call_id": "call_001",
    "skill_id": "literature_search",
    "runtime": "claude_agent_sdk",
    "status": "completed",
    "started_at": "2026-05-12T00:00:00Z",
    "ended_at": "2026-05-12T00:01:00Z",
    "input_ref": "skill_io/literature_search/request.json",
    "output_ref": "skill_io/literature_search/response.json",
    "artifact_refs": [],
    "error": null
  }
]
```

## 9. artifacts.json 要求

产物记录至少包含：

```json
[
  {
    "artifact_id": "artifact_001",
    "type": "stage_summary",
    "title": "阶段结果摘要",
    "format": "markdown",
    "path": "artifacts/artifact_sample.md",
    "created_by": "claude_agent_sdk",
    "related_stage": "solution_design",
    "related_skill_id": "literature_search"
  }
]
```

产物类型建议：

```text
stage_summary
research_note
evidence_pack
graph_data
report_section
code_result
experiment_record
error_report
```

> 权威中文标签见 `packages/shared/src/i18n/dict.ts` 中 `ARTIFACT_TYPE_LABELS`；新增产物类型时需同步该映射。

## 10. 失败样例

必须保留失败样例。

失败样例用于验证：

* 前端失败态展示；
* 后端错误记录；
* 任务详情回看；
* 下一步行动提示；
* Claude 调用异常处理。

失败结构示例：

```json
{
  "status": "failed",
  "error": {
    "code": "SKILL_EXECUTION_FAILED",
    "message": "Skill 执行失败。",
    "recoverable": true,
    "suggested_action": "请检查输入样例或重试。"
  }
}
```

## 11. Fixtures 与 Skill examples 的关系

区别：

```text
skills/<domain>/<skill_id>/examples/
  单个 Skill 的最小输入输出样例

fixtures/
  产品流程级样例和 recorded runs
```

一个 recorded run 可以引用多个 Skill examples，也可以包含实际运行后的输出副本。

## 12. 使用场景

Fixtures 用于：

* 前端主工作台开发；
* 任务看板开发；
* 任务详情开发；
* 态势看板开发；
* 后端接口联调；
* Claude SDK 最小调用验证；
* Skill 回放测试；
* PR 验收；
* 演示数据准备。

## 13. 不得存放的内容

禁止在 fixtures 中存放：

* 密钥；
* token；
* 私有接口地址；
* 未脱敏用户数据；
* 不可公开的实验数据；
* 大体积原始文件；
* 无来源说明的科研结论；
* 临时调试垃圾文件。

大体积文件应放外部存储或专门数据目录，并在 fixture 中保留引用。

## 14. Sprint01 要求

Sprint01 不要求每个 Skill 都有 fixture。

但必须优先准备：

* 主工作台流程样例；
* 任务看板列表样例；
* 任务详情样例；
* Claude SDK 最小调用样例；
* skill_management 检查样例；
* OpenTrajectory 能力转 Skill 的代表样例。

## 15. 维护规则

以下变化需要同步 fixtures：

* 任务状态字段变化；
* 阶段字段变化；
* 能力调用字段变化；
* 产物字段变化；
* Skill 输入输出变化；
* 前端页面展示字段变化；
* Claude SDK 返回结构变化。

## 16. 验收标准

一个 fixture 可用前，至少满足：

* 命名清楚；
* 有来源说明；
* 字段结构稳定；
* 不含敏感信息；
* 能被前端或后端读取；
* 能支撑一个明确开发或验收场景；
* 不是随手写的孤立 mock。

## 17. 推荐原则

Fixtures 要少而稳。

优先维护少量高质量流程样例，不堆大量不可验证的数据文件。
