# 门禁与质量分界（code-quality-gates）

> 五层强制模型 + 自动检查 vs 独立评审的诚实分界。本文件是"要不要加门禁、门禁管到哪"的裁决依据。

## 职责

定义强制层级（L0-L4）、任务四 Gate（A-D）与"机器判什么、人判什么"的边界及元规则。

## 适用场景

新增/修订任何自动检查前；评审中判断"这个该机器拦还是人审"；声称"验证通过/完成"前。

## 层级定义（五层强制）

| 层 | 名称 | 载体 | 性质 |
|---|---|---|---|
| L4 | 人工/独立评审兜底 | 风险分级 review、CODEOWNERS、四眼原则 | 高险路径必人审 |
| L3 | 上下文注入（软） | AGENTS.md 入口、dev skills、Claude hooks 本地加速 | 只影响行为不拦截 |
| L2 | 流程状态机门禁 | PR 证据合同脚本校验、issue `status/verified` 关联、分支保护 | GitHub 原生机制 |
| L1 | 仓库 CI 硬门禁 | ruff/mypy/tsc/eslint 增量、placement guard、secret 扫描、指针完整性、单测+diff-coverage、门禁 canary | **唯一可靠权威** |
| L0 | 本地工具拦截（软镜像） | pre-commit（Windows 兼容）+ Claude hooks——与 L1 同一实现 | 加速，非权威 |

关键立场：多 IDE 并行 ⇒ L0/L3 只是加速层；本地不装钩子不能绕过任何规则，因为权威在 L1+L2。每条规则显式标注所在层（见 `../enforcement/enforcement-matrix.md`）。

## 任务四 Gate（A-D）

| Gate | 含义 | 机判承载 |
|---|---|---|
| A（施工前） | 任务可开工：issue `status/verified` + task spec 存在 | L2 issue-link 检查（fix 类限定） |
| B/C（Apply/Verify 交付前） | PR 自检证据字段完整 | L2 PR 证据合同（见 [../workflows/pr-workflow.md](../workflows/pr-workflow.md)） |
| D（集成/真实运行） | merge 后验证与夜间回归 | L1 CI + 夜间流水线（重建进债务账本） |

两轴模型（位置×高度）与飞书映射详见 [../workflows/issue-workflow.md](../workflows/issue-workflow.md)。

**[SPEC-GATE-01｜L1｜canary]** 每个门禁必须通过 canary：带病样本必红、干净样本必绿；canary 常驻 CI job（B5 接线）。防"配置存在≠有效"（verifier 空转前科）。

## 诚实分界

### 适合自动检查（机器判退出码）

语法/格式/复杂度阈值、类型、密钥扫描、构建、测试通过性、覆盖率下限、模板字段完备性、标签与关联完备性、指针可解析性、placement 数值判定。

### 必须由独立执行主体（人或对抗 reviewer agent）审查

机器判不了语义，以下事项自动检查**只能保证"记录存在"，不保证"内容为真"**：

- 领域语义正确性（研究生命周期状态机、durable 语义是否被改错）；
- 测试是否真的行使生产责任（替身有没有变成被测系统——替身分类学见 `../testing/`）；
- 错误信息与 API 契约设计质量；
- 是否引入重复实现（受控重复 vs 意外重复的判定）；
- AI 生成代码是否"只是看起来合理"。

### 元规则（规则修订的显式约束）

1. **新增自动规则必须在 enforcement-matrix 登记失效模式与预估误报成本**；不配强制的规则必须写明理由（SPEC-KNOW-01 为范式）。
2. **"更严 ≠ 更对"**（09 号战时教训）：加严阈值或扩围必须给出失效模式证据，不接受"严一点总没错"。
3. 加规则走 PR，同样过指针检查与评审；规则与配置路径的引用纳入指针完整性（防投影悄悄失联）。

## 防什么失效模式

- 规则写了没工具（范例仓"函数≤50 行"无 linter 形同虚设）→ 登记制 + 门禁 canary 只对"声称有强制"的规则断言。
- 强制层被静默绕过（本地不装钩子）→ L1 为权威，CI 不可跳过（分支保护）。
- 人工评审形同虚设 → 风险分级让"必须人审"范围显式且最小（全人审=没人审）。
- 门禁自身腐烂（配置存在但失效/空转）→ canary 常驻 CI：带病样本必红、干净样本必绿。

## 同步点

- 新增门禁 → enforcement-matrix 登记 + canary 补样本（`scripts/gates/` 下 canary 目录，B5 接线）。
- 层级职责变动 → `../governance/ai-collaboration.md`（hooks/pre-commit/CI 分工）与 AGENTS.md 指路同步。

## 不负责什么

- 具体编码阈值 → [coding-standards.md](coding-standards.md)
- PR 字段清单 → [../workflows/pr-workflow.md](../workflows/pr-workflow.md)
- 测试域门禁登记 → B4 的 testing-gates（`testing/` 引用的四部曲条目强制状态）
