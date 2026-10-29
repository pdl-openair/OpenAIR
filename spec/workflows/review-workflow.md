# 评审工作流（review-workflow）——风险分级评审策略

> 风险分级驱动："必须人审"的范围显式且最小——**全人审=没人审**。

## 职责

定义变更风险三级、各级评审动作映射与语义审查清单。

## 适用场景

PR 发起时自评风险分级；reviewer 决定审查深度；CI/分支保护按级别要求评审。

## 风险分级（classify_change_risk 脚本可判，B3 交付）

| 级 | 判定（任一命中即该级） | 评审动作 |
|---|---|---|
| **high** | auth / token / schema 契约 / skills registry / 删除类变更 / `spec/enforcement/` 配置 | **必人审**（CODEOWNERS：Maxine-1520、nipeone）+ 对抗 reviewer；分支保护 require review |
| **medium** | `services/`、`apps/` 业务逻辑变更 | 至少 1 名评审人（人或对抗 reviewer agent），verdict 记录 |
| **low** | docs / spec / tests / scripts 变更 | 轻量评审；可由对抗 reviewer agent 承载，人抽查 |

**[SPEC-PROC-04｜L2/L4]** high 必人审；双 AI 闭环不得放行 high 风险变更。

## 语义审查清单（机器判不了，评审必看）

- 领域语义正确性：研究生命周期状态机、durable 语义是否被改错；
- 测试是否真的行使生产责任（替身有没有变成被测系统）；
- 错误信息与 API 契约设计质量；
- 是否引入重复实现（受控重复须注记）；
- AI 生成代码是否"只是看起来合理"；
- 是否违反 `spec/collaboration/`、`spec/testing/` 对应条目（如 09 号硬规则、验收清单）。

## 对抗 reviewer（施工者/评估者分离）

- 独立执行主体（人或 `.claude/agents/adversarial-reviewer.md`，B3 交付），输出**结构化 verdict 三态**（**ACCEPT / REJECT / INSUFFICIENT_EVIDENCE**，必居其一、附证据，与 agent 定义及战役实践逐字一致）。GitHub UI 的 approve / request-changes 是评审人的**人工操作词汇**，可与三态并列注明（如 "verdict=ACCEPT，已 approve"），但不得混用替代三态本身。
- 自主施工战役内的合并规则（CI 绿 + verdict=ACCEPT + 风险 ≤medium，合并止于重构工作分支）见 [../governance/ai-collaboration.md](../governance/ai-collaboration.md)；产品开发的"永不自动合并"原则不变。

## 防什么失效模式

- 高危路径双 AI 闭环放行 → SPEC-PROC-04 强制 high 人审。
- 评审 checklist 形同虚设（全勾不看不查）→ 风险分级收敛注意力：high 才全清单，low 轻量。
- "看起来合理"的 AI 代码 → 语义审查清单 + 独立 reviewer。

## 同步点

- 分级判定规则变化 → classify_change_risk + enforcement-matrix SPEC-PROC-04 行 + CODEOWNERS 同步。

## 不负责什么

- PR 必填字段 → [pr-workflow.md](pr-workflow.md)
- 验收清单（任务粒度，Gate B/C 用）→ [../collaboration/04_施工验收检查.md](../collaboration/04_施工验收检查.md)
