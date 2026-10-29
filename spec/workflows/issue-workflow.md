# Issue 工作流（issue-workflow）——位置 × 高度两轴模型

> 本仓词汇与状态机不是二选一，是正交两轴：**位置轴**（流程在哪）× **高度轴**（证据到哪级）。不造新词。

## 职责

定义 issue 状态机、验证六层级、四 Gate 映射与飞书双轨映射。

## 适用场景

建/推进 issue；判断任务能否开工（DoR）；PR 关联 issue；填写验证层级声明；双轨状态同步。

## 位置轴：GitHub 原生标签（issue）

`status/verified → status/in-progress → status/ready-for-review`（最小集 3+1：另设 `no-issue-required` 逃生门，打标签即审计痕）。

PR 侧不另造标签机制：`draft → self-checked → ready-for-review → approved` 以**证据合同字段完整性**判定（分支保护 + check 承载，见 [pr-workflow.md](pr-workflow.md)）。

## 高度轴：验证六层级（本仓特色词汇，保留）

| 级 | 词汇 |
|---|---|
| L1 | 已分析 |
| L2 | 已自测 |
| L3 | 单测通过 |
| L4 | 集成验证 |
| L5 | 预发验证 |
| L6 | 真实 E2E 已验证 |

声明规则：PR 证据合同必填"本 PR 的验证层级"（一行 `验证层级: L<1-6>`）；**低层级不得冒充高层级**——"测试通过"不等于"完成"。

## 四 Gate 映射表

| Gate | 含义 | 映射 |
|---|---|---|
| A（施工前） | 任务可开工 | issue 带 `status/verified` + task spec 存在（`docs/task_specs/`） |
| B（Apply 交付前） | 变更已应用 | PR self-checked 的证据字段（改了什么/影响面） |
| C（Verify 交付前） | 已验证 | 证据字段"怎么验证"含层级声明 + CI 链接 |
| D（集成/真实运行） | 合入后成立 | merge 后验证与夜间回归（夜间流水线重建进债务账本） |

## 必须遵守的硬门禁

- **[SPEC-PROC-02｜L2｜CI issue-link]** `fix` 类提交且触产品代码的 PR **必须**关联带 `status/verified` 标签的 issue（Reproduce-Before-Fix：先复现再修，防重复修复）。feat/refactor/chore/docs PR **不受此门禁**（不阻碍快速开发）；逃生门 `no-issue-required`。扩围走 enforcement-matrix 一行 PR。
- Issue 只跟踪状态/负责人/优先级/验收结果；长上下文放 task spec 或对应 docs。
- 复杂任务（多模块/多文件/AI coding/Skill 接入）先补 task spec 再开工。

## 飞书双轨映射列

| GitHub 侧 | 飞书底表侧 |
|---|---|
| issue open + `status/verified` | 待开始 |
| `status/in-progress` | 进行中 |
| PR 关联（Ref） | 待核查 |
| `status/ready-for-review` + PR ready-for-review | 待核查 |
| PR merged + issue closed | 已完成 |

双轨平台（GitHub + 飞书多维表格）是实时状态源；夜间自动回写随流水线重建恢复（债务账本），重建前人工同步为准。详细映射文档：`docs/dev_skills/github-feishu-mapping.md`。

## 防什么失效模式

- 看到 bug 直接写码、不复现不验证/重复修复 → SPEC-PROC-02 硬门禁（fix 类限定）。
- "验证通过"被说成"完成"（层级失真）→ 证据合同强制层级声明 + L4 评审抽查真实性（脚本只保证记录存在）。
- 标签越多维护成本越高 → 首版最小集 3+1，拒绝照搬范例全状态集。

## 同步点

- 标签集或映射变化 → 本文件 + enforcement-matrix SPEC-PROC-02 行 + 飞书映射文档同步。

## 不负责什么

- PR 证据字段全清单 → [pr-workflow.md](pr-workflow.md)
- task spec 模板 → `docs/task_specs/TEMPLATE.md`
