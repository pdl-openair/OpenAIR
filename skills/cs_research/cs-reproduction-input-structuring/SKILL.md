---
name: cs-reproduction-input-structuring
description: 从与已确认 ResearchDesign 绑定的用户原文及修订中结构化论文复现下一步执行所需的明确代码仓库与 revision；缺失事实保持未知，不猜默认分支、最新版本或论文隐含参数。
---

# 论文复现执行输入结构化

## 输入

- `research_goal`：能力 Binding 从 `canonical_user_query` 映射的输入，包含与已确认 ResearchDesign 绑定的用户原文及已纳入该方案的用户修订；系统按来源顺序冻结并原样提供。字段名不表示 Planner 生成的目标摘要。

## 执行规则

1. 不重新定义研究目标、成功标准或项目完成要求；这些继续由 ResearchDesign 管理。
2. 只有冻结的用户原文及修订中明确包含代码仓库时才填写 `repository`；没有则返回 `null`。
3. 只有冻结的用户原文及修订中明确包含 branch、tag、commit 或 revision 时才填写 `revision`；没有则返回 `null`。
4. 将缺失的必要执行输入写入 `unresolved_inputs`。

## 边界

- 不进行网络检索。
- 不下载代码。
- 不创建环境。
- 不执行实验。
- 不把 `main`、`master`、`latest` 或当前默认分支补成 revision。
- 可以理解用户原文及修订中的自然语言和修订含义，但不能把未明确提供的信息改写成已确认事实。

## 输出

返回满足正式输出 Schema 的 JSON 对象。来源引用、方案版本、Artifact 身份和当前迭代关联由代码负责，不要求模型生成或猜测这些确定性关系。只有通过来源核验后的 `reproduction_repository`、`reproduction_revision` 与执行输入缺口进入后续正式状态；ResearchDesign 仍是研究目标和项目完成要求的唯一 Authority。
