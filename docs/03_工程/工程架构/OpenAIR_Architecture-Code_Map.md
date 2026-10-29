# OpenAIR 工程架构与代码地图

> 状态：已确认  
> 更新时间：2026-08-28  
> 代码核验基线：`origin/dev @ 31b5e32e`  
> 用途：帮助开发者快速理解 OpenAIR 的主要工程分区，并找到对应代码入口。

---

## 1. 系统分成哪几块

```text
研究入口与方案
    ↓
执行决策
    ↓
任务执行
    ↓
结果与完成

研究协作区
    ↑
读取上述正式科研事实并呈现给用户
```

五个工程区域分别是：

1. **研究入口与方案**：接收研究问题，选择 Workflow，形成 ResearchDesign。
2. **执行决策**：根据当前科研状态决定下一步执行什么、使用哪个 Agent / Skill。
3. **任务执行**：把 WorkUnit 交给实际执行能力，并负责运行、提交和恢复。
4. **结果与完成**：保存研究产物、更新 ScientificState，并判断研究是否完成。
5. **研究协作区**：把正式科研事实组织成用户看到的研究过程和交互。

---

## 2. 我在改哪一块

### A. 研究入口与方案

| 我要处理什么 | 第一入口 | 继续看 |
| --- | --- | --- |
| 用户研究问题如何进入系统 | `routes/research_start.py` | `modules/start_router.py` |
| 系统如何选择研究流程 | `modules/start_router.py` | `modules/production_workflow_source.py` |
| Workflow 骨架在哪里定义 | `schemas/workflows/*.yaml` | `models/workflow_definition.py` |
| Planner 如何生成研究方案 | `modules/research_start_planner.py` | `models/research_planner_semantic.py` |
| ResearchDesign 如何形成 | `modules/research_design_service.py` | `modules/research_design_compiler.py` |

ResearchDesign 当前保存于：

```text
openair_tasks.payload.researchDesignVersions[]
```

### B. 执行决策

| 我要处理什么 | 第一入口 | 继续看 |
| --- | --- | --- |
| 系统如何决定下一步执行什么 | `modules/research_orchestration_resolver.py` | `modules/research_execution_decision_service.py` |
| Agent / Skill 如何被选择 | `modules/research_orchestration_resolver.py` | Agent / Skill Registry |
| ExecutionDecision 如何形成 | `modules/research_execution_decision_service.py` | `models/research_orchestration.py` |
| WorkUnit 如何形成 | `modules/research_execution_decision_service.py` | `models/research_orchestration.py` |

主要数据：

```text
openair_tasks.payload.executionDecisions[]
openair_tasks.payload.workUnits[]
```

Agent 定义：

```text
schemas/agents/agent-registry.yaml
```

Skill 定义：

```text
skills/registry.yaml
```

### C. 任务执行

| 我要处理什么 | 第一入口 | 继续看 |
| --- | --- | --- |
| WorkUnit 如何进入执行 | `modules/research_dispatch_service.py` | `execution/research_dispatch_provider.py` |
| Agent / Skill 如何实际运行 | `execution/research_dispatch_provider.py` | `execution/research_dispatch_worker.py` |
| Worker 如何执行任务 | `execution/research_dispatch_worker.py` | `worker/entrypoint.py` |
| claim / lease / 提交 / 恢复 | `modules/research_dispatch_service.py` | `openair_dispatch` |

当前主要执行记录保存于：

```text
openair_dispatch
```

Agent 与直接 Skill 已共享主要执行链，但旧 execution 路径尚未完全退出。

### D. 结果与完成

| 我要处理什么 | 第一入口 | 继续看 |
| --- | --- | --- |
| Artifact 如何保存 | `modules/artifact_repository.py` | `openair_artifacts` |
| ScientificState 如何形成 | `modules/research_finalization.py` | `models/research_orchestration.py` |
| 系统如何判断研究是否完成 | `modules/scientific_completion_integrity.py` | `modules/research_finalization.py` |
| FinalResult 如何形成 | `modules/research_finalization.py` | `modules/scientific_completion_integrity.py` |

主要数据：

```text
ScientificState
→ openair_tasks.payload.scientificStates[]

CompletionDecision
→ openair_tasks.payload.completionDecisions[]

FinalResult
→ openair_tasks.payload.finalResults[]

Artifact
→ openair_tasks.payload.artifacts[]
→ openair_artifacts
```

### E. 研究协作区

| 我要处理什么 | 第一入口 | 当前情况 |
| --- | --- | --- |
| 当前协作区接口 | `routes/collaboration.py` | 旧即时投影 |
| 当前协作内容如何生成 | `modules/canonical_collaboration.py` | 从已有科研数据生成 |
| 研究生命周期如何投影 | `modules/collaboration_lifecycle.py` | 旧实现 |
| 新 Research Collaboration | — | 尚未进入正式代码基线 |

Research Collaboration 是展示和协作层。

它读取已有正式科研事实，不成为新的科研状态源。

截至 `origin/dev @ 31b5e32e`，新的 Research Collaboration 架构尚未正式落位。

---

## 3. 当前尚未完整落位

| 领域 | 当前缺口 |
| --- | --- |
| 核心对象 | ResearchProject 尚无独立正式实体 |
| 核心对象 | Result 尚无独立正式实体 |
| Workflow | 已有 WorkflowDefinition，但没有独立运行实例 |
| Evidence | 独立实现目前主要覆盖外部执行和恢复场景 |
| 执行 | Shared Execution Runtime 尚未完全统一 |
| 研究协作区 | 新 Research Collaboration 尚未进入正式代码基线 |

这些项目表示：

> 架构已经明确，但当前正式代码还没有完全达到目标结构。

本地图只记录正式代码基线。

正在其他工作树施工、尚未合入的代码，不计为正式实现。
