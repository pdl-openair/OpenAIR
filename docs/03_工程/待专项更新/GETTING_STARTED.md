# OpenAIR 开发入门

> 状态：已确认  
> 更新时间：2026-08-28  
> 用途：帮助第一次进入 OpenAIR 仓库的开发者快速找到正确文档、代码入口和当前任务。

## 1. 第一次来，先做这 3 件事

### ① 先理解研究主链路

```text
科研问题
→ ResearchDesign
→ ExecutionDecision
→ Agent / Skill 执行
→ ScientificState / Artifact
→ CompletionDecision
→ FinalResult
```

不需要先理解全部实现细节。

### ② 找到当前任务对应的正式文档

先确定你正在处理：

- 核心研究流程；
- 某个领域 Workflow；
- Agent / Skill；
- 工程架构；
- 具体施工任务。

再进入对应文档，不要从旧代码反推设计。

### ③ 用 Code Map 找代码入口

不知道代码在哪里时，先看：

```text
docs/03_工程/工程架构/OpenAIR_Architecture-Code_Map.md
```

它负责回答：

> 这项架构能力当前由哪段正式代码实现？

---

## 2. 按你的目的找文档

| 你要了解什么 | 看这里 |
| --- | --- |
| 一项研究从开始到结束怎么走 | `docs/01_核心设计/研究生命周期与对象/OpenAIR 研究项目生命周期.md` |
| 系统如何持续推进研究 | `docs/01_核心设计/研究执行与能力/OpenAIR 研究执行机制.md` |
| Agent / Skill 怎么分工 | `docs/01_核心设计/研究执行与能力/OpenAIR Agent 与 Skill 能力体系.md` |
| ResearchDesign、ScientificState 等对象是什么 | `docs/01_核心设计/研究生命周期与对象/OpenAIR 核心对象与数据模型.md` |
| 查统一术语 | `docs/01_核心设计/OpenAIR 术语表.md` |
| 某个领域如何研究 | `docs/02_产品与Workflow/` |
| 架构能力对应哪段代码 | `docs/03_工程/工程架构/OpenAIR_Architecture-Code_Map.md` |
| 当前具体开发任务怎么做 | `docs/task_specs/` |
| 开发协作和提交规则 | `docs/04_开发协作/` |
| 项目内开发 Skill | `docs/dev_skills/` |

完整文档目录见：

```text
docs/README.md
```

---

## 3. 接到开发任务后

按这个顺序开始：

```text
当前任务
→ 找对应 Source Truth
→ 找当前 task spec
→ 用 Code Map 定位代码
→ 确认修改边界
→ 开发
→ 测试
→ 验收
```

判断优先级：

```text
最新人工确认的长期 Source Truth
>
当前正式施工合同
>
当前真实代码
>
旧设计、旧 task spec、历史实现
```

如果当前代码与 Source Truth 不一致：

> 先确认这是尚未完成的架构迁移，还是代码已经偏离正式设计。

不要为了兼容旧实现继续叠加补丁。

---

## 4. 仓库目录速查

| 目录 | 内容 |
| --- | --- |
| `apps/web/` | Web 前端 |
| `services/api/` | API、研究编排和执行后端 |
| `packages/` | 共享类型和公共契约 |
| `skills/` | 正式 Skill |
| `schemas/` | Workflow、Agent 等正式定义 |
| `docs/` | 长期设计、工程文档和当前施工合同 |

本地环境、依赖安装和启动方式见：

```text
docs/03_工程/待专项更新/LOCAL_DEVELOPMENT.md
```
