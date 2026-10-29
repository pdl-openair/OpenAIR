# 经典软工 → 代码化资产映射（classic-foundations）

> 3.0 底座：把经典软工知识对位到本仓自造词汇与代码化资产，防"重发明继续发生"。
> 每条映射必须同时有"本仓对应物"与"经典出处"；后续工具演进在 PR 中同步本表。

## 职责

维护经典知识 ↔ 本仓词汇 ↔ 强制资产的总映射表，及"明确不引入"清单与理由。

## 适用场景

新造一个机制/词汇前先查本表（大概率已有经典对应）；写"接驳注记"（B6 批次落盘到收编文档）时定位出处。

## 映射总表

| 经典知识 | 本仓自造词汇（已有） | 代码化资产（锚点） | 强制层 | 经典出处接驳 |
|---|---|---|---|---|
| 函数体量/圈复杂度 | 02 号"函数变长先查职责边界"（只有信号无强制） | [coding-standards.md](coding-standards.md) SPEC-CODE-02（C901≤15 / PLR0915≤100，增量） | L1 | 《代码整洁之道》函数章节；golangci funlen/cyclop 的 Python 对应 |
| 测试金字塔/分层测试 | 测试三部曲"五级证明责任"（成文，机器强制由 testing-gates 登记） | B4 的 testing-gates + diff-coverage 下限 + unit-test CI job | L1 | 测试金字塔↔证明责任层互注；diff-coverage 为 Codecov 阈值思想的本地实现 |
| 测试替身分类学 | 三部曲 Fixture/Fake/Replay/Dataset/Live | 评审 checklist 项（语义判断，不强制） | L4 | Meszaros《xUnit Test Patterns》替身谱系 |
| DoR/DoD 阶段门禁 | 四 Gate A-D（任务粒度） | issue `status/verified` 关联（DoR）+ PR 证据合同（DoD） | L2 | RUP/CMMI phase-gate；范例状态机的 GitHub 原生转译 |
| 四眼原则/职责分离 | 五角色分权、施工者/评估者分离 | 作者≠评审（verdict 合同）+ CODEOWNERS + 风险分级 | L2/L4 | 审计四眼原则 |
| 原子提交 | 提交规范 8 类型 + Co-authored-by（[../collaboration/git_commit_rules.md](../collaboration/git_commit_rules.md)） | hook_guard 禁 `git add -A`/`--no-verify` + check_commit_msg | L0+L1 | Conventional Commits；范例提交纪律 |
| 纵深防御 | secret 卫生散见 | [security.md](security.md) 三层防线 | L0+L1 | 范例三层防线原样采纳 |
| DRY | —（防幻觉正则双抄实证） | 首轮清理 R2 抽取共享模块 + 评审 checklist"受控重复须注记"；pylint R0801 夜间报告（不阻断） | L4+夜间报告 | 《程序员修炼之道》DRY；PT/TS 契约镜像属"受控重复"，注记豁免 |
| WIP 限制/精益 | WIP=1 + 第一真实阻塞 | 施工组织规则（批次任务规格字段）+ 99 号机制 | 流程 | Kanban/Lean；不强行工具化（人数少，工具化成本>收益，理由登记） |
| Flaky/已知失败治理 | 三部曲三态结果、非确定性预登记 | 文档层保留；pytest marker 基础设施随 unit-test job 固化 | L1（marker）/L4（治理） | xfail 治理经典实践 |
| 语义化版本 | —（阶段制） | **不引入** | — | 见下"明确不引入" |
| CODEOWNERS | — | **最小引入**：全仓 Lynn-1221、Maxine-1520；高风险路径（auth/schema/skills registry）Maxine-1520、nipeone | L2 | GitHub 原生 + 变更管理 CAB 思想 |
| 重构节奏 | "有计划无节奏"（历史病灶） | [../governance/debt-ledger.md](../governance/debt-ledger.md) + placement 棘轮 + 常态化清理槽位 | L1/L2 | Fowler《重构》小步安全重构；绞杀者模式↔legacy/canonical 双轨 |
| 可复现构建 | devstack 脚本 + uv.lock | uv.lock/pnpm-lock 既有 + CI `--frozen-lockfile` 校验保持 | L1 | Windows 无 make 故用 python 脚本 + npm scripts 等价物 |
| 回顾制度化 | 99 号"正文稳定/来源积累" | [../collaboration/99_经验来源.md](../collaboration/99_经验来源.md) 保留；spec 修订走 PR+评审 | L3 | Retrospective 经典实践；**本仓反向输出项**（范例无此机制） |

## 明确不引入（理由登记）

