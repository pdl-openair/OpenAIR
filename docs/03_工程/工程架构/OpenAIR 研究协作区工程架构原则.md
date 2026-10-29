# OpenAIR 研究协作区工程架构原则

> 状态：已确认  
> 更新时间：2026-08-28  
> 产品基线：`OpenAIR_研究协作区设计说明_定稿_20260828_补齐.md`  
> 职责：定义研究协作区长期稳定的工程边界。本文档不包含本轮施工步骤、迁移脚本或删除清单。

---

## 1. 单一协作流

每个研究项目只有一条 Research Collaboration 流。

```text
初始 query
→ ResearchDesign
→ 研究执行
→ 用户持续参与
→ 阶段结果 / Artifact
→ Final Result
```

初始 query 是第一条用户消息。用户提交后立即进入工作台，不等待 ResearchDesign 完成。

ResearchDesign 修改、重新确认、执行、受阻、恢复、Artifact 和 Final Result 都进入同一条流，不创建第二套对话或临时历史。

---

## 2. Research Collaboration 的性质

Research Collaboration 是**展示投影**，不是新的科研状态源。

正式科研事实仍由现有对象负责：

- ResearchDesign
- ExecutionDecision
- WorkUnit
- Result
- Evidence
- ScientificState
- Artifact
- CompletionDecision / Final Result
- 用户真实输入及后端命令记录

研究活动只是展示组织方式。

一个 ExecutionDecision 可以对应一段持续研究活动，并产生多次真实进展。

WorkUnit 只用于追溯，不直接决定研究活动边界。

---

## 3. 前后端职责

### 后端负责

- 判断发生了什么；
- 判断内容类型和研究活动归属；
- 判断当前科研状态；
- 记录用户输入实际产生的影响；
- 判断 Action Required；
- 判断当前允许的操作；
- 关联 Artifact / Final Result；
- 提供稳定 ID、时间和顺序；
- 保存并恢复完整协作历史。

### 前端负责

- 请求协作投影；
- 按后端类型渲染；
- 展开、折叠、滚动；
- 提交自然语言输入；
- 调用后端明确允许的操作；
- 打开中央正式内容；
- 在收到实时变化通知后读取最新投影。

### 前端禁止

- 根据 WorkUnit 创建研究活动；
- 根据 Result、SSE event、字符串或正则推断科研语义；
- 自行解释“继续 / 确认 / 重试”等业务意图；
- 根据标题或文案判断是否阻塞；
- 拼接 ResearchDesign、Action Required、Artifact 等业务语义；
- 用 React 本地状态、localStorage、GET、SSE 维护第二套科研事实；
- 创建独立 ResearchDesign 编辑对话；
- 永久截断历史。

---

## 4. 协作投影合同

每条协作记录至少包含：

```text
id
taskId
sequence
kind
occurredAt
activityId?
title?
summary?
status?
sourceRef
```

`sourceRef` 必须能够回到真实来源。

主要 `kind`：

```text
user_message
activity_started
activity_progress
activity_completed
stage_result
action_required
action_resolved
artifact_ready
final_result_ready
history_notice
```

规则：

- `activityId` 只是展示标识，不是新的科研对象；
- 同一研究活动的多次进展共享 `activityId`；
- WorkUnit ID 只能作为追溯信息；
- 记录按后端 `sequence` 排序；
- 刷新、重连和分页不得改变已有记录；
- `history_notice` 只用于无法恢复的旧历史缺口。

---

## 5. 实时更新与历史

SSE 只是变化通知机制。

```text
正式科研事实变化
→ 更新 Research Collaboration 投影
→ SSE 通知有新 sequence
→ 前端读取新增记录
```

SSE 不保存协作历史，不决定科研状态，不生成用户可见业务语义。

完整历史必须可恢复。

允许：

- cursor 分页；
- 虚拟滚动；
- 折叠；
- 摘要预览；
- 按需加载。

禁止：

- “只保留最近 50 条”；
- “只保存 7 天”；
- 其他会永久丢失协作历史的工程限制。

---

## 6. 代码结构原则

后端研究协作能力独立成模块：

```text
research_collaboration/
├── contracts
├── repository
├── recorder
├── service
└── projectors
```

职责：

- `contracts`：公共合同；
- `repository`：投影记录的存取；
- `recorder`：正式变化进入投影的唯一写入口；
- `projectors`：单一正式来源转换为协作记录；
- `service`：统一读取、分页、待处理事项和允许操作。

前端独立成 feature：

```text
research-collaboration/
├── api
├── types
├── useResearchCollaboration
├── ResearchCollaborationPane
├── ResearchCollaborationTimeline
├── ResearchCollaborationComposer
└── items
```

前端 feature 不直接拼 ResearchDesign、Result、WorkUnit、Artifact 等多个业务数据源。

---

## 7. 长期工程规则

- 一个模块一个主要职责；
- route 不拼业务语义；
- repository 不判断科研语义；
- projector 可独立测试；
- React 组件不理解科研状态机；
- 不使用前端业务正则；
- 不保留 legacy / compat / shim 双轨；
- 不为 Demo 写死科研逻辑；
- 不新增千行级组件或服务；
- 大型核心文件不继续吸收研究协作区职责。