- **semver**：内部产品无外部按版本消费方；合同版本已由 payload v1.2（schemas/ 合同版本）承担。未来开放 API 时再议。
- **devcontainer**：现有 setup + uv + LOCAL_DEVELOPMENT 已可用，容器化是独立工程。
- **`.loop/` 式执行层**：本仓验证账本 + 99 号机制已承担等价职能，重复建设无增益。
- **全仓 mypy strict**：scope 棘轮替代（增量收窄，不一次性冻结全仓）。

## 接驳注记总表（B6 已落盘，2026-09-24）

为何接驳：防"以原生词汇零文献引用地重发明"；**出处只作锚不作权威**——接驳≠照搬，每条保留一句话差异，两者不一致时以本仓正文为准。

设计 §6 清单中"金字塔/替身谱系/xfail 三处"原记于同一文档名下，收编后的 `spec/testing/` 中分属三份文档；按任务规格 B6 §2.1 注按内容定位，映射见下表。

| # | 本仓条目（落点） | ≈ 经典概念 | 出处 | 差异一句话 |
|---|---|---|---|---|
| 1 | [六类测试责任与 minimum_owner 分层](../testing/OpenAIR%20测试架构.md) | ≈ 测试金字塔/分层测试 | Cohn《Succeeding with Agile》；Fowler TestPyramid | 按失败风险的最低证明责任分层，不按成本比例规定各层数量 |
| 2 | [测试材料五分类](../testing/OpenAIR%20测试执行基座（Harness）设计.md) | ≈ 测试替身分类学 | Meszaros《xUnit Test Patterns》 | 按运行语义分五类并要求身份/版本可追溯，不按验证方式（stub/mock）分类 |
| 3 | [预期失败标记与三态结论](../testing/OpenAIR%20测试设计合同.md) | ≈ 已知失败治理（xfail） | pytest xfail 与 flaky 治理经典实践 | 标记只登记缺口管理状态、不吞失败证据；三态多出"未形成有效证据" |
| 4 | [WIP=1 与不扩大 WIP](../collaboration/09_工程施工与脚本交付规范.md) | ≈ WIP 限制/精益 | Kanban WIP limit；Lean | 单总控单当前任务的施工纪律，不引入看板列限额工具 |
| 5 | [验证账本（一次证据关闭）](../collaboration/09_工程施工与脚本交付规范.md) | ≈ phase-gate/质量记录 | RUP/CMMI 阶段门禁 | 三类最小事实+按风险去重，非逐阶段审批签署文档 |
| 6 | [写入前复核冻结基线](../collaboration/09_工程施工与脚本交付规范.md) | ≈ 单一写入者原则 | Single Writer Principle（LMAX Disruptor） | 靠"合同锁范围+写入前复核"两道检查，不靠锁原语 |
| 7 | [复杂度信号先查职责](../collaboration/02_工程实现与施工守则.md) | ≈ 函数短小/圈复杂度 | 《代码整洁之道》函数章；McCabe 1976 | 文档只给信号，机判阈值在 SPEC-CODE-02（C901≤15/PLR0915≤100） |
| 8 | [新路径接管后旧路径退出](../collaboration/02_工程实现与施工守则.md) | ≈ 绞杀者模式 | Fowler《Strangler Fig Application》2004 | 接管完成后旧路径立即退出，不允许新旧长期并存 |
| 9 | [五角色协作责任](../collaboration/03_任务推进与交接.md) | ≈ 四眼原则/职责分离 | 审计 four-eyes principle；SoD | 五角色分权（含执行工具），把双人复核升级为职责与越权边界 |
| 10 | [Before/After 判断变好](../collaboration/07_评测驱动迭代.md) | ≈ 对照实验 | 受控实验设计/A-B 测试 | 同场景自身对照且成本与下游可用性同权，非随机分组统计 |
| 11 | [先产品后治理的债清单](../collaboration/OpenAIR%20多条线并行施工统一要求.md) | ≈ 技术债台账 | Cunningham technical debt 隐喻 | 固化为 `spec/governance/debt-ledger.md` 登记制，每条带复审日期 |
| 12 | [提交格式与 AI 署名](../collaboration/git_commit_rules.md) | ≈ Conventional Commits | conventionalcommits.org | type/scope 为自家裁剪清单，另强制 AI 参与 Co-authored-by |

每条注记的原文落点行（插在对应条目/章节之后、不改原句）见各文档内 `[经典注记]` 行；总表与原文注记同 PR 维护。

## 防什么失效模式

- "重发明"：新机制不带出处、与既有词汇平行造新词 → 本表为查询入口。
- 映射表腐烂（工具演进后对应物消失）→ 指针完整性检查保锚点；接驳注记随 PR 同步。

## 同步点

- 新增映射 → 同时更新 enforcement-matrix 对应行与接驳注记总表。

## 不负责什么

- 收编文档正文本身（注记落原文档，B6）→ `../collaboration/`、`../testing/`
